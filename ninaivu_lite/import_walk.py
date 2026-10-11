"""Walking an import's sources: every wanted file, in a fixed order, and the
estimate the console asks for before Start. A part of :class:`importer.Importer`."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from . import db
from .dates import long_path
from .import_plan import (ESTIMATE_CAP, IGNORE_DIRS, MARKER, MIN_BYTES, NEVER_MEDIA_EXTS,
                          PACKED_EXTS, Gate, _ext, free_space, is_sidecar, is_within,
                          kind_by_extension, norm, sniff_media)

# Logged under the importer's name, as these lines always were.
log = logging.getLogger("ninaivu_lite.importer")


class Walking:
    """The sources walked, and the estimate made from that walk."""

    def _done_bytes(self, sources: list[str], destination: str) -> int:
        """Bytes of these sources already archived in *destination* and still
        the same file: the room a run (or a resumed run) does not need again."""
        done = 0
        try:
            conn = db.connect(self.data_dir)
        except Exception:  # noqa: BLE001 — no estimate is better than no run
            return 0
        try:
            for row in conn.execute("SELECT source, destination, size, mtime FROM import_files "
                                    "WHERE status = 'verified' AND destination IS NOT NULL"):
                if not is_within(row["destination"], destination) \
                        or not any(is_within(row["source"], s) for s in sources):
                    continue
                try:
                    st = os.stat(long_path(row["source"]))
                except OSError:
                    continue
                if st.st_size == row["size"] and abs(st.st_mtime - (row["mtime"] or 0)) < 2:
                    done += row["size"]
        finally:
            conn.close()
        return done

    # -- walking the sources ------------------------------------------------------

    def _walk(self, sources: list[str], kinds: list[str], destination: str,
              gate: Gate | None = None, counters: dict[str, int] | None = None,
              cancelled: threading.Event | None = None, library: list[str] | None = None):
        """(path, stat, kind) for every wanted file under the sources, in a
        fixed order, so a dry run predicts the real run."""
        counters = counters if counters is not None else {}
        avoid = {norm(destination), norm(self.data_dir)} if destination else {norm(self.data_dir)}
        # A source holding the library (a whole drive, Pictures) must not copy
        # the library into the archive: every photograph would show twice.
        avoid |= {norm(root) for root in (self.job.get("library", ()) if library is None
                                          else library)}
        for source in sources:
            stack = [os.path.abspath(source)]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if gate:
                    gate.check()
                if cancelled is not None and cancelled.is_set():
                    return
                real = norm(current)
                if real in visited or real in avoid:
                    continue
                visited.add(real)
                counters["folder"] = current  # type: ignore[assignment]
                try:
                    with os.scandir(long_path(current)) as entries:
                        items = sorted(entries, key=lambda e: e.name)
                except OSError as exc:
                    counters["unreadable"] = counters.get("unreadable", 0) + 1
                    log.info("import: cannot read %s: %s", current, exc)
                    continue
                subdirs = []
                for entry in items:
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not name.startswith(".") and name not in IGNORE_DIRS:
                                subdirs.append(os.path.join(current, name))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    kind = self._wanted(os.path.join(current, name), name, st.st_size, kinds,
                                        counters)
                    if kind:
                        yield os.path.join(current, name), st, kind
                stack.extend(reversed(subdirs))

    @staticmethod
    def _wanted(path: str, name: str, size: int, kinds: list[str],
                counters: dict[str, int]) -> str | None:
        kind = kind_by_extension(name)
        if kind is None:
            if _ext(name) in PACKED_EXTS and not name.startswith("."):
                counters["packed"] = counters.get("packed", 0) + 1
                return None
            if is_sidecar(name) or name == MARKER or _ext(name) in NEVER_MEDIA_EXTS \
                    or name.startswith("."):
                return None
            if size < MIN_BYTES:
                counters["too_small"] = counters.get("too_small", 0) + 1
                return None
            kind = sniff_media(path)
            if kind is None:
                return None
        if size < MIN_BYTES:
            counters["too_small"] = counters.get("too_small", 0) + 1
            return None
        if kind not in kinds:
            counters["left_out"] = counters.get("left_out", 0) + 1
            return None
        return kind

    # -- the estimate (the console asks before Start) ------------------------------

    def estimate(self, sources: list[str], destination: str, kinds: list[str],
                 token: str, library: list[str] | None = None) -> dict[str, Any]:
        stop = threading.Event()
        # Cancelling stops the walk at the next folder, not after the next
        # 200 photographs: a drive of many folders and few photos never got there.
        snapshot = {"running": True, "finished": False, "cancelled": False, "stop": stop,
                    "files": 0, "bytes": 0, "folder": "", "started": time.time()}
        with self._lock:
            self._estimates[token] = snapshot
            self._estimates = {k: v for k, v in self._estimates.items()
                               if v["running"] or time.time() - v["started"] < 600}
        counters: dict[str, Any] = {}
        files = size = 0
        by_kind: dict[str, int] = {}
        bytes_by_kind: dict[str, int] = {}
        truncated = False
        try:
            # The library folders are stepped over as the real run will
            # (A142), not taken from whatever the last run had.
            for _path, st, kind in self._walk(sources, kinds, destination, counters=counters,
                                             cancelled=stop, library=list(library or ())):
                files += 1
                size += st.st_size
                by_kind[kind] = by_kind.get(kind, 0) + 1
                bytes_by_kind[kind] = bytes_by_kind.get(kind, 0) + st.st_size
                if files % 200 == 0:
                    with self._lock:
                        snapshot.update(files=files, bytes=size, folder=counters.get("folder", ""))
                        if snapshot["cancelled"]:
                            stop.set()
                if files >= ESTIMATE_CAP:
                    truncated = True
                    break
        finally:
            with self._lock:
                snapshot.update(running=False, finished=True, files=files, bytes=size)
        if snapshot["cancelled"]:
            return {"ok": False, "cancelled": True}
        free = free_space(destination)
        needed = int(max(0, size - self._done_bytes(sources, destination)) * 1.1)
        return {
            "ok": True, "files": files, "bytes": size, "by_kind": by_kind,
            "bytes_by_kind": bytes_by_kind, "too_small": counters.get("too_small", 0),
            "left_out": counters.get("left_out", 0), "unreadable": counters.get("unreadable", 0),
            "packed": counters.get("packed", 0), "truncated": truncated, "needed": needed, "free": free,
            "fits": free is None or free >= needed,
        }

    def estimate_progress(self, token: str) -> dict[str, Any]:
        with self._lock:
            s = self._estimates.get(token)
            if not s:
                return {"running": False, "finished": False, "known": False}
            return {"running": s["running"], "finished": s["finished"], "known": True,
                    "files": s["files"], "bytes": s["bytes"], "folder": s["folder"],
                    "elapsed": time.time() - s["started"]}

    def cancel_estimate(self, token: str) -> None:
        with self._lock:
            s = self._estimates.get(token)
            if s:
                s["cancelled"] = True
                s["stop"].set()
