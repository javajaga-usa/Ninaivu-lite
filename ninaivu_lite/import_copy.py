"""Copying and checking: each file hashed as it is read, written under a
temporary name, renamed into place and read back; duplicates by content; the
sidecars beside it; and the audit that reads the archive again. A part of
:class:`importer.Importer`."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any

from . import dates, parallel
from .dates import long_path
from .import_plan import (CHUNK, LIVE_CLIP_EXTS, LIVE_STILL_EXTS, MARKER, PARTIAL_DIR, PLAN,
                          SHOW_EVERY, TERMINAL, Cancelled, Incomplete, Problem, _say,
                          capture_date, free_space, hash_file, human_size, is_within, publish,
                          still_done, target_folder, too_big)

# Logged under the importer's name, as these lines always were.
log = logging.getLogger("ninaivu_lite.importer")


class Copying:
    """Every file copied, checked and written down; and the audit."""

    # -- copying ---------------------------------------------------------------------

    def _partial_dir(self) -> str:
        return os.path.join(self.job["destination"], PARTIAL_DIR)

    def _sweep_partials(self) -> None:
        """Leftovers of an interrupted copy live in one folder, so clearing
        them never walks the archive."""
        folder = self._partial_dir()
        try:
            shutil.rmtree(long_path(folder))
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.info("import: could not clear %s: %s", folder, exc)

    def _copy_all(self, conn: sqlite3.Connection) -> None:
        j = self.job
        dry = j["mode"] == "dry-run"
        counters: dict[str, Any] = {}
        total = size = 0
        for _path, st, _kind in self._walk(j["sources"], j["kinds"], j["destination"],
                                           self.gate, counters):
            total += 1
            size += st.st_size
            if total % 500 == 0:
                self._set(total_files=total, total_bytes=size)
        self._set(total_files=total, total_bytes=size, unreadable=counters.get("unreadable", 0),
                  packed=counters.get("packed", 0), too_small=counters.get("too_small", 0))
        if not dry:
            # Only what this run's sources already put in this archive is
            # room not needed again: every earlier import, to anywhere, is not.
            done = self._done_bytes(j["sources"], j["destination"])
            free = free_space(j["destination"])
            needed = int(max(0, size - done) * 1.1)
            if free is not None and free < needed:
                raise Problem(_say("Not enough room: the archive needs about {needed} free and "
                                   "the destination has {free}.",
                                   needed=human_size(needed), free=human_size(free)))
            os.makedirs(long_path(j["destination"]), exist_ok=True)
            self._write_marker()
            self._sweep_partials()
            os.makedirs(long_path(self._partial_dir()), exist_ok=True)
        self._set(phase="copying", fresh_started=time.time())
        counters = {}
        shown_at, shown_bytes = time.monotonic(), j["bytes_copied"]
        for path, st, kind in self._walk(j["sources"], j["kinds"], j["destination"],
                                         self.gate, counters):
            self._one(conn, path, st, kind)
            with self._lock:
                self.job["processed"] += 1
                copied = self.job["bytes_copied"]
            # The gallery shows them as they come: a look at the library now
            # and then, when something new has landed. Each look starts the
            # scan over from its walk, so asking every few hundred files kept
            # it walking and never making thumbnails while an import ran (a
            # resumed run steps over thousands of files a second).
            if not dry and self.on_files is not None and copied != shown_bytes \
                    and time.monotonic() - shown_at >= SHOW_EVERY:
                self.on_files(j["destination"])
                shown_at, shown_bytes = time.monotonic(), copied
        if not dry:
            self._sweep_partials()
        # A source that went away during the copy (a card pulled out) is not
        # a finished import, whatever was copied before it went.
        unreadable = max(j.get("unreadable", 0), counters.get("unreadable", 0))
        self._set(unreadable=unreadable)
        gone = [src for src in j["sources"] if not os.path.isdir(long_path(src))]
        missed = total - j["processed"]
        if gone or (unreadable > j.get("unreadable", 0) and missed > 0) \
                or missed > max(5, total // 100):
            raise Incomplete(_say(
                "Not finished: {missed} of {total} files were not reached. The drive may "
                "have been unplugged. Plug it back in and press Start to finish; what was "
                "copied is kept.", missed=f"{max(missed, 0):,}", total=f"{total:,}"))

    def _write_marker(self) -> None:
        path = os.path.join(self.job["destination"], MARKER)
        if os.path.isfile(long_path(path)):
            return
        try:
            with open(long_path(path), "w", encoding="utf-8") as f:
                json.dump({"tool": "ninaivu-lite", "layout": "YYYY/MM/DD",
                           "created": time.time(), "id": uuid.uuid4().hex}, f)
        except OSError as exc:
            log.info("import: could not write the archive marker: %s", exc)

    def _one(self, conn: sqlite3.Connection, src: str, st: os.stat_result, kind: str) -> None:
        j = self.job
        dry = j["mode"] == "dry-run"
        name = os.path.basename(src)
        row = conn.execute("SELECT status, destination, hash, dest_hash, duplicate_of, size, "
                           "mtime FROM import_files WHERE source = ?", (src,)).fetchone()
        # A duplicate counts only against a copy in this destination (A138):
        # one found in another archive is no reason to leave this one short.
        kept = row and (row["destination"] or row["duplicate_of"])
        if row is not None and row["status"] in TERMINAL and (
                not kept or is_within(kept, j["destination"])) \
                and still_done(row, st):
            with self._lock:
                self.job["stepped_over"] += 1
            return
        if row is None:
            with conn:
                conn.execute(
                    "INSERT INTO import_files (source, name, size, mtime, status, job_id, "
                    "updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (src, name, st.st_size, st.st_mtime, j["job_id"], time.time()))
        # A dry run never writes over what a real run recorded (an archived
        # copy, a duplicate, an error to retry): its answer for such a file is
        # only counted.
        aside = dry and row is not None and row["status"] not in PLAN + ("pending",)

        def mark(status: str, **fields: Any) -> None:
            if aside:
                with self._lock:
                    tally = self.job["plan_aside"]
                    tally[status] = tally.get(status, 0) + 1
                return
            # Looked at again: what is recorded is what the file is now, written
            # with the answer, so a run stopped mid-copy is not taken as done.
            self._mark(conn, src, status, size=st.st_size, mtime=st.st_mtime, **fields)

        tmp = None
        try:
            if st.st_size == 0:
                mark("plan-skip" if dry else "skipped", error="The file is empty.")
                return
            taken, source = capture_date(src, kind, st)
            folder = target_folder(j["destination"], taken)
            # A Live Photo's short video goes where its still went, under the
            # still's name: filed by its own date (kept in UTC) it can land a
            # day away, and a still renamed IMG_0042_1.HEIC for a photograph
            # of that name already there would leave IMG_0042.MOV beside that
            # other photograph, which the gallery would then play it from.
            beside = self._still_beside(conn, src, dry) if kind == "video" else None
            if beside is not None:
                folder = os.path.dirname(beside)
                name = os.path.splitext(os.path.basename(beside))[0] + os.path.splitext(name)[1]
            when = taken.strftime("%Y-%m-%d %H:%M:%S") if taken else None
            src_hash = None
            # Hash up front only when it can settle a duplicate now; otherwise
            # the hash is taken while copying, which reads the file once. A
            # dry run asks the same question (it used to read every byte of
            # every file, as long as the copy it foretells); only then are its
            # earlier predictions of this size read. (From Ninaivu 2.1.0.)
            if self._size_known(conn, st.st_size):
                if dry:
                    self._hash_earlier_predictions(conn, st.st_size)
                src_hash = hash_file(src, self.gate)
                dup = self._duplicate_of(conn, src_hash, src, st.st_size)
                if dup:
                    mark("plan-duplicate" if dry else "duplicate",
                         hash=src_hash, taken=when, date_source=source, duplicate_of=dup)
                    return
            if dry:
                candidate, identical, src_hash = self._unique_path(
                    conn, folder, name, src_hash, st.st_size, planning=True, source=src,
                    with_hash=True)
                if identical:
                    mark("plan-duplicate", hash=src_hash, taken=when, date_source=source,
                         duplicate_of=candidate)
                else:
                    mark("planned", hash=src_hash, taken=when, date_source=source,
                         destination=candidate)
                return
            tmp = os.path.join(self._partial_dir(), f"{uuid.uuid4().hex}.tmp")
            digest, count = self._copy_and_hash(src, tmp)
            if src_hash and digest != src_hash:
                raise OSError("The file changed while it was being read.")
            if count != st.st_size and count != os.stat(long_path(src)).st_size:
                raise OSError("The file changed size while it was being read.")
            if not src_hash:
                dup = self._duplicate_of(conn, digest, src, count)
                if dup:
                    mark("duplicate", hash=digest, taken=when, date_source=source,
                         duplicate_of=dup)
                    return
            os.makedirs(long_path(folder), exist_ok=True)
            # Only the same photograph as the one filed there (the fresh copy
            # has its recorded hash) replaces it: a new photograph at a reused
            # card path must never write over an archived one, edited or not.
            if row is not None and row["hash"] == digest and self._damaged_copy(row, j["destination"]):
                # The audit found this source's earlier copy damaged: the
                # fresh copy takes its place rather than a _1 beside it.
                prior = row["destination"]
                final, identical = prior, self._same_bytes(conn, prior, digest, count)
                repair = True
            else:
                final, identical = self._unique_path(conn, folder, name, digest, count)
                repair = False
            if not identical:
                try:
                    shutil.copystat(long_path(src), long_path(tmp))
                except OSError:
                    pass
            while not identical:
                if repair:
                    os.replace(long_path(tmp), long_path(final))
                elif not publish(tmp, final):
                    # A file came to that name since it was found free (a
                    # copy made by hand, another program): never over it.
                    final, identical = self._unique_path(conn, folder, name, digest, count)
                    continue
                tmp = None
                break
            if not identical:
                with self._lock:
                    self.job["bytes_copied"] += count
                back = hash_file(final, self.gate)
                if back != digest:
                    try:
                        os.remove(long_path(final))
                    except OSError:
                        pass
                    raise OSError("The copy read back from the archive did not match the "
                                  "original, so it was removed.")
            mark("verified", hash=digest, dest_hash=digest, taken=when, date_source=source,
                 destination=final)
            self._copy_sidecars(src, final, identical)
        except Cancelled:
            raise
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EFBIG) and too_big(j["destination"], st.st_size):
                # One file over what the destination's format holds (4 GB on
                # FAT32, which Windows reports as a full disk) fails alone
                # (A139): stopping here would stop every rerun at this file.
                log.info("import: %s: too large for the destination: %s", src, exc)
                mark("error", error="Too large for the destination drive's format "
                                    "(FAT32 holds files up to 4 GB).")
                return
            if exc.errno == errno.ENOSPC:
                # A full disk fails every file after this one, and the index
                # is usually on the same disk: stop now, Start carries on.
                mark("error", error="The destination is full.")
                raise Problem(_say("The destination is full. Make room on it, then press Start "
                                   "to carry on.")) from exc
            log.info("import: %s: %s", src, exc)
            mark("error", error=f"{exc}"[:300])
        except Exception as exc:  # noqa: BLE001 — recorded against the file, run goes on
            log.info("import: %s: %s", src, exc)
            mark("error", error=f"{exc}"[:300])
        finally:
            if tmp:
                try:
                    os.remove(long_path(tmp))
                except OSError:
                    pass

    def _still_beside(self, conn: sqlite3.Connection, src: str, dry: bool) -> str | None:
        """Where the still of a Live Photo whose short video is *src* was put
        in this archive (or found already there), or None: IMG_0042.HEIC (or
        .JPG) beside IMG_0042.MOV in the source folder, imported first (a
        folder is walked in name order)."""
        stem, ext = os.path.splitext(src)
        if ext.lower() not in LIVE_CLIP_EXTS:
            return None
        statuses = ("planned", "plan-duplicate") if dry else ("verified", "duplicate")
        for row in conn.execute(
                "SELECT source, status, destination, duplicate_of FROM import_files "
                "WHERE source > ? AND source < ? ORDER BY source", (stem + ".", stem + ".\uffff")):
            other_stem, other_ext = os.path.splitext(row["source"])
            if other_stem != stem or other_ext.lower() not in LIVE_STILL_EXTS \
                    or row["status"] not in statuses:
                continue
            kept = row["destination"] if row["status"] in ("verified", "planned") \
                else row["duplicate_of"]
            if kept and is_within(kept, self.job["destination"]):
                return kept
        return None

    def _damaged_copy(self, row: sqlite3.Row | None, destination: str) -> bool:
        """Whether the archived copy an error row points at is one the audit
        found damaged (its bytes no longer match the hash it was filed with).
        A row marked error for any other reason, such as a read error on a
        changed source, still points at a good earlier copy of something else,
        which must never be written over."""
        if row is None or row["status"] != "error" or not row["destination"] \
                or not row["dest_hash"] or not is_within(row["destination"], destination):
            return False
        try:
            # Damage (a bad sector, bit rot) changes the bytes where they lie;
            # anything that saves the file (a photo editor's crop or turn)
            # gives it a new time or size. Such a copy is the family's edit,
            # never written over: the fresh copy goes beside it.
            st = os.stat(long_path(row["destination"]))
            if row["mtime"] is None or st.st_size != row["size"] \
                    or abs(st.st_mtime - row["mtime"]) > 2:
                return False
            return hash_file(row["destination"], self.gate) != row["dest_hash"]
        except OSError:
            return False

    def _mark(self, conn: sqlite3.Connection, src: str, status: str, **fields: Any) -> None:
        fields.update(status=status, updated_at=time.time(), job_id=self.job["job_id"])
        if status != "error":
            fields.setdefault("error", None)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE import_files SET {sets} WHERE source = ?",
                         [*fields.values(), src])

    @staticmethod
    def _size_known(conn: sqlite3.Connection, size: int) -> bool:
        return conn.execute("SELECT 1 FROM import_files WHERE size = ? AND status IN "
                            "('verified', 'planned') LIMIT 1", (size,)).fetchone() is not None

    def _duplicate_of(self, conn: sqlite3.Connection, digest: str, src: str,
                      size: int) -> str | None:
        """The archived copy of these bytes, inside this destination, or None.

        A copy counts only if it is on disk now with these very bytes: one
        deleted or changed since it was recorded is no reason to leave the
        new file out. A planned copy (a dry run) is not on disk yet."""
        for row in conn.execute(
                "SELECT destination, status FROM import_files WHERE hash = ? AND source != ? "
                "AND status IN ('verified', 'planned') AND destination IS NOT NULL", (digest, src)):
            if not is_within(row[0], self.job["destination"]):
                continue
            if row[1] == "planned" or self._same_bytes(conn, row[0], digest, size):
                return row[0]
        return None

    def _unique_path(self, conn: sqlite3.Connection, folder: str, name: str, digest: str | None,
                     size: int, planning: bool = False, source: str = "",
                     with_hash: bool = False) -> tuple:
        """A free name in *folder*, or the existing file when its bytes are
        the same (then nothing needs copying). Returns (path, identical).

        A dry run may come without *digest*: the source (*source*) is then
        read only if a file of its size is found at a candidate name, the one
        case the answer depends on its bytes. With *with_hash* the digest
        used, or None, comes back as a third value."""
        stem, ext = os.path.splitext(name)
        counter = 0

        def found(path: str, identical: bool) -> tuple:
            return (path, identical, digest) if with_hash else (path, identical)

        while counter <= 9999:
            self.gate.check()
            candidate = os.path.join(folder, name if counter == 0 else f"{stem}_{counter}{ext}")
            if os.path.exists(long_path(candidate)):
                if digest is None and source and self._same_size(candidate, size):
                    digest = hash_file(source, self.gate)
                if self._same_bytes(conn, candidate, digest, size):
                    return found(candidate, True)
            elif not (planning and conn.execute(
                    "SELECT 1 FROM import_files WHERE destination = ? AND status = 'planned' "
                    "AND source != ?", (candidate, source)).fetchone()):
                return found(candidate, False)
            counter += 1
        raise RuntimeError(f"No free name could be found for {name}.")

    @staticmethod
    def _same_size(candidate: str, size: int) -> bool:
        """Whether the file at *candidate* could be these bytes at all."""
        try:
            return os.stat(long_path(candidate)).st_size == size
        except OSError:
            return False

    def _hash_earlier_predictions(self, conn: sqlite3.Connection, size: int) -> None:
        """Read the files a dry run predicted at this size without reading
        them: only now does their digest matter, since this file is the same
        size and may be the twin of one. A real run never needs this, because
        every file it copies is hashed on the way."""
        rows = conn.execute("SELECT source, destination FROM import_files WHERE size = ? "
                            "AND status = 'planned' AND hash IS NULL", (size,)).fetchall()
        for row in rows:
            if not row["destination"] or not is_within(row["destination"], self.job["destination"]):
                continue
            try:
                digest = hash_file(row["source"], self.gate)
            except OSError:
                continue
            with conn:
                conn.execute("UPDATE import_files SET hash = ? WHERE source = ?",
                             (digest, row["source"]))

    def _same_bytes(self, conn: sqlite3.Connection, candidate: str, digest: str | None,
                    size: int) -> bool:
        try:
            if os.stat(long_path(candidate)).st_size != size:
                return False
        except OSError:
            return False
        if digest is None:
            return False
        # Read from disk, never taken from the index: an archived file can be
        # changed or replaced after its hash was recorded.
        try:
            return hash_file(candidate, self.gate) == digest
        except OSError:
            return False

    def _copy_and_hash(self, src: str, tmp: str) -> tuple[str, int]:
        h = hashlib.sha256()
        total = 0
        # Three things at once on a computer with the cores: the source's
        # next megabyte is read while this one is hashed and, beside it,
        # written. The card, the processor and the archive disk are all kept
        # busy, and each still sees the file in order, once.
        with open(long_path(src), "rb") as fi, open(long_path(tmp), "wb") as fo, \
                closing(parallel.chunks(fi, CHUNK, self.gate.check)) as read, \
                parallel.pool(1, "hash") as hasher:
            for chunk in read:
                hashed = hasher.submit(h.update, chunk)
                fo.write(chunk)
                total += len(chunk)
                hashed.result()
            fo.flush()
            os.fsync(fo.fileno())
        return h.hexdigest(), total

    def _copy_sidecars(self, src: str, final: str, identical: bool = False) -> None:
        """The .xmp/.aae/.thm/.json companions travel with the file, renamed
        to match if it was suffixed. Never fatal: losing a sidecar is bad,
        failing the photo over one is worse. An existing sidecar is never
        written over (A137): it may hold the family's later edits, or belong
        to another photograph with the same name stem."""
        src_dir, base = os.path.split(src)
        stem = os.path.splitext(base)[0]
        final_dir, final_base = os.path.split(final)
        final_stem = os.path.splitext(final_base)[0]
        companions = list(dates.takeout_sidecars(src))
        for ext in (".xmp", ".aae", ".thm", ".XMP", ".AAE", ".THM"):
            for candidate in (os.path.join(src_dir, stem + ext), os.path.join(src_dir, base + ext)):
                if os.path.isfile(long_path(candidate)):
                    companions.append(candidate)
        for companion in dict.fromkeys(companions):
            out_name = os.path.basename(companion)
            if stem != final_stem:
                out_name = out_name.replace(stem, final_stem, 1)
            target = os.path.join(final_dir, out_name)
            if os.path.lexists(long_path(target)):
                if identical or out_name.startswith(final_base):
                    # Already beside this very photograph: keep what is there.
                    continue
                # A stem-named sidecar of another photograph (IMG_0001.JPG
                # beside this IMG_0001.jpeg): use the full-name form instead.
                target = os.path.join(final_dir, final_base + os.path.splitext(companion)[1])
                if os.path.lexists(long_path(target)):
                    log.info("import: sidecar kept out, %s already exists", target)
                    continue
            try:
                # Through a temporary name: one cut short (a full disk) is
                # never left under the real name to be skipped for good.
                partial = target + ".partial"
                shutil.copy2(long_path(companion), long_path(partial))
                if not publish(partial, target):
                    raise FileExistsError(target)
            except OSError as exc:
                log.info("import: sidecar not copied %s: %s", companion, exc)
                try:
                    os.remove(long_path(target + ".partial"))
                except OSError:
                    pass

    # -- the audit ------------------------------------------------------------------

    def _verify(self, conn: sqlite3.Connection) -> None:
        rows = [(r["source"], r["destination"], r["dest_hash"]) for r in conn.execute(
            "SELECT source, destination, dest_hash FROM import_files WHERE status = 'verified'")]
        rows = [r for r in rows if r[1] and is_within(r[1], self.job["destination"])]
        self._set(total_files=len(rows), phase="verifying", fresh_started=time.time())
        for src, final, expected in rows:
            self.gate.check()
            try:
                digest = hash_file(final, self.gate)
                if digest != expected:
                    self._mark(conn, src, "error", error="The archived copy no longer matches "
                                                         "its hash. The run copies it again.")
                else:
                    self._mark(conn, src, "verified")
            except FileNotFoundError:
                self._mark(conn, src, "error", error="The archived copy is missing.")
            except OSError as exc:
                self._mark(conn, src, "error", error=f"{exc}"[:300])
            with self._lock:
                self.job["processed"] += 1
