"""Finding photos and making thumbnails, in one background thread.

A scan has two passes:

1. **Walk** every folder with ``os.scandir``. A file whose size and modified
   time are unchanged is not opened again, so re-scanning an unchanged library
   takes seconds. New and changed files have their header read (date, size,
   camera) and are queued for thumbnails.
2. **Grid thumbnails**, newest first, so the top of the timeline fills in
   first. These are small and quick (about 20 ms a photo).
3. **Viewer pictures** (1600 px), newest first, in a second quieter pass.

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
from pathlib import Path
from typing import Any

from . import db, media
from .dates import from_timestamp, long_path

log = logging.getLogger(__name__)

#: Folders never worth walking into.
IGNORE_DIRS = {"$RECYCLE.BIN", "System Volume Information", "@eaDir", "#recycle", ".Trash",
               ".Trashes", ".git", "node_modules", "__pycache__", ".thumbnails", ".cache"}
BATCH = 200
RESCAN_EVERY = 30 * 60


class Scanner:
    def __init__(self, data_dir: str | Path, folders: list[str]) -> None:
        self.data_dir = Path(data_dir)
        self.thumbs_dir = self.data_dir / "thumbs"
        self._data_real = os.path.realpath(str(self.data_dir))
        self.folders = list(folders)
        #: Look again every RESCAN_EVERY seconds by itself (the "watch" setting).
        self.auto = True
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        #: Goes up whenever the index changes, so cached answers know they are stale.
        self.generation = 0
        self.status: dict[str, Any] = {
            "state": "idle", "found": 0, "new": 0, "added": 0, "removed": 0,
            "thumbs_left": 0, "thumbs_total": 0, "started": None,
            "unreachable": [], "last_finished": None,
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

    def rescan(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        conn = db.connect(self.data_dir)
        try:
            while not self._stop.is_set():
                try:
                    self.scan_once(conn)
                except Exception:  # noqa: BLE001 — the loop must survive anything
                    log.exception("scan failed; will try again later")
                    self._set(state="idle")
                # With "watch" off it waits for the Rescan button only.
                self._wake.wait(RESCAN_EVERY if self.auto else None)
                self._wake.clear()
        finally:
            conn.close()

    # --- one scan --------------------------------------------------------------

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.status.update(values)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.status)

    def scan_once(self, conn: sqlite3.Connection) -> None:
        ids = db.sync_folders(conn, self.folders)
        self.generation += 1
        self._set(state="walking", found=0, new=0, added=0, removed=0, unreachable=[],
                  started=time.time())
        unreachable = []
        for path, folder_id in ids.items():
            if self._stop.is_set():
                return
            if not os.path.isdir(long_path(path)):
                unreachable.append(path)
                continue
            self._walk_folder(conn, folder_id, path)
        self._set(unreachable=unreachable)
        self._make_thumbnails(conn)
        if self._straighten(conn):
            self._make_thumbnails(conn)      # only the ones just turned
        self._set(state="idle", last_finished=time.time())

    def _walk_folder(self, conn: sqlite3.Connection, folder_id: int, root: str) -> None:
        known = {(r["dir"], r["name"]): (r["id"], r["size"], r["mtime"], r["missing"])
                 for r in conn.execute(
                     "SELECT id, dir, name, size, mtime, missing FROM assets WHERE folder_id = ?",
                     (folder_id,))}
        seen: set[int] = set()
        pending: list[tuple] = []
        rules: dict[str, int | None] = {}

        def flush() -> None:
            if not pending:
                return
            with conn:
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
                           -- a turn somebody set by hand outlives a changed file, and the
                           -- shape stays the shape as shown
                           width=CASE WHEN assets.rot_source='manual' AND assets.rotation % 180 != 0
                                      THEN excluded.height ELSE excluded.width END,
                           height=CASE WHEN assets.rot_source='manual' AND assets.rotation % 180 != 0
                                       THEN excluded.width ELSE excluded.height END,
                           rotation=CASE WHEN assets.rot_source='manual' THEN assets.rotation ELSE 0 END,
                           rot_source=CASE WHEN assets.rot_source='manual' THEN 'manual'
                                           ELSE excluded.rot_source END,
                           upright=CASE WHEN assets.rot_source='manual' THEN 1 ELSE excluded.upright END""",
                    pending)
            pending.clear()
            self.generation += 1

        for rel_dir, name, full, st in self._files(root):
            if self._stop.is_set():
                break
            kind = media.kind_of(name)
            if kind is None:
                continue
            self.status["found"] += 1
            row = known.get((rel_dir, name))
            if row and row[1] == st.st_size and abs(row[2] - st.st_mtime) < 1:
                seen.add(row[0])
                if row[3]:
                    with conn:
                        conn.execute("UPDATE assets SET missing = 0 WHERE id = ?", (row[0],))
                continue
            info = media.describe(full, name, kind, st)
            if rel_dir not in rules:
                rules[rel_dir] = db.rule_for(conn, folder_id, rel_dir)
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
            if len(pending) >= BATCH:
                flush()
        flush()
        if self._stop.is_set():
            return
        gone = [r[0] for r in known.values() if r[0] not in seen and not r[3]]
        self.status["removed"] += len(gone)
        if gone:
            with conn:
                conn.executemany("UPDATE assets SET missing = 1 WHERE id = ?",
                                 [(i,) for i in gone])
            self.generation += 1

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
                       width=excluded.width, height=excluded.height, thumb=0, large=0, missing=0""",
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

    def _files(self, root: str):
        """(relative dir, name, full path, stat) for every file under *root*."""
        stack = [""]
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
                        if name not in IGNORE_DIRS and os.path.join(current, name) != self._data_real \
                                and os.path.realpath(os.path.join(current, name)) != self._data_real:
                            subdirs.append(f"{rel}/{name}" if rel else name)
                    elif entry.is_file():
                        yield rel, name, os.path.join(current, name), entry.stat()
                except OSError:
                    continue
            stack.extend(sorted(subdirs, reverse=True))

    # --- thumbnails --------------------------------------------------------------

    _QUEUE = {
        # pass: (where, sizes, state shown)
        "small": ("a.thumb = 0", ("s",), "thumbnails"),
        "large": ("a.thumb = 1 AND a.large = 0 AND a.kind = 'picture'", ("l",), "finishing"),
    }

    def _make_thumbnails(self, conn: sqlite3.Connection) -> None:
        for name in ("small", "large"):
            where, sizes, state = self._QUEUE[name]
            total = conn.execute(
                f"SELECT COUNT(*) FROM assets a WHERE {where} AND a.missing = 0").fetchone()[0]
            self._set(state=state, thumbs_total=total, thumbs_left=total)
            while not self._stop.is_set():
                rows = conn.execute(
                    f"""SELECT a.id, a.kind, a.dir, a.name, a.thumb, a.rotation, f.path AS root
                        FROM assets a JOIN folders f ON f.id = a.folder_id
                        WHERE {where} AND a.missing = 0
                        ORDER BY a.captured_at DESC LIMIT 50""").fetchall()
                left = conn.execute(
                    f"SELECT COUNT(*) FROM assets a WHERE {where} AND a.missing = 0").fetchone()[0]
                self._set(thumbs_left=left)
                if not rows:
                    break
                self.generation += 1
                for row in rows:
                    if self._stop.is_set() or self._wake.is_set():
                        return  # a rescan was asked for: walk first, then carry on here
                    self._thumbnail(conn, row, sizes)

    # --- which way up ------------------------------------------------------------

    def _straighten(self, conn: sqlite3.Connection) -> int:
        """Photographs without a camera tag, looked at once each for their
        faces (when OpenCV is installed) and turned in the index. Nobody is
        asked and the file is never touched; the thumbnails are remade, and
        the viewer turns the picture as it shows it. Returns how many turned."""
        if not media.FACES:
            return 0
        turned = 0
        while not self._stop.is_set() and not self._wake.is_set():
            rows = conn.execute(
                """SELECT a.id, a.dir, a.name, a.width, a.height, f.path AS root FROM assets a
                   JOIN folders f ON f.id = a.folder_id
                   WHERE a.upright = 0 AND a.kind = 'picture' AND a.missing = 0
                   ORDER BY a.captured_at DESC LIMIT 50""").fetchall()
            if not rows:
                break
            self._set(state="finishing", thumbs_total=0, thumbs_left=0)
            for row in rows:
                if self._stop.is_set() or self._wake.is_set():
                    return turned
                rotation = media.detect_rotation(full_path(row["root"], row["dir"], row["name"]))
                turned += self.set_rotation(conn, row["id"], rotation, "faces" if rotation else "none",
                                            remake=False)
        return turned

    def set_rotation(self, conn: sqlite3.Connection, asset_id: int, rotation: int, source: str,
                     remake: bool = True) -> bool:
        """Store a quarter turn for one photograph (the index's answer, never the
        file's), swapping its width and height as shown. The thumbnails are
        remade now, or left for the thumbnail pass when *remake* is False.
        Returns whether the picture turned a different way than before."""
        row = conn.execute("SELECT rotation, width, height FROM assets WHERE id = ?",
                           (asset_id,)).fetchone()
        if row is None:
            return False
        rotation %= 360
        changed = rotation != (row["rotation"] or 0)
        swap = changed and (rotation % 180) != ((row["rotation"] or 0) % 180)
        width, height = (row["height"], row["width"]) if swap else (row["width"], row["height"])
        with conn:
            conn.execute(
                "UPDATE assets SET rotation = ?, rot_source = ?, upright = 1, width = ?, height = ?"
                + (", thumb = 0, large = 0" if changed else "") + " WHERE id = ?",
                (rotation, source, width, height, asset_id))
        if changed:
            # The old thumbnails show the old way up: gone now, so nothing can
            # serve them before the new ones exist.
            for size in media.THUMB_SIZES:
                try:
                    media.thumb_path(self.thumbs_dir, asset_id, size).unlink()
                except OSError:
                    pass
            self.generation += 1
            if remake:
                row = conn.execute(
                    """SELECT a.id, a.kind, a.dir, a.name, a.thumb, a.rotation, f.path AS root
                       FROM assets a JOIN folders f ON f.id = a.folder_id WHERE a.id = ?""",
                    (asset_id,)).fetchone()
                if row is not None:
                    self._thumbnail(conn, row, ("s", "l"))
        return changed

    def _thumbnail(self, conn: sqlite3.Connection, row: sqlite3.Row,
                   sizes: tuple[str, ...]) -> bool:
        full = full_path(row["root"], row["dir"], row["name"])
        if row["kind"] == "video":
            sizes = ("s", "l")   # one ffmpeg call makes both
        ok, colour = media.make_thumbnails(full, row["kind"], self.thumbs_dir, row["id"], sizes,
                                           rotation=row["rotation"] or 0)
        version = int(time.time() * 1000) % 2_000_000_000
        with conn:
            if "s" in sizes:
                conn.execute("UPDATE assets SET thumb = ?, color = COALESCE(?, color), "
                             "thumb_v = ? WHERE id = ?",
                             (db.THUMB_OK if ok else db.THUMB_NONE, colour, version, row["id"]))
            if "l" in sizes:
                conn.execute("UPDATE assets SET large = ? WHERE id = ?",
                             (1 if ok else 0, row["id"]))
        return ok

    def thumbnail_now(self, conn: sqlite3.Connection, asset_id: int, size: str = "s") -> bool:
        """Make one thumbnail right away, for a picture that is on screen."""
        row = conn.execute(
            """SELECT a.id, a.kind, a.dir, a.name, a.thumb, a.large, a.rotation, f.path AS root
               FROM assets a JOIN folders f ON f.id = a.folder_id WHERE a.id = ?""",
            (asset_id,)).fetchone()
        if row is None or row["thumb"] == db.THUMB_NONE:
            return False
        # A file on disk counts only when the index says it is current: after
        # a turn the old picture may still be there for a moment.
        current = row["thumb"] == db.THUMB_OK if size == "s" else row["large"] == 1
        if current and media.thumb_path(self.thumbs_dir, asset_id, size).exists():
            return True
        return self._thumbnail(conn, row, (size,))


def day_of(taken_at: float) -> str:
    """'YYYY-MM-DD' in local time: the day a photo is filed under."""
    return from_timestamp(taken_at).strftime("%Y-%m-%d")


def full_path(root: str, rel_dir: str, name: str) -> str:
    parts = [p for p in rel_dir.split("/") if p] if rel_dir else []
    return os.path.join(root, *parts, name)
