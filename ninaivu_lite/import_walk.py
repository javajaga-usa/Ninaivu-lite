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
from .import_plan import (ESTIMATE_ABANDONED, ESTIMATE_KEEP, ESTIMATE_WAIT, IGNORE_DIRS, MARKER,
                          MIN_BYTES, NEVER_MEDIA_EXTS, PACKED_EXTS, Gate, _ext, free_space,
                          is_sidecar, is_within, kind_by_extension, norm, sniff_media)

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
        """Count what a run would take, here and now, and return the answer."""
        snapshot = self._new_estimate(sources, destination, kinds, library, watched=False)
        with self._lock:
            self._estimates[token] = snapshot
            snapshot["tokens"].add(token)
        return self._count(snapshot, sources, destination, kinds, library)

    def ask_estimate(self, sources: list[str], destination: str, kinds: list[str],
                     token: str, library: list[str] | None = None,
                     wait: float = ESTIMATE_WAIT) -> dict[str, Any]:
        """The console's question: the count of these folders, or, while it
        is still being made, ``{"ok": False, "pending": True}`` after *wait*
        seconds, and the page asks again with the same token.

        The count used to be made inside the request, so a page held one of
        the web server's few threads for as long as the drive took to walk
        (most of an hour for a large USB drive), and every reload of the
        Import page started a second walk of the same drive while the first
        went on, for nobody, to the end. Now a count of the same folders
        already under way is joined (a reload, a second tab), a finished one
        is handed back for ten minutes until a run starts, and a walk nobody
        has asked about for half a minute is stopped. (From Ninaivu 2.1.0.)"""
        key = self._estimate_key(sources, destination, kinds, library)
        with self._lock:
            self._prune_estimates()
            snapshot = self._estimates.get(token)
            if snapshot is None or snapshot["key"] != key or snapshot["cancelled"]:
                snapshot = next((s for s in self._estimates.values()
                                 if s["key"] == key and s["watched"] and not s["cancelled"]),
                                None)
            fresh = snapshot is None
            if fresh:
                snapshot = self._new_estimate(sources, destination, kinds, library, watched=True)
            self._estimates[token] = snapshot
            snapshot["tokens"].add(token)
            snapshot["seen"] = time.time()
        if fresh:
            threading.Thread(target=self._count, name="import-estimate", daemon=True,
                             args=(snapshot, sources, destination, kinds, library)).start()
        snapshot["done"].wait(max(0.0, wait))
        with self._lock:
            snapshot["seen"] = time.time()
            result = snapshot.get("result")
        return result if result is not None else {"ok": False, "pending": True}

    def _estimate_key(self, sources: list[str], destination: str, kinds: list[str],
                      library: list[str] | None) -> tuple:
        return (tuple(norm(s) for s in sources), norm(destination) if destination else "",
                tuple(sorted(kinds)), tuple(sorted(norm(f) for f in library or ())))

    def _new_estimate(self, sources: list[str], destination: str, kinds: list[str],
                      library: list[str] | None, watched: bool) -> dict[str, Any]:
        now = time.time()
        return {"running": True, "finished": False, "cancelled": False,
                "stop": threading.Event(), "done": threading.Event(), "result": None,
                "files": 0, "bytes": 0, "folder": "", "started": now, "seen": now,
                "ended": None, "watched": watched, "tokens": set(),
                "key": self._estimate_key(sources, destination, kinds, library)}

    def _prune_estimates(self) -> None:
        """Drop counts that ended more than ten minutes ago (under the lock)."""
        now = time.time()
        self._estimates = {k: v for k, v in self._estimates.items()
                           if v["running"] or now - (v["ended"] or now) < ESTIMATE_KEEP}

    def forget_estimates(self) -> None:
        """A run is starting: it counts for itself, so a count under way is
        stopped (two walks of one slow drive slow each other), and a finished
        one is out of date once the run has copied something."""
        with self._lock:
            for s in self._estimates.values():
                s["cancelled"] = True
                s["stop"].set()
            self._estimates = {k: v for k, v in self._estimates.items() if v["running"]}

    def _count(self, snapshot: dict[str, Any], sources: list[str], destination: str,
               kinds: list[str], library: list[str] | None) -> dict[str, Any]:
        stop = snapshot["stop"]
        counters: dict[str, Any] = {}
        files = size = 0
        by_kind: dict[str, int] = {}
        bytes_by_kind: dict[str, int] = {}
        result: dict[str, Any] = {"ok": False, "cancelled": True}
        try:
            # The library folders are stepped over as the real run will
            # (A142), not taken from whatever the last run had. Every file is
            # counted: the count used to stop at 200,000 and say "at least".
            # Cancelling stops the walk at the next folder, not after the next
            # 200 photographs: a drive of many folders and few photos never got there.
            for _path, st, kind in self._walk(sources, kinds, destination, counters=counters,
                                             cancelled=stop, library=list(library or ())):
                files += 1
                size += st.st_size
                by_kind[kind] = by_kind.get(kind, 0) + 1
                bytes_by_kind[kind] = bytes_by_kind.get(kind, 0) + st.st_size
                if files % 200 == 0:
                    with self._lock:
                        snapshot.update(files=files, bytes=size, folder=counters.get("folder", ""))
                        if snapshot["watched"] and time.time() - snapshot["seen"] > ESTIMATE_ABANDONED:
                            # The page that asked has gone (a reload, a closed tab).
                            log.info("import: count of %s stopped: nobody is watching it",
                                     ", ".join(sources))
                            snapshot["cancelled"] = True
                        if snapshot["cancelled"]:
                            stop.set()
            if not snapshot["cancelled"]:
                free = free_space(destination)
                needed = int(max(0, size - self._done_bytes(sources, destination)) * 1.1)
                result = {
                    "ok": True, "files": files, "bytes": size, "by_kind": by_kind,
                    "bytes_by_kind": bytes_by_kind, "too_small": counters.get("too_small", 0),
                    "left_out": counters.get("left_out", 0),
                    "unreadable": counters.get("unreadable", 0),
                    "packed": counters.get("packed", 0), "truncated": False, "needed": needed,
                    "free": free, "fits": free is None or free >= needed,
                }
        finally:
            with self._lock:
                snapshot.update(running=False, finished=True, files=files, bytes=size,
                                ended=time.time(), result=result)
            snapshot["done"].set()
        return result

    def estimate_progress(self, token: str) -> dict[str, Any]:
        with self._lock:
            s = self._estimates.get(token)
            if not s:
                return {"running": False, "finished": False, "known": False}
            s["seen"] = time.time()
            return {"running": s["running"], "finished": s["finished"], "known": True,
                    "files": s["files"], "bytes": s["bytes"], "folder": s["folder"],
                    "elapsed": (s["ended"] or time.time()) - s["started"]}

    def cancel_estimate(self, token: str) -> None:
        """This page no longer wants the count; it stops once no page does."""
        with self._lock:
            s = self._estimates.pop(token, None)
            if s:
                s["tokens"].discard(token)
                if not s["tokens"] and s["running"]:
                    s["cancelled"] = True
                    s["stop"].set()
