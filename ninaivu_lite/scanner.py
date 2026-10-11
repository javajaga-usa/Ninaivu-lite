"""Finding photos and making thumbnails, in one background thread.

A scan has two passes:

1. **Walk** every folder with ``os.scandir``. A file whose size and modified
   time are unchanged is not opened again, so re-scanning an unchanged library
   takes seconds. New and changed files have their header read (date, size,
   camera) and are queued for thumbnails.
2. **Grid thumbnails** (256 px), newest first, so the top of the timeline
   fills in first. These are small and quick (about 20 ms a photo).
3. **Big tiles** (640 px), newest first, in a second pass.

Between the walk and the thumbnails, a Live Photo's still and its short
video are paired (:meth:`Scanner._pair_live`); after them, in the quiet
pass, files the same size as another are read to find copies
(:meth:`Scanner._find_copies`). Neither changes a file.

On a computer with cores to spare, new files' headers are read, thumbnails
made and faces looked for several at a time (:data:`THUMB_WORKERS`, one for
every core but one); the index is written by this thread alone.

The gallery can also ask for either directly (:meth:`Scanner.thumbnail_now`),
so whatever is on screen never waits for the queue.

A folder that cannot be reached (an unplugged drive, a sleeping NAS) is skipped
and its photos are left alone; they are not marked missing until the folder is
reachable again and they are really gone. A crash or restart mid-scan loses
nothing: the next scan carries on from what the index already holds.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import copies, dates, db, media, parallel
from .backups import AVATARS_DIR
from .dates import from_timestamp, long_path

log = logging.getLogger(__name__)

#: Folders never worth walking into.
IGNORE_DIRS = {"$RECYCLE.BIN", "System Volume Information", "@eaDir", "#recycle", ".Trash",
               ".Trashes", ".git", "node_modules", "__pycache__", ".thumbnails", ".cache"}
BATCH = 200
RESCAN_EVERY = 30 * 60
#: How soon to try again when the index could not be opened at all.
RETRY_EVERY = 60
#: Files looked at at once during a scan: headers read, thumbnails made,
#: faces looked for. Pillow and OpenCV let go of Python's lock while they
#: work, so each one more is close to another core's worth: a Raspberry Pi's
#: four cores make a first scan about three times faster, an eight-core
#: computer about seven. One core is always left for the gallery, so a one-
#: or two-core computer does them one at a time, as it always did. See
#: :func:`parallel.workers` for the memory limit.
THUMB_WORKERS = parallel.WORKERS
#: New files' headers taking this long each (seconds) are read several at a
#: time: a network drive or a hard disk, not a fast local disk.
SLOW_HEADER = 0.002
#: Thumbnails written to the index together (see :class:`Batch`).
RECORD_EVERY_ROWS = 50
RECORD_EVERY_SECONDS = 1.0
#: A Live Photo's still and short video (see Scanner._pair_live), by extension.
LIVE_STILLS = frozenset({"heic", "heif", "jpg", "jpeg"})
LIVE_CLIPS = frozenset({"mov", "mp4"})
#: An iPhone's Live Photo plays for about three seconds. A longer video
#: beside a photograph of the same name is a video of its own.
LIVE_MAX_SECONDS = 4.0
#: A walk that changed files in at most this many folders pairs Live Photos
#: in those folders only (a pair is always in one folder); more, and the
#: whole library is paired again.
PAIR_FOLDERS_AT_MOST = 200
#: When a walk began that has not finished yet (a shutdown or a power cut
#: mid-walk), in the data folder: see Scanner._walk_began.
UNFINISHED_FILE = "scan-unfinished"


class _Stopped(Exception):
    """A file being read for copies when a stop or a rescan was asked for."""


class Scanner:
    def __init__(self, data_dir: str | Path, folders: list[str]) -> None:
        self.data_dir = Path(data_dir)
        self.thumbs_dir = self.data_dir / "thumbs"
        self._data_real = os.path.realpath(str(self.data_dir))
        self.folders = list(folders)
        #: Folders inside each library folder that the last walk of them could
        #: not read: what the console counts as "could not be read" (A143).
        self._unreadable: dict[str, set[str]] = {}
        #: Look again every RESCAN_EVERY seconds by itself (the "watch" setting).
        self.auto = True
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        #: What the next look covers (see rescan).
        self._within: set[str] = set()
        self._everything = False
        #: Thumbnails being made for a request now, one at a time each.
        self._making = parallel.OneAtATime()
        # Thumbnails to make again in the background (remake_later), in order.
        self._remaking: dict[int, None] = {}
        self._remaking_lock = threading.Lock()
        self._remaker = False
        #: Live Photos paired since this scanner started (see scan_once).
        self._paired = False
        #: Folders whose files came, changed or went in the walk under way.
        self._touched: set[tuple[int, str]] = set()
        #: Library folders whose unreadable files were read again since this
        #: scanner started (see _walk_folder).
        self._reread: set[str] = set()
        #: Goes up whenever the index changes, so cached answers know they are stale.
        self.generation = 0
        self.status: dict[str, Any] = {
            "state": "idle", "found": 0, "new": 0, "added": 0, "removed": 0,
            "thumbs_left": 0, "thumbs_total": 0, "started": None,
            "unreachable": [], "unreadable": 0, "last_finished": None,
        }

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def rescan(self, within: str | None = None) -> None:
        """Look at the library again. *within* asks for one folder only (an
        import's destination, which just had files copied into it): walking
        every library folder for it kept a large library on a network drive
        walking for minutes a time, once a minute through an import, with no
        thumbnails made meanwhile. A plain rescan (the Rescan button, a folder
        added) looks at everything, and wins over any folders asked for."""
        with self._lock:
            if within is None:
                self._everything = True
            else:
                self._within.add(within)
        self._wake.set()

    def _asked(self) -> list[str] | None:
        """The folders asked for since the last look, or None for everything."""
        with self._lock:
            within, everything = sorted(self._within), self._everything
            self._within, self._everything = set(), False
        return None if everything or not within else within

    def _run(self) -> None:
        parallel.background()
        conn = None
        within: list[str] | None = None        # the first look is at everything
        try:
            while not self._stop.is_set():
                try:
                    # Opened inside the loop: an index locked or unreadable for a
                    # moment must not end scanning until the next restart.
                    if conn is None:
                        conn = db.connect(self.data_dir)
                    self.scan_once(conn, within)
                    failed = False
                except Exception:  # noqa: BLE001 — the loop must survive anything
                    log.exception("scan failed; will try again later")
                    self._set(state="idle")
                    failed = True
                # With "watch" off it waits for the Rescan button only.
                woken = self._wake.wait(RETRY_EVERY if conn is None
                                        else RESCAN_EVERY if self.auto else None)
                self._wake.clear()
                asked = self._asked()
                # The half-hourly look, and one after a failure, is at everything.
                within = asked if woken and not failed else None
        finally:
            if conn is not None:
                conn.close()

    # --- one scan --------------------------------------------------------------

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.status.update(values)

    @property
    def data_real(self) -> str:
        """The data folder, resolved: nothing in it is ever a photograph."""
        return self._data_real

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.status)

    def scan_once(self, conn: sqlite3.Connection, within: list[str] | None = None) -> None:
        """Walk the library (or only the folders *within*, see rescan), then
        make what thumbnails are missing."""
        ids = db.sync_folders(conn, self.folders)
        if not within:
            self._unreadable = {}
        self.generation += 1
        self._set(state="walking", found=0, new=0, added=0, removed=0, started=time.time(),
                  **({} if within else {"unreachable": [], "unreadable": 0}))
        unreachable = []
        started = self._walk_began(int(time.time()) - 1)
        self._gone_now: list[int] = []
        self._touched = set()
        for path, folder_id in ids.items():
            if self._stop.is_set():
                return
            if path not in self.folders:
                continue                # taken out of the library since this scan began (A148)
            under = None
            if within:
                under = [rel for rel in (spelled(path, inside(folder, path)) for folder in within)
                         if rel is not None and not self._in_data(path, rel)]
                if not under:
                    continue
            if not os.path.isdir(long_path(path)) or self._emptied(conn, folder_id, path):
                unreachable.append(path)
                continue
            self._walk_folder(conn, folder_id, path, None if "" in (under or ()) else under)
        self._carry_over(conn, started)
        if not within and not self._stop.is_set():
            # Every folder walked to the end: what was new is settled.
            try:
                (self.data_dir / UNFINISHED_FILE).unlink(missing_ok=True)
            except OSError:
                pass
        # Live Photos are paired again where files came, changed or went, and
        # in the whole library once after every start: the first start after
        # the update pairs a library indexed before it. The whole library is
        # a second on 100,000 photographs (several on a Raspberry Pi), and an
        # import asks for a walk of its folder once a minute.
        if not self._paired or len(self._touched) > PAIR_FOLDERS_AT_MOST:
            self._pair_live(conn)
            self._paired = True
        elif self._touched:
            self._pair_live(conn, self._touched)
        if not within:
            self._set(unreachable=unreachable)
        self._make_thumbnails(conn)
        if self._straighten(conn):
            self._make_thumbnails(conn)      # only the ones just turned
        self._find_copies(conn)
        self._set(state="idle", last_finished=time.time())

    def _walk_began(self, now: int) -> int:
        """When the walk that new rows are counted from began: *now*, or an
        earlier walk's start that never reached the end. A photograph moved
        in Explorer is matched with where it was by being new in the walk
        that finds it gone (see _carry_over). A walk cut short (the computer
        shut down, Stop) had already written some of the moved photographs
        at their new place, so the next walk took them as old, and they lost
        their albums, favourites and Hidden (DATA1)."""
        path = self.data_dir / UNFINISHED_FILE
        try:
            return min(now, int(path.read_text(encoding="ascii").strip()))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return now
        try:
            path.write_text(str(now), encoding="ascii")
        except OSError:
            pass
        return now

    def _walk_folder(self, conn: sqlite3.Connection, folder_id: int, root: str,
                     under: list[str] | None = None) -> None:
        """Index the files under *root*, or only under its folders *under*
        (paths relative to it): what is gone is looked for there only."""
        where, args = "folder_id = ?", [folder_id]
        if under is not None and "" not in under:
            # Only those folders' rows, read from the (folder_id, dir) index:
            # an import's walk of its folder once a minute read every row of
            # a library of 100,000. "/" + 1 is "0", so the range is the subtree.
            where += " AND (" + " OR ".join(["dir = ? OR (dir >= ? AND dir < ?)"] * len(under)) + ")"
            for u in under:
                args += [u, u + "/", u + "0"]
        # A photograph that could not be read for want of a reader this
        # computer has now (pillow-heif installed since, so an iPhone's HEIC
        # photographs were indexed as unreadable, under the day they were
        # copied and without a thumbnail) is read again, once after every
        # start, for its date, place and picture.
        readable = {ext for ext in media.LATE_READERS if media.can_read(ext)} \
            if root not in self._reread else set()
        known = {(r["dir"], r["name"]): (r["id"], r["size"], r["mtime"], r["missing"],
                                         r["error"] is not None and r["ext"] in readable)
                 for r in conn.execute(
                     "SELECT id, dir, name, ext, size, mtime, missing, error FROM assets "
                     f"WHERE {where}", args)
                 if under is None or any(r["dir"] == u or r["dir"].startswith(u + "/")
                                         for u in under)}
        seen: set[int] = set()
        failed: list[str] = []          # folders inside the root that could not be read
        pending: list[tuple] = []
        rules: dict[str, int | None] = {}

        def flush() -> None:
            if not pending:
                return
            try:
                write(pending)
            except (sqlite3.Error, OverflowError):
                # One row the index cannot hold (a value out of range) must not
                # cost the batch: the rest go in one by one, the bad one is left.
                for one in pending:
                    try:
                        write([one])
                    except (sqlite3.Error, OverflowError) as exc:
                        log.warning("could not index %s/%s: %s", one[1], one[2], exc)
                        failed.append(f"{one[1]}/{one[2]}" if one[1] else one[2])
            pending.clear()
            self.generation += 1

        def write(rows: list[tuple]) -> None:
            with conn:
                # The write lock first, then what the folder is now: a folder
                # taken out of the library (A148), or a rule set on a folder
                # (A149), since these files were looked at counts, not what
                # was true when the walk began.
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT detached_at FROM folders WHERE id = ?",
                                (folder_id,)).fetchone()[0] is not None:
                    return
                now = db.rules_of(conn, folder_id)
                rows = [(*r[:21], *_ruled(db.rule_in(now, r[1])), *r[23:]) for r in rows]
                conn.executemany(
                    """INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at,
                           date_key, date_source, width, height, camera, lens, iso, f_number,
                           exposure, focal_length, gps_lat, gps_lon, error, visibility,
                           vis_source, rot_source, upright, thumb, large, missing)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)
                       ON CONFLICT (folder_id, dir, name) DO UPDATE SET
                           ext=excluded.ext, kind=excluded.kind, size=excluded.size,
                           mtime=excluded.mtime, captured_at=excluded.captured_at,
                           date_key=excluded.date_key, date_source=excluded.date_source,
                           camera=excluded.camera,
                           lens=excluded.lens, iso=excluded.iso, f_number=excluded.f_number,
                           exposure=excluded.exposure, focal_length=excluded.focal_length,
                           gps_lat=excluded.gps_lat, gps_lon=excluded.gps_lon,
                           error=excluded.error, thumb=0, large=0, missing=0,
                           -- a changed file is another file: how long it plays and
                           -- what it holds are found out again
                           duration=0, quick_hash=NULL, content_hash=NULL,
                           -- a turn somebody set by hand outlives a changed file, and the
                           -- shape stays the shape as shown
                           width=CASE WHEN assets.rot_source='manual' AND assets.rotation % 180 != 0
                                      THEN excluded.height ELSE excluded.width END,
                           height=CASE WHEN assets.rot_source='manual' AND assets.rotation % 180 != 0
                                       THEN excluded.width ELSE excluded.height END,
                           rotation=CASE WHEN assets.rot_source='manual' THEN assets.rotation ELSE 0 END,
                           mirror=CASE WHEN assets.rot_source='manual' THEN assets.mirror ELSE 0 END,
                           rot_source=CASE WHEN assets.rot_source='manual' THEN 'manual'
                                           ELSE excluded.rot_source END,
                           upright=CASE WHEN assets.rot_source='manual' THEN 1 ELSE excluded.upright END""",
                    rows)

        def describe(item: tuple) -> tuple[dict[str, Any], float]:
            began = time.perf_counter()
            info = media.describe(item[2], item[1], item[4], item[3])
            return info, time.perf_counter() - began

        def described(todo: list[tuple]) -> None:
            # Reading a header is mostly Python's own work on a fast disk, so
            # threads only get in each other's way there; on a network drive,
            # a USB stick or a waking hard disk it is mostly waiting, and
            # several at a time is several times faster. Each batch goes the
            # way the last one says the disk needs (SLOW_HEADER).
            made = parallel.in_order(todo, describe, pool if slow["disk"] else None, workers)
            spent = 0.0
            try:
                for item, (info, took) in made:
                    spent += took
                    if self._stop.is_set():
                        return
                    add(item, info)
            finally:
                made.close()
            slow["disk"] = spent / len(todo) >= SLOW_HEADER

        def add(item: tuple, info: dict[str, Any]) -> None:
            rel_dir, name, full, st, kind, row = item
            try:
                if rel_dir not in rules:
                    rules[rel_dir] = db.rule_for(conn, folder_id, rel_dir)
            except Exception:  # noqa: BLE001 — one bad file must not stop the scan
                log.exception("could not look at %s", full)
                failed.append(f"{rel_dir}/{name}" if rel_dir else name)
                return
            rule = rules[rel_dir]
            # A camera's own orientation tag is final (the browser and the
            # thumbnails honour it); a photograph without one is looked at
            # for its faces later, in the quiet pass after the thumbnails.
            tagged = (info.get("orientation") or 1) != 1
            pending.append((folder_id, rel_dir, name, os.path.splitext(name)[1].lower().lstrip("."),
                            kind, st.st_size, st.st_mtime, info["taken_at"],
                            day_of(info["taken_at"]), info.get("taken_source"), info.get("width"),
                            info.get("height"), info.get("camera"), info.get("lens"),
                            info.get("iso"), info.get("f_number"), info.get("exposure"),
                            info.get("focal_length"), info.get("lat"), info.get("lon"),
                            info.get("error"), db.VIS_FAMILY if rule is None else rule,
                            "default" if rule is None else "rule",
                            "exif" if tagged else "none",
                            1 if tagged or kind != "picture" or info.get("error") else 0))
            if row:
                seen.add(row[0])
            else:
                self.status["added"] += 1
            self.status["new"] += 1
            self._touched.add((folder_id, rel_dir))
            if len(pending) >= BATCH:
                flush()

        workers = THUMB_WORKERS
        pool = parallel.pool(workers, "headers") if workers > 1 else None
        todo: list[tuple] = []
        slow = {"disk": False}
        try:
            for rel_dir, name, full, st in self._files(root, failed, under):
                if self._stop.is_set() or root not in self.folders:
                    break
                kind = media.kind_of(name)
                if kind is None:
                    continue
                self.status["found"] += 1
                row = known.get((rel_dir, name))
                if row and row[1] == st.st_size and abs(row[2] - st.st_mtime) < 1 \
                        and not row[4]:
                    seen.add(row[0])
                    if row[3]:
                        self._touched.add((folder_id, rel_dir))  # back: its pair may be too
                        with conn:
                            # Back after being away: a thumbnail that failed while it
                            # was away is asked for again. Never in a folder taken
                            # out of the library meanwhile (A148).
                            conn.execute("UPDATE assets SET missing = 0, "
                                         "thumb = CASE WHEN thumb = ? THEN 0 ELSE thumb END "
                                         "WHERE id = ? AND folder_id IN "
                                         "(SELECT id FROM folders WHERE detached_at IS NULL)",
                                         (db.THUMB_NONE, row[0]))
                    continue
                todo.append((rel_dir, name, full, st, kind, row))
                if len(todo) >= BATCH:
                    described(todo)
                    todo.clear()
            if todo and not self._stop.is_set():
                described(todo)
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        if root not in self.folders:
            return                      # taken out of the library meanwhile (A148)
        flush()
        if self._stop.is_set():
            return
        if under is None:
            self._reread.add(root)
        # A folder that could not be read this time (permissions, a flaky
        # disk) says nothing about the photographs in it: they keep their
        # place until a walk that can read it finds them really gone.
        # Counted per folder, so a walk of one folder (an import's) replaces
        # what was known about that folder rather than adding to it (A143).
        mine = self._unreadable.setdefault(root, set())
        if under is None:
            mine.clear()
        else:
            mine -= {rel for rel in mine
                     if any(rel == u or rel.startswith(u + "/") for u in under)}
        mine.update(failed)
        self.status["unreadable"] = sum(len(v) for v in self._unreadable.values())
        gone = [r[0] for (rel_dir, name), r in known.items()
                if r[0] not in seen and not r[3] and not under_any(rel_dir, name, failed)]
        self.status["removed"] += len(gone)
        if gone:
            gone_ids = set(gone)
            self._touched.update((folder_id, rel_dir) for (rel_dir, _name), r in known.items()
                                 if r[0] in gone_ids)
            with conn:
                conn.executemany("UPDATE assets SET missing = 1 WHERE id = ?",
                                 [(i,) for i in gone])
            self.generation += 1
            self._gone_now.extend(gone)

    def _in_data(self, root: str, rel: str) -> bool:
        """Whether *rel* in *root* is in (or is) this program's own data folder."""
        real = os.path.realpath(os.path.join(root, *rel.split("/")) if rel else root)
        return real == self._data_real or real.startswith(self._data_real.rstrip(os.sep) + os.sep)

    @staticmethod
    def _emptied(conn: sqlite3.Connection, folder_id: int, root: str) -> bool:
        """A library folder that held photographs and is now empty: a drive
        unplugged whose mount point stays behind as an empty folder, far more
        often than every photograph deleted. Treated as unreachable, so they
        are not counted as removed."""
        try:
            with os.scandir(long_path(root)) as entries:
                if next(entries, None) is not None:
                    return False
        except OSError:
            return False
        return conn.execute("SELECT 1 FROM assets WHERE folder_id = ? AND missing = 0 LIMIT 1",
                            (folder_id,)).fetchone() is not None

    def _carry_over(self, conn: sqlite3.Connection, started: int) -> None:
        """A photograph moved or renamed in Explorer is gone from one place
        and new in another in the same scan. What was decided about it goes
        with it: who may see it (a photograph Hidden on its own stays
        Hidden), favourites, album places and share links. A file matches
        when its size and moment taken are the same, and only one new file
        does."""
        gone_ids = getattr(self, "_gone_now", [])
        if not gone_ids:
            return
        new = conn.execute(
            "SELECT id, name, kind, size, captured_at, visibility FROM assets "
            "WHERE added_at >= ? AND missing = 0", (started,)).fetchall()
        if not new:
            return
        by_key: dict[tuple, list] = {}
        for row in new:
            by_key.setdefault((row["kind"], row["size"], row["captured_at"]), []).append(row)
        used: set[int] = set()
        moves = []
        for start in range(0, len(gone_ids), 500):
            chunk = gone_ids[start:start + 500]
            for old in conn.execute(
                    "SELECT id, name, kind, size, captured_at, visibility, vis_source, rotation, "
                    "mirror, rot_source FROM assets WHERE id IN (" + ",".join("?" * len(chunk)) + ")",
                    chunk):
                found = [r for r in by_key.get((old["kind"], old["size"], old["captured_at"]), [])
                         if r["id"] not in used]
                same_name = [r for r in found if r["name"] == old["name"]]
                pick = same_name if len(same_name) == 1 else found
                if len(pick) != 1:
                    continue
                used.add(pick[0]["id"])
                moves.append((old, pick[0]))
        if not moves:
            return
        with conn:
            for old, row in moves:
                to, frm = row["id"], old["id"]
                if old["vis_source"] == "item" or old["visibility"] > row["visibility"]:
                    conn.execute("UPDATE assets SET visibility = ?, vis_source = 'item' "
                                 "WHERE id = ?", (old["visibility"], to))
                conn.execute("UPDATE OR IGNORE user_assets SET asset_id = ? WHERE asset_id = ?",
                             (to, frm))
                conn.execute("UPDATE OR IGNORE album_items SET asset_id = ? WHERE asset_id = ?",
                             (to, frm))
                conn.execute("UPDATE albums SET cover_id = ? WHERE cover_id = ?", (to, frm))
                conn.execute("UPDATE shares SET target_id = ? WHERE scope = 'asset' "
                             "AND target_id = ?", (to, frm))
        for old, row in moves:
            if old["rot_source"] == "manual":
                self.set_rotation(conn, row["id"], old["rotation"], "manual", remake=False,
                                  mirror=bool(old["mirror"]))
        self._carry_avatars({old["id"]: row["id"] for old, row in moves})
        log.info("%d moved or renamed photographs kept what was set for them", len(moves))
        self.generation += 1

    def _carry_avatars(self, moved: dict[int, int]) -> None:
        """A profile picture is shown on the sign-in screen only while the
        photograph it was made from is in the library (A146), named by its
        id beside the picture (api_auth.avatar_source_file). Moved in
        Explorer, the photograph has a new id: the record follows it, or the
        picture went from the sign-in screen for good (DATA2)."""
        try:
            records = list((self.data_dir / AVATARS_DIR).glob("*.jpg.source"))
        except OSError:
            return
        for record in records:
            try:
                was = int(record.read_text(encoding="ascii").strip())
                if was in moved:
                    tmp = record.with_name(record.name + ".tmp")
                    tmp.write_text(str(moved[was]), encoding="ascii")
                    os.replace(tmp, record)
            except (OSError, ValueError):
                continue

    # --- Live Photos -------------------------------------------------------------

    def _pair_live(self, conn: sqlite3.Connection,
                   folders: set[tuple[int, str]] | None = None) -> int:
        """Pair each Live Photo's still with its short video: an iPhone keeps
        IMG_1234.HEIC (or .JPG) and IMG_1234.MOV side by side, and an export
        from Google Photos MVIMG_1234.jpg and MVIMG_1234.mp4. Same folder,
        same name but for the extension (in any case), and a video of at most
        :data:`LIVE_MAX_SECONDS` (or one whose header does not say). The
        video stays in the index and on disk, is played from the still's
        LIVE badge, and is left out of the grid and every count; a still
        gone, a video grown long or renamed, and the pair is undone.

        Motion kept inside the photograph itself (a Pixel's ``.MP.jpg``, a
        Samsung motion photo) is not taken out: it shows as the photograph.
        *folders*, (library folder id, folder inside it) pairs: only there.
        Returns how many pairs changed."""
        exts = sorted(LIVE_STILLS | LIVE_CLIPS)
        args: list[Any] = list(exts)
        only = ""
        if folders is not None:
            only = " AND (" + " OR ".join(["(a.folder_id = ? AND a.dir = ?)"] * len(folders)) + ")"
            for one in sorted(folders):
                args += one
        rows = conn.execute(
            f"""SELECT a.id, a.folder_id, a.dir, a.name, a.ext, a.kind, a.duration, a.live_of,
                       a.live_id, a.size, a.mtime, f.path AS root
                FROM assets a JOIN folders f ON f.id = a.folder_id
                WHERE a.missing = 0 AND (a.ext IN ({','.join('?' * len(exts))})
                      OR a.live_of IS NOT NULL OR a.live_id IS NOT NULL){only}""",
            args).fetchall() if folders != set() else []
        groups: dict[tuple[int, str, str], tuple[list, list]] = {}
        for row in rows:
            stem = os.path.splitext(row["name"])[0].casefold()
            if row["kind"] == "picture" and row["ext"] in LIVE_STILLS:
                # A Pixel's PXL_..._MP.jpg, should its video be beside it.
                side = 0
                stem = stem[:-3] if stem.endswith(".mp") else stem
            elif row["kind"] == "video" and row["ext"] in LIVE_CLIPS:
                side = 1
            else:
                continue
            groups.setdefault((row["folder_id"], row["dir"], stem), ([], []))[side].append(row)
        still_of: dict[int, int] = {}
        lengths: list[tuple[float, int, int, float]] = []
        for stills, clips in groups.values():
            if not stills or not clips:
                continue
            # The pair there already, else the camera's own pair (HEIC, MOV).
            clip = min(clips, key=lambda r: (r["live_of"] is None, r["ext"] != "mov", r["id"]))
            still = min(stills, key=lambda r: (r["id"] != clip["live_of"],
                                               r["ext"] not in ("heic", "heif"), r["id"]))
            # Read again for a clip already paired too: a duration of 0 is
            # one forgotten when the file changed, and a long video saved
            # over the clip is a video of its own.
            seconds = clip["duration"] or 0
            if not seconds:
                seconds = dates.container_duration(
                    full_path(clip["root"], clip["dir"], clip["name"])) or 0
                if seconds:
                    lengths.append((seconds, clip["id"], clip["size"], clip["mtime"]))
            if seconds <= LIVE_MAX_SECONDS:
                still_of[clip["id"]] = still["id"]
        clip_of = {still: clip for clip, still in still_of.items()}
        changes = [(still_of.get(r["id"]), clip_of.get(r["id"]), r["id"]) for r in rows
                   if (r["live_of"], r["live_id"]) != (still_of.get(r["id"]), clip_of.get(r["id"]))]
        before = {r["id"]: r["live_of"] for r in rows}
        joined = [(still, clip) for clip, still in still_of.items() if before.get(clip) != still]
        if joined:
            # Only the few clips somebody kept somewhere (each table read once).
            kept = {r[0] for r in conn.execute(
                "SELECT asset_id FROM album_items UNION SELECT cover_id FROM albums "
                "UNION SELECT asset_id FROM user_assets WHERE favorite = 1 "
                "UNION SELECT target_id FROM shares WHERE scope = 'asset'")}
            joined = [(still, clip) for still, clip in joined if clip in kept]
        if changes or lengths:
            with conn:
                conn.executemany("UPDATE assets SET duration = ? WHERE id = ? AND size = ? "
                                 "AND mtime = ?", lengths)
                conn.executemany("UPDATE assets SET live_of = ?, live_id = ? WHERE id = ?", changes)
                # A clip put in an album, made a favourite or shared while it
                # was a video of its own (before index version 11, or before
                # its still was there) goes on being found there: its still,
                # which plays it, takes its place. The clip's own rows are
                # kept, for when the pair is undone.
                for still, clip in joined:
                    conn.execute("INSERT OR IGNORE INTO album_items (album_id, asset_id, added_at) "
                                 "SELECT album_id, ?, added_at FROM album_items WHERE asset_id = ?",
                                 (still, clip))
                    conn.execute("UPDATE albums SET cover_id = ? WHERE cover_id = ?", (still, clip))
                    conn.execute("INSERT INTO user_assets (user_id, asset_id, favorite) "
                                 "SELECT user_id, ?, 1 FROM user_assets "
                                 "WHERE asset_id = ? AND favorite = 1 "
                                 "ON CONFLICT (user_id, asset_id) DO UPDATE SET favorite = 1",
                                 (still, clip))
                    conn.execute("UPDATE shares SET target_id = ? WHERE scope = 'asset' "
                                 "AND target_id = ?", (still, clip))
            self.generation += 1
        if changes:
            log.info("Live Photos: %d paired, %d changed", len(still_of), len(changes))
        return len(changes)

    def add_file(self, conn: sqlite3.Connection, folder_id: int, root: str, rel_dir: str,
                 name: str, visibility: int | None = None) -> int:
        """Index one new file now (a copy Sudar just saved), with its small
        thumbnail, so it is on screen before the next scan. Returns its id."""
        full = full_path(root, rel_dir, name)
        st = os.stat(long_path(full))
        kind = media.kind_of(name) or "picture"
        info = media.describe(full, name, kind, st)
        if visibility is None:
            rule = db.rule_for(conn, folder_id, rel_dir)
            visibility, source = (db.VIS_FAMILY, "default") if rule is None else (rule, "rule")
        else:
            source = "item"
        with conn:
            cur = conn.execute(
                """INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at,
                       date_key, date_source, width, height, camera, lens, iso, f_number,
                       exposure, focal_length, gps_lat, gps_lon, error, visibility, vis_source,
                       upright)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT (folder_id, dir, name) DO UPDATE SET
                       size=excluded.size, mtime=excluded.mtime, captured_at=excluded.captured_at,
                       date_key=excluded.date_key, date_source=excluded.date_source,
                       width=excluded.width, height=excluded.height, thumb=0, large=0, missing=0,
                       duration=0, quick_hash=NULL, content_hash=NULL""",
                (folder_id, rel_dir, name, os.path.splitext(name)[1].lower().lstrip("."), kind,
                 st.st_size, st.st_mtime, info["taken_at"], day_of(info["taken_at"]),
                 info.get("taken_source"), info.get("width"), info.get("height"),
                 info.get("camera"), info.get("lens"), info.get("iso"), info.get("f_number"),
                 info.get("exposure"), info.get("focal_length"), info.get("lat"), info.get("lon"),
                 info.get("error"), visibility, source))
            asset_id = int(cur.lastrowid) if cur.lastrowid else int(conn.execute(
                "SELECT id FROM assets WHERE folder_id = ? AND dir = ? AND name = ?",
                (folder_id, rel_dir, name)).fetchone()[0])
        self.generation += 1
        self.thumbnail_now(conn, asset_id, "s")
        return asset_id

    def _files(self, root: str, failed: list[str] | None = None,
               under: list[str] | None = None):
        """(relative dir, name, full path, stat) for every file under *root*
        (or under its folders *under*). What could not be read is added to
        *failed*, as paths relative to it."""
        stack = sorted(set(under), reverse=True) if under else [""]
        visited: set[tuple[int, int]] = set()
        while stack:
            rel = stack.pop()
            current = os.path.join(root, *rel.split("/")) if rel else root
            try:
                st = os.stat(long_path(current))
                key = (st.st_dev, st.st_ino)
                if key in visited and st.st_ino:
                    continue           # a link back up the tree
                visited.add(key)
                with os.scandir(long_path(current)) as entries:
                    items = list(entries)
            except OSError as exc:
                log.info("skipping %s: %s", current, exc)
                if failed is not None:
                    failed.append(rel)
                continue
            subdirs = []
            for entry in items:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        # Our own data folder, if someone put it among the photos,
                        # would otherwise show every thumbnail as a photo.
                        # Nor any folder inside it, through a link.
                        if name not in IGNORE_DIRS and not within(
                                os.path.realpath(os.path.join(current, name)), self._data_real):
                            subdirs.append(f"{rel}/{name}" if rel else name)
                    elif entry.is_file():
                        full = os.path.join(current, name)
                        if entry.is_symlink() and leads_away(full, self._data_real):
                            continue
                        yield rel, name, full, entry.stat()
                    elif entry.is_symlink():
                        # A link whose target cannot be reached (a linked folder
                        # on a drive that is unplugged) is unread, not empty.
                        entry.stat()
                        continue
                except OSError:
                    if failed is not None:
                        failed.append(f"{rel}/{name}" if rel else name)
                    continue
            stack.extend(sorted(subdirs, reverse=True))

    # --- thumbnails --------------------------------------------------------------

    _QUEUE = {
        # pass: (where, sizes, state shown)
        # A Live Photo's short video has no tile of its own: its still has.
        "small": ("a.thumb = 0 AND a.live_of IS NULL", ("s",), "thumbnails"),
        "large": ("a.thumb = 1 AND a.large = 0 AND a.kind = 'picture'", ("l",), "finishing"),
    }

    def _make_thumbnails(self, conn: sqlite3.Connection) -> None:
        workers = THUMB_WORKERS
        pool = parallel.pool(workers, "thumbnails") if workers > 1 else None
        try:
            for name in ("small", "large"):
                if not self._thumbnail_pass(conn, name, pool, workers):
                    return
        finally:
            if pool is not None:
                # At most *workers* pictures are still being made. They finish
                # on their own, unrecorded, so the next pass makes them again.
                pool.shutdown(wait=False, cancel_futures=True)

    def _thumbnail_pass(self, conn: sqlite3.Connection, name: str,
                        pool: ThreadPoolExecutor | None, workers: int) -> bool:
        """One pass of the queue; False when it stopped early for a stop or a rescan."""
        where, sizes, state = self._QUEUE[name]
        # Each video's ffmpeg gets its share of the cores the workers have.
        share = max(1, (parallel.cores() - 1) // workers) if pool is not None else 0
        total = conn.execute(
            f"SELECT COUNT(*) FROM assets a WHERE {where} AND a.missing = 0").fetchone()[0]
        self._set(state=state, thumbs_total=total, thumbs_left=total)
        rows = self._queue(conn, where, "a.id, a.kind, a.dir, a.name, a.thumb, a.rotation, a.mirror")
        # Written down as each is ready, not in date order: one slow file (a
        # damaged video ffmpeg gives 90 seconds, a 50 MP scan) no longer
        # leaves the other workers idle until it is done. And in batches,
        # not one commit each (see Batch).
        made = parallel.as_done(rows, lambda row: self._render(row, sizes, share), pool, workers)
        tried = 0
        with Batch(conn, self._record_many) as batch:
            try:
                for row, result in made:
                    tried += 1
                    if result is not None:
                        if batch.add((row, *result)):
                            # Counted down here rather than counted again: on
                            # a large library that count was a walk of the index.
                            self._set(thumbs_left=max(0, total - tried))
                            self.generation += 1
                    if self._stop.is_set() or self._wake.is_set():
                        return False  # a rescan was asked for: walk first, then carry on here
            finally:
                made.close()
        self._set(thumbs_left=max(0, total - tried))
        self.generation += 1
        return not self._stop.is_set()

    def _queue(self, conn: sqlite3.Connection, where: str, columns: str):
        """The rows matching *where*, newest first, read a page at a time on
        this thread as the workers want more.

        Each row is tried once per pass. The next page starts after the last
        row read (not at an offset): rows done leave the queue and rows that
        failed stay in it, so an offset would skip work, and starting over
        would spin at full CPU on a row whose drive is asleep. Starting after
        the last row read, not the last one written down, the next page can
        be read while this one is still being worked on, so the workers never
        wait for the slowest of a page to finish."""
        after: tuple[float, int] | None = None
        while not self._stop.is_set():
            page = "" if after is None else \
                "AND (a.captured_at < ? OR (a.captured_at = ? AND a.id < ?))"
            args = () if after is None else (after[0], after[0], after[1])
            rows = conn.execute(
                f"""SELECT {columns}, a.captured_at, f.path AS root
                    FROM assets a JOIN folders f ON f.id = a.folder_id
                    WHERE {where} AND a.missing = 0 {page}
                    ORDER BY a.captured_at DESC, a.id DESC LIMIT 50""", args).fetchall()
            if not rows:
                return
            after = (rows[-1]["captured_at"], rows[-1]["id"])
            yield from rows

    # --- which way up ------------------------------------------------------------

    def _straighten(self, conn: sqlite3.Connection) -> int:
        """Photographs without a camera tag, looked at once each for their
        faces (when OpenCV is installed) and turned in the index. Nobody is
        asked and the file is never touched; the thumbnails are remade, and
        the viewer turns the picture as it shows it. Returns how many turned."""
        if not media.FACES:
            return 0
        if conn.execute("SELECT 1 FROM assets a WHERE a.upright = 0 AND a.kind = 'picture' "
                        "AND a.missing = 0 LIMIT 1").fetchone() is None:
            return 0
        self._set(state="finishing", thumbs_total=0, thumbs_left=0)
        turned = 0
        workers = THUMB_WORKERS
        pool = parallel.pool(workers, "faces") if workers > 1 else None
        rows = self._queue(conn, "a.upright = 0 AND a.kind = 'picture'",
                           "a.id, a.dir, a.name, a.width, a.height")
        # Looked at several at a time; each answer is written here, as it comes.
        looked = parallel.as_done(
            rows, lambda row: media.detect_rotation(
                full_path(row["root"], row["dir"], row["name"])), pool, workers)
        try:
            for row, rotation in looked:
                turned += self.set_rotation(conn, row["id"], rotation,
                                            "faces" if rotation else "none", remake=False)
                if self._stop.is_set() or self._wake.is_set():
                    break
        finally:
            looked.close()
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        return turned

    # --- copies ------------------------------------------------------------------

    def _find_copies(self, conn: sqlite3.Connection) -> int:
        """The same file in two places (see :mod:`copies`): files of a size
        another file has too are quick-hashed, files whose quick hashes agree
        hashed whole. One file at a time (several reading one disk at once
        only make it seek), each answer kept, so this is done once a file.
        Stops for a stop or a rescan and carries on next time. Never writes
        to a file. Returns how many files were read."""
        read = 0
        for listing, column in ((copies.to_quick_hash, "quick_hash"),
                                (copies.to_content_hash, "content_hash")):
            after: tuple[int, int] | None = None
            while not (self._stop.is_set() or self._wake.is_set()):
                rows = listing(conn, after)
                if not rows:
                    break
                after = (rows[-1]["size"], rows[-1]["id"])
                self._set(state="finishing", thumbs_total=0, thumbs_left=0)
                found = []
                for row in rows:
                    if self._stop.is_set() or self._wake.is_set():
                        break
                    value = self._hash_one(row, column)
                    if value is not None:
                        found.append((value, row["id"], row["size"], row["mtime"]))
                if found:
                    read += len(found)
                    with conn:
                        # Not when the file changed meanwhile: the walk has
                        # forgotten its hashes, and these are the old file's.
                        conn.executemany(f"UPDATE assets SET {column} = ? WHERE id = ? "
                                         f"AND size = ? AND mtime = ?", found)
                    self.generation += 1
        return read

    def _hash_one(self, row: sqlite3.Row, column: str) -> str | None:
        """One file's quick or full hash; :data:`copies.UNREADABLE` for one
        that is there but cannot be read; None for one away (a drive asleep)
        or a read stopped for a rescan, so it is asked again next time."""
        path = full_path(row["root"], row["dir"], row["name"])
        if not os.path.isfile(long_path(path)) or leads_away(path, self._data_real):
            return None

        def check() -> None:
            if self._stop.is_set() or self._wake.is_set():
                raise _Stopped()

        try:
            if column == "quick_hash":
                return copies.quick_hash(path, row["size"])
            return copies.content_hash(path, check)
        except _Stopped:
            return None
        except PermissionError as exc:
            log.info("could not read %s for copies: %s", path, exc)
            return copies.UNREADABLE
        except OSError as exc:          # gone or away mid-read: next time
            log.info("could not read %s for copies: %s", path, exc)
            return None

    def set_rotation(self, conn: sqlite3.Connection, asset_id: int, rotation: int, source: str,
                     remake: bool = True, mirror: bool | None = None) -> bool:
        """Store a quarter turn for one photograph or video (the index's answer,
        never the file's), swapping its width and height as shown, and with
        *mirror* (None keeps it) whether it is shown mirrored. The thumbnails
        are remade now, or left for the thumbnail pass when *remake* is False.
        Returns whether the picture shows a different way than before."""
        rotation %= 360
        with conn:
            # Read under the write lock: the shape is swapped from what is
            # stored now, and a turn somebody set by hand while the faces
            # were being looked at is never replaced by the guess.
            conn.execute("BEGIN IMMEDIATE")
            row, mirror = self._store_rotation(conn, asset_id, rotation, source, mirror)
        if row is None:
            return False
        return self._turned(conn, asset_id, row, rotation, mirror, remake)

    def set_rotations(self, conn: sqlite3.Connection, turns: list[tuple[int, int]],
                      source: str) -> list[int]:
        """Store many (id, rotation) at once, as :meth:`set_rotation` without
        remaking, in one transaction: one commit each wrote about 33 KB to the
        index's journal a photograph (167 MB for a selection of 5,000, on a
        Raspberry Pi's SD card). Returns the ids that turned."""
        stored = []
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for asset_id, rotation in turns:
                row, mirror = self._store_rotation(conn, asset_id, rotation % 360, source, None)
                if row is not None:
                    stored.append((asset_id, row, rotation % 360, mirror))
        return [asset_id for asset_id, row, rotation, mirror in stored
                if self._turned(conn, asset_id, row, rotation, mirror, remake=False)]

    @staticmethod
    def _store_rotation(conn: sqlite3.Connection, asset_id: int, rotation: int, source: str,
                        mirror: bool | None) -> tuple[sqlite3.Row | None, bool]:
        """The index's part of :meth:`set_rotation`, in the caller's write
        transaction: (the row as it was, or None when it is not turned; mirror)."""
        row = conn.execute("SELECT kind, thumb, rotation, mirror, width, height, rot_source "
                           "FROM assets "
                           "WHERE id = ?", (asset_id,)).fetchone()
        if row is None or (source != "manual" and row["rot_source"] == "manual"):
            return None, False
        mirror = bool(row["mirror"]) if mirror is None else bool(mirror)
        changed = rotation != (row["rotation"] or 0) or mirror != bool(row["mirror"])
        swap = (rotation % 180) != ((row["rotation"] or 0) % 180)
        width, height = (row["height"], row["width"]) if swap else (row["width"], row["height"])
        # A new thumbnail version at once: a tile asked for before the new
        # thumbnail is made must not be answered by the browser's year-long
        # cached copy of the old one.
        conn.execute(
            "UPDATE assets SET rotation = ?, mirror = ?, rot_source = ?, upright = 1, "
            "width = ?, height = ?"
            + (", thumb = 0, large = 0, thumb_v = thumb_v + 1" if changed else "")
            + " WHERE id = ?",
            (rotation, int(mirror), source, width, height, asset_id))
        return row, mirror

    def _turned(self, conn: sqlite3.Connection, asset_id: int, row: sqlite3.Row,
                rotation: int, mirror: bool, remake: bool) -> bool:
        """After a turn is stored: the thumbnails. Whether it shows differently."""
        changed = rotation != (row["rotation"] or 0) or mirror != bool(row["mirror"])
        # A video's picture is its own thumbnail turned the new way: often the
        # only one there is (a browser's poster, without ffmpeg), which making
        # it again from the file would lose.
        poster = self._turned_poster(asset_id, row, rotation, mirror) \
            if changed and row["kind"] == "video" and row["thumb"] == db.THUMB_OK else None
        if changed:
            # The old thumbnails show the old way up: gone now, so nothing can
            # serve them before the new ones exist.
            for size in media.THUMB_SIZES:
                try:
                    media.thumb_path(self.thumbs_dir, asset_id, size).unlink()
                except OSError:
                    pass
            self.generation += 1
            try:
                colour = media.save_thumbnails(poster, self.thumbs_dir, asset_id) \
                    if poster is not None else None
            except (OSError, ValueError):
                poster = None   # made from the file instead, as for a photograph
            if poster is not None:
                with conn:
                    conn.execute("UPDATE assets SET thumb = ?, large = 1, color = ?, thumb_v = ? "
                                 "WHERE id = ? AND rotation = ? AND mirror = ?",
                                 (db.THUMB_OK, colour, int(time.time() * 1000) % 2_000_000_000,
                                  asset_id, rotation, int(mirror)))
                self.generation += 1
            elif remake:
                row = conn.execute(
                    """SELECT a.id, a.kind, a.dir, a.name, a.thumb, a.rotation, a.mirror,
                              f.path AS root
                       FROM assets a JOIN folders f ON f.id = a.folder_id WHERE a.id = ?""",
                    (asset_id,)).fetchone()
                if row is not None:
                    self._thumbnail(conn, row, ("s", "l"))
        return changed

    def _turned_poster(self, asset_id: int, row: sqlite3.Row, rotation: int,
                       mirror: bool) -> Image.Image | None:
        """The video's big thumbnail as it would be turned *rotation* and
        mirrored *mirror* from the frame itself, or None when it cannot be read."""
        for size in ("l", "s"):
            try:
                with Image.open(media.thumb_path(self.thumbs_dir, asset_id, size)) as img:
                    frame = img.convert("RGB")
            except (OSError, ValueError):
                continue
            # Back to the frame as the video has it (the turn undone, then the
            # mirror), then the new way.
            frame = frame.rotate(row["rotation"] or 0, expand=True)
            if row["mirror"]:
                frame = ImageOps.mirror(frame)
            return media.turn(frame, rotation, mirror)
        return None

    def remake_later(self, asset_ids: list[int]) -> None:
        """Make the thumbnails of *asset_ids* again, in the background, one at
        a time: for many turned at once (Rotate on a selection), where making
        them all before answering would keep the person waiting. A tile on
        screen meanwhile is made at once when it is asked for. One worker for
        all of them: a large selection comes in pieces, and a second Rotate
        before the first is done adds to the same list rather than making the
        same pictures twice at once."""
        with self._remaking_lock:
            self._remaking.update(dict.fromkeys(asset_ids))
            if self._remaker:
                return
            self._remaker = True
        threading.Thread(target=self._remake_work, name="remake-thumbnails", daemon=True).start()

    def _remake_work(self) -> None:
        parallel.background()
        conn, finished = None, False
        try:
            conn = db.connect(self.data_dir)
            # Written down in batches, as in the thumbnail pass (see Batch).
            with Batch(conn, self._record_many) as batch:
                # Made in this run but perhaps not written down yet (the batch):
                # asked for again meanwhile, the same turn is not made twice.
                made_as: dict[int, tuple] = {}
                while not self._stop.is_set():
                    with self._remaking_lock:
                        if not self._remaking:
                            # Under the lock: one added now starts a new worker.
                            self._remaker, finished = False, True
                            return
                        asset_id = next(iter(self._remaking))
                        del self._remaking[asset_id]
                    row = self._now_row(conn, asset_id)
                    # Made already (a tile asked for it), or its folder taken out of
                    # the library meanwhile: nothing to do.
                    if row is None or row["thumb"] != 0 or row["missing"]:
                        continue
                    if made_as.get(asset_id) == (row["rotation"], row["mirror"]):
                        continue
                    try:
                        made = self._render(row, ("s", "l"))
                    except Exception:  # noqa: BLE001 — the thumbnail pass tries again
                        log.debug("could not remake thumbnails for %s", asset_id, exc_info=True)
                        continue
                    if made is not None:
                        made_as[asset_id] = (row["rotation"], row["mirror"])
                        if batch.add((row, *made)):
                            self.generation += 1
        finally:
            self.generation += 1
            if not finished:
                with self._remaking_lock:
                    self._remaker = False
            if conn is not None:
                conn.close()

    def _thumbnail(self, conn: sqlite3.Connection, row: sqlite3.Row,
                   sizes: tuple[str, ...], threads: int = 0) -> bool:
        made = self._render(row, sizes, threads)
        if made is None:
            return False
        self._record(conn, row, *made)
        return made[1]

    def _render(self, row: sqlite3.Row, sizes: tuple[str, ...], threads: int = 0
                ) -> tuple[tuple[str, ...], bool, str | None] | None:
        """Make the thumbnails for *row* (no index work, so any thread may):
        (sizes made, whether they were, colour), or None for a file that is
        not there right now. *threads* is for a video's ffmpeg."""
        full = full_path(row["root"], row["dir"], row["name"])
        if not os.path.isfile(long_path(full)) or leads_away(full, self._data_real):
            # Away, not broken: a drive asleep or unplugged. The row keeps its
            # place in the queue for when the file is back; the next walk
            # marks it missing if it is gone for good (or made a link away).
            return None
        if row["kind"] == "video":
            sizes = ("s", "l")   # one ffmpeg call makes both
        elif sizes == ("s",) and not media.read_reduced(row["name"]):
            # A HEIC, PNG or TIFF is read whole for the small one too: the big
            # one from the same reading costs 5% more, where the finishing
            # pass would read the file whole again (half the scan's time).
            sizes = ("s", "l")
        ok, colour = media.make_thumbnails(full, row["kind"], self.thumbs_dir, row["id"], sizes,
                                           rotation=row["rotation"] or 0, threads=threads,
                                           mirror=bool(row["mirror"]))
        return sizes, ok, colour

    def _record(self, conn: sqlite3.Connection, row: sqlite3.Row, sizes: tuple[str, ...],
                ok: bool, colour: str | None) -> None:
        self._record_many(conn, [(row, sizes, ok, colour)])

    @staticmethod
    def _record_many(conn: sqlite3.Connection, made: list[tuple]) -> None:
        """Thumbnails made, written down in one transaction."""
        version = int(time.time() * 1000) % 2_000_000_000
        with conn:
            for row, sizes, ok, colour in made:
                Scanner._record_one(conn, row, sizes, ok, colour, version)

    @staticmethod
    def _record_one(conn: sqlite3.Connection, row: sqlite3.Row, sizes: tuple[str, ...],
                    ok: bool, colour: str | None, version: int) -> None:
        if conn.execute("SELECT 1 FROM assets WHERE id = ? AND rotation = ? AND mirror = ?",
                        (row["id"], row["rotation"] or 0, int(bool(row["mirror"])))
                        ).fetchone() is None:
            # Turned or flipped while this was being made (Save in the viewer,
            # Rotate on a selection): the picture is the old way up. Left to be
            # made again, never written down as current.
            conn.execute("UPDATE assets SET thumb = ?, large = 0 WHERE id = ? AND thumb != ?",
                         (db.THUMB_PENDING, row["id"], db.THUMB_NONE))
            return
        if "s" in sizes:
            # NONE only for a file that is there and cannot be read as a
            # picture: that is final until the file changes.
            conn.execute("UPDATE assets SET thumb = ?, color = COALESCE(?, color), "
                         "thumb_v = ? WHERE id = ?",
                         (db.THUMB_OK if ok else db.THUMB_NONE, colour, version, row["id"]))
        if "l" in sizes:
            # 2: tried and failed, so the finishing pass does not ask again;
            # a request for the big one (thumbnail_now) still may.
            conn.execute("UPDATE assets SET large = ? WHERE id = ?",
                         (1 if ok else 2, row["id"]))

    def thumbnail_now(self, conn: sqlite3.Connection, asset_id: int, size: str = "s",
                      slots: parallel.Slots | None = None) -> bool:
        """Make one thumbnail right away, for a picture that is on screen.

        With *slots*, it is made in turn with the others being made for
        requests, and :class:`parallel.Busy` says too many are waiting. Two
        requests for the same one at once (the family's television and a
        phone on the same new folder) make it once: the second waits for the
        first and finds it made."""
        found = self._made_already(conn, asset_id, size)
        if found is not None:
            return found
        with self._making.hold((asset_id, size)):
            row = self._now_row(conn, asset_id)
            found = self._made_already(conn, asset_id, size, row)
            if found is not None:
                return found
            if slots is None:
                return self._thumbnail(conn, row, (size,))
            with slots.slot():
                # A video's ffmpeg shares the cores with the other tiles
                # being made right now, rather than each taking them all.
                threads = max(1, parallel.cores() // max(1, slots.in_use))
                return self._thumbnail(conn, row, (size,), threads)

    @staticmethod
    def _now_row(conn: sqlite3.Connection, asset_id: int) -> sqlite3.Row | None:
        return conn.execute(
            """SELECT a.id, a.kind, a.dir, a.name, a.thumb, a.large, a.rotation, a.mirror,
                      a.missing, f.path AS root
               FROM assets a JOIN folders f ON f.id = a.folder_id WHERE a.id = ?""",
            (asset_id,)).fetchone()

    def _made_already(self, conn: sqlite3.Connection, asset_id: int, size: str,
                      row: sqlite3.Row | None = None) -> bool | None:
        """True when the thumbnail is there and current, False when there can
        be none, None when it is still to be made."""
        row = row if row is not None else self._now_row(conn, asset_id)
        if row is None or row["thumb"] == db.THUMB_NONE:
            return False
        # A file on disk counts only when the index says it is current: after
        # a turn the old picture may still be there for a moment.
        current = row["thumb"] == db.THUMB_OK if size == "s" else row["large"] == 1
        if current and media.thumb_path(self.thumbs_dir, asset_id, size).exists():
            return True
        return None

class Batch:
    """Thumbnails written down a batch at a time: one transaction for up to
    :data:`RECORD_EVERY_ROWS` of them, or :data:`RECORD_EVERY_SECONDS`,
    whichever comes first.

    One commit each wrote about 22 KB to the index's journal per thumbnail
    (the row's page and the grid index's pages, again and again): 2 GB for a
    first scan of 100,000 photographs, on a Raspberry Pi's SD card. Every
    commit also tells the remembered counts (db.Remembered) the index has
    changed, so during a scan every page load and every phone counted the
    whole library again. Batched, the journal takes about 1 KB a thumbnail
    and the counts are kept between batches. A thumbnail made but not yet
    written down is at most a second old; a request for it in that second
    makes it again, as before."""

    def __init__(self, conn: sqlite3.Connection, write) -> None:
        self.conn = conn
        self.write = write
        self.pending: list[tuple] = []
        self.since = time.monotonic()

    def __enter__(self) -> Batch:
        return self

    def __exit__(self, *_exc) -> None:
        self.flush()

    def add(self, item: tuple) -> bool:
        """Keep *item*; True when the batch was written down just now."""
        self.pending.append(item)
        if len(self.pending) >= RECORD_EVERY_ROWS \
                or time.monotonic() - self.since >= RECORD_EVERY_SECONDS:
            self.flush()
            return True
        return False

    def flush(self) -> None:
        if self.pending:
            self.write(self.conn, self.pending)
            self.pending = []
        self.since = time.monotonic()


def _ruled(rule: int | None) -> tuple[int, str]:
    """(visibility, vis_source) for a new file under *rule* (None: no rule)."""
    return (db.VIS_FAMILY, "default") if rule is None else (rule, "rule")


def day_of(taken_at: float) -> str:
    """'YYYY-MM-DD' in local time: the day a photo is filed under."""
    return from_timestamp(taken_at).strftime("%Y-%m-%d")


def inside(folder: str, root: str) -> str | None:
    """*folder* as a path relative to the library folder *root* ("/"
    between parts, "" for the root itself), or None when it is not in it.
    A folder holding the root counts as the whole of it."""
    def norm(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))
    f, r = norm(folder), norm(root)
    if f == r or r.startswith(f.rstrip(os.sep) + os.sep):
        return ""
    if not f.startswith(r.rstrip(os.sep) + os.sep):
        return None
    rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(root))
    return "/".join(p for p in rel.split(os.sep) if p and p != ".")


def spelled(root: str, rel: str | None) -> str | None:
    """*rel* inside *root* spelled as the folders themselves are (the walk
    and the index use the names as listed; on Windows and a Mac a path
    typed as ``archive`` finds ``Archive``, and indexing it under the
    other spelling would list every photograph in it twice). None when it
    is not there (yet)."""
    if not rel:
        return rel
    here, parts = root, []
    for part in rel.split("/"):
        if part.startswith(".") or part in IGNORE_DIRS:
            return None                 # never walked into, so never walked from
        try:
            with os.scandir(long_path(here)) as entries:
                names = [e.name for e in entries if e.is_dir()]
        except OSError:
            return None
        name = part if part in names else next(
            (n for n in names if n.casefold() == part.casefold()), None)
        if name is None:
            return None
        parts.append(name)
        here = os.path.join(here, name)
    return "/".join(parts)


def under_any(rel_dir: str, name: str, failed: list[str]) -> bool:
    """Whether the file *rel_dir*/*name* lies in (or is) one of the *failed* paths."""
    path = f"{rel_dir}/{name}" if rel_dir else name
    return any(p == "" or path == p or path.startswith(p + "/") for p in failed)


def within(real: str, folder_real: str) -> bool:
    """Whether the resolved path *real* is *folder_real* or inside it."""
    a, b = os.path.normcase(real), os.path.normcase(folder_real)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def leads_away(path: str, data_real: str) -> bool:
    """Whether *path*, through a link on the way, reaches into this program's
    own data folder (the index, the thumbnails of Hidden photographs) or is a
    file that is not the photograph or video its name says: "photo.jpg" made
    a link to a private key, by someone who can write to the photo folder.
    Neither is indexed or served. A linked folder of photographs is fine."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    if within(real, data_real):
        return True
    return media.kind_of(os.path.basename(real)) != media.kind_of(os.path.basename(path))


def full_path(root: str, rel_dir: str, name: str) -> str:
    parts = [p for p in rel_dir.split("/") if p] if rel_dir else []
    return os.path.join(root, *parts, name)
