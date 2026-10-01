"""Bringing old drives, cards and backup folders into one archive.

Taken from Ninaivu's archive engine and cut to the part a household uses:

* **Many sources, one archive.** Every file is filed as ``YYYY/MM/DD`` by the
  date it was taken (EXIF, the video's own header, a Google Takeout sidecar,
  the file name, a dated folder, then the file's timestamp); a file with no
  believable date goes to ``Unknown-Date``.
* **Sources are only ever read.** Nothing in them is written, renamed or
  deleted, and the bytes are copied unchanged.
* **Every copy is checked.** The file is hashed (SHA-256) as it is read,
  written to a temporary name, renamed into place, then read back from the
  archive and hashed again. A copy that does not match is removed and
  reported.
* **Duplicates are found by content** and left where they are, logged.
  The same name with different bytes gets ``_1``, ``_2``.
* **Start is also Resume.** Progress lives in the index, so an interrupted
  run carries on where it stopped. A dry run decides everything and writes
  nothing; an audit re-reads every archived file and checks its hash.

One background thread, the standard library and Pillow. No pacing, no
drive-health sampling, no classifiers: those stay in Ninaivu.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from PIL import Image

from . import db, dates, media
from .dates import long_path

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
#: Below this a file is an icon or a thumbnail, whatever its extension says.
MIN_BYTES = 24 * 1024
MARKER = ".photo-archive-root"
UNDATED = "Unknown-Date"
PARTIAL_DIR = ".ninaivu-importing"
SIDECAR_EXTS = {".xmp", ".aae", ".thm", ".json"}
KINDS = ("image", "video")
MODES = ("copy", "dry-run", "verify")
#: Extensions that are never media, so they are not opened to be sure.
NEVER_MEDIA_EXTS = {
    ".txt", ".md", ".htm", ".html", ".xml", ".css", ".js", ".mjs", ".ts", ".json", ".py", ".pyc",
    ".java", ".class", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".php", ".rb", ".sh", ".bat",
    ".cmd", ".ps1", ".exe", ".dll", ".so", ".dylib", ".sys", ".msi", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".zip", ".rar", ".7z", ".gz", ".tar",
    ".bz2", ".xz", ".iso", ".dmg", ".db", ".sqlite", ".log", ".ini", ".cfg", ".conf", ".lnk",
    ".url", ".ttf", ".otf", ".woff", ".woff2", ".svg", ".ico", ".cur", ".db-wal", ".db-shm",
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".wma", ".opus",
}
IGNORE_DIRS = {"$RECYCLE.BIN", "System Volume Information", "@eaDir", "#recycle", "node_modules",
               "__pycache__", "Windows", "Program Files", "Program Files (x86)", "ProgramData"}
TERMINAL = ("verified", "duplicate", "skipped")
PLAN = ("planned", "plan-duplicate", "plan-skip")
ESTIMATE_CAP = 200_000


class Cancelled(Exception):
    """Stop was pressed."""


# --- what a file is -----------------------------------------------------------------


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def is_sidecar(name: str) -> bool:
    return _ext(name) in SIDECAR_EXTS


def kind_by_extension(name: str) -> str | None:
    kind = media.kind_of(name)
    return "image" if kind == "picture" else kind


def sniff_media(path: str) -> str | None:
    """'image' or 'video' from the first bytes, for a file whose extension
    says nothing: IMG_0042 with no suffix, or a camcorder's CLIP.DAT."""
    try:
        with open(long_path(path), "rb") as f:
            head = f.read(32)
    except OSError:
        return None
    if len(head) < 12:
        return None
    if head[4:8] == b"ftyp":
        brand = head[8:11]
        return "image" if brand in (b"hei", b"avi", b"mif", b"msf", b"cr3") else "video"
    if head[:4] == b"RIFF":
        return {b"WEBP": "image", b"AVI ": "video"}.get(head[8:12])
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if head[:6] in (b"GIF87a", b"GIF89a") or head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    if head[:4] in (b"\x1aE\xdf\xa3", b"\x30\x26\xb2\x75", b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "video"
    if head[:3] == b"FLV":
        return "video"
    return None


def hash_file(path: str, gate: Gate | None = None) -> str:
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        while True:
            if gate:
                gate.check()
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# --- when it was taken ------------------------------------------------------------


def _exif_date(path: str) -> datetime | None:
    try:
        with Image.open(long_path(path)) as img:
            exif = img.getexif()
            raw = [exif.get(0x0132)]
            try:
                sub = exif.get_ifd(0x8769)
                raw = [sub.get(0x9003), sub.get(0x9004)] + raw
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — not a readable picture
        return None
    for value in raw:
        ts = dates.parse_exif_datetime(value)
        if ts:
            return dates.from_timestamp(ts)
    return None


def capture_date(path: str, kind: str, st: os.stat_result) -> tuple[datetime | None, str]:
    if kind == "image":
        taken = _exif_date(path)
        if taken is not None:
            return taken, "exif"
    return dates.fallback_date(path, st)


def target_folder(destination: str, taken: datetime | None) -> str:
    if taken is None:
        return os.path.join(destination, UNDATED)
    return os.path.join(destination, f"{taken.year:04d}", f"{taken.month:02d}", f"{taken.day:02d}")


# --- paths and the job's shape --------------------------------------------------------


def norm(path: str) -> str:
    """For comparisons only: absolute, links resolved, case-folded on Windows."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def is_within(child: str, parent: str) -> bool:
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _year_folder(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1900 <= int(name) <= 2100


def _date_part(name: str) -> bool:
    if name == UNDATED or _year_folder(name):
        return True
    return len(name) == 2 and name.isdigit() and 1 <= int(name) <= 31


def _looks_like_archive(path: str, exclude: str | None) -> bool:
    if os.path.isfile(os.path.join(path, MARKER)):
        return True
    try:
        with os.scandir(long_path(path)) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if exclude and os.path.normcase(entry.name) == os.path.normcase(exclude):
                    continue
                if entry.name == UNDATED or _year_folder(entry.name):
                    return True
    except OSError:
        pass
    return False


def resolve_destination(chosen: str, known: list[str] = ()) -> dict[str, Any]:
    """The archive root the person meant. Choosing ``Archive/2018`` from inside
    an archive would copy the rest of it into that folder a second time, so a
    folder inside an archive (by marker, by an earlier run, or by its dated
    layout) is replaced with the archive's root, and the reason is said."""
    chosen = (chosen or "").strip()
    out = {"destination": chosen, "corrected": False, "reason": None}
    if not chosen:
        return out
    target = os.path.abspath(os.path.expanduser(chosen))
    current = target
    while True:
        if os.path.isfile(os.path.join(current, MARKER)):
            if norm(current) != norm(target):
                return {"destination": current, "corrected": True,
                        "reason": "That folder is inside an archive made earlier."}
            return out
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for root in known:
        if root and norm(root) != norm(target) and is_within(target, root):
            return {"destination": os.path.abspath(root), "corrected": True,
                    "reason": "That folder is inside the archive an earlier run built."}
    current, child, climbed = target, None, False
    while True:
        name = os.path.basename(current.rstrip(os.sep))
        if not name or not _date_part(name):
            break
        parent = os.path.dirname(current.rstrip(os.sep))
        if not parent or parent == current:
            break
        current, child, climbed = parent, name, True
    if climbed and _looks_like_archive(current, child) and norm(current) != norm(target):
        return {"destination": current, "corrected": True,
                "reason": "That is one of the dated folders inside an existing archive."}
    return out


def clean_sources(raw: Any) -> list[str]:
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        path = item.get("path") if isinstance(item, dict) else item
        if isinstance(path, str) and path.strip():
            text = os.path.normpath(os.path.expanduser(path.strip()))
            if text not in out:
                out.append(text)
    return out[:50]


def clean_kinds(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return list(KINDS)
    return [k for k in KINDS if k in raw]


def validate(sources: list[str], destination: str, data_dir: str) -> list[str]:
    """Why the job cannot start: sentences to show, or an empty list."""
    problems = []
    if not sources:
        problems.append("Add at least one source folder.")
    if not destination:
        problems.append("Choose a destination folder for the archive.")
    if problems:
        return problems
    for s in sources:
        if not os.path.isabs(s):
            problems.append(f"Give the full path of the source folder, not “{s}”.")
        elif not os.path.isdir(long_path(s)):
            problems.append(f"Source “{s}” is not a folder that can be opened.")
    if not os.path.isabs(destination):
        problems.append("Give the full path of the destination folder, for example D:\\Photo Archive.")
    elif os.path.exists(destination) and not os.path.isdir(destination):
        problems.append(f"Destination exists but is not a folder: {destination}")
    elif is_within(destination, data_dir):
        problems.append("The archive cannot be built inside Ninaivu Lite's own data folder.")
    for s in sources:
        if not os.path.isdir(long_path(s)):
            continue
        if norm(s) == norm(destination):
            problems.append(f"Destination is the same folder as source “{s}”. "
                            "The archive must be somewhere else.")
        elif is_within(s, destination):
            problems.append(f"Source “{s}” is inside the destination, so all of it would be "
                            "skipped as part of the archive. Choose a source outside it.")
    for a in sources:
        for b in sources:
            if a != b and os.path.isdir(a) and os.path.isdir(b) and norm(a) != norm(b) \
                    and is_within(a, b):
                problems.append(f"Source “{a}” is already covered by source “{b}”.")
    return problems


def notices(sources: list[str], destination: str) -> list[str]:
    out = []
    for s in sources:
        if destination and os.path.isdir(s) and norm(s) != norm(destination) \
                and is_within(destination, s):
            out.append(f"The archive “{destination}” sits inside source “{s}”. It is skipped "
                       "during the scan, so files already archived are not read back in.")
    return out


def free_space(destination: str) -> int | None:
    probe = destination
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


# --- pause and stop ------------------------------------------------------------------


class Gate:
    def __init__(self) -> None:
        self._go = threading.Event()
        self._go.set()
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._go.clear()

    def resume(self) -> None:
        self._go.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._go.set()

    @property
    def paused(self) -> bool:
        return not self._go.is_set()

    def check(self) -> None:
        """Called between chunks: parks while paused, raises when stopped."""
        if self._cancel.is_set():
            raise Cancelled()
        if not self._go.is_set():
            self._go.wait()
            if self._cancel.is_set():
                raise Cancelled()


# --- the engine -----------------------------------------------------------------------


class Importer:
    """One run at a time, in one background thread."""

    def __init__(self, data_dir: str | os.PathLike) -> None:
        self.data_dir = str(data_dir)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.gate = Gate()
        self.job: dict[str, Any] = {}
        self._estimates: dict[str, dict[str, Any]] = {}

    # -- lifecycle --------------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, sources: list[str], destination: str, kinds: list[str], mode: str) -> None:
        """Begin a run. Raises ValueError with a sentence when it cannot."""
        with self._lock:
            if self.running:
                raise ValueError("A run is already going. Stop it first.")
            self.gate = Gate()
            self.job = {
                "mode": mode, "sources": list(sources), "destination": destination,
                "kinds": list(kinds), "phase": "counting", "message": "",
                "processed": 0, "stepped_over": 0, "total_files": 0, "total_bytes": 0,
                "bytes_copied": 0, "started_at": time.time(), "ended_at": None,
                "fresh_started": None, "unreadable": 0, "job_id": None,
            }
            self._thread = threading.Thread(target=self._run, name="importer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self.gate.cancel()

    def pause(self) -> None:
        if self.running:
            self.gate.pause()

    def resume(self) -> None:
        self.gate.resume()

    def wait(self, timeout: float = 60) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.job.update(values)

    def progress(self) -> dict[str, Any]:
        with self._lock:
            j = dict(self.job)
        running = self.running
        eta = None
        if running and j.get("phase") in ("copying", "verifying") and j.get("fresh_started"):
            fresh = j["processed"] - j["stepped_over"]
            left = j["total_files"] - j["processed"]
            elapsed = time.time() - j["fresh_started"]
            if fresh >= 10 and elapsed > 2:
                eta = int(left * elapsed / fresh)
        return {
            "is_scanning": running,
            "is_paused": running and self.gate.paused,
            "job_mode": j.get("mode"),
            "phase": j.get("phase"),
            "job_message": j.get("message", ""),
            "destination": j.get("destination", ""),
            "processed": j.get("processed", 0),
            "stepped_over": j.get("stepped_over", 0),
            "total_files": j.get("total_files", 0),
            "total_bytes": j.get("total_bytes", 0),
            "bytes_copied": j.get("bytes_copied", 0),
            "eta_seconds": eta,
            "is_resume": bool(j.get("stepped_over")),
        }

    # -- walking the sources ------------------------------------------------------

    def _walk(self, sources: list[str], kinds: list[str], destination: str,
              gate: Gate | None = None, counters: dict[str, int] | None = None,
              cancelled: threading.Event | None = None):
        """(path, stat, kind) for every wanted file under the sources, in a
        fixed order, so a dry run predicts the real run."""
        counters = counters if counters is not None else {}
        avoid = {norm(destination), norm(self.data_dir)} if destination else {norm(self.data_dir)}
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
                 token: str) -> dict[str, Any]:
        snapshot = {"running": True, "finished": False, "cancelled": False,
                    "files": 0, "bytes": 0, "folder": "", "started": time.time()}
        stop = threading.Event()
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
            for _path, st, kind in self._walk(sources, kinds, destination, counters=counters,
                                             cancelled=stop):
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
        needed = int(size * 1.1)
        return {
            "ok": True, "files": files, "bytes": size, "by_kind": by_kind,
            "bytes_by_kind": bytes_by_kind, "too_small": counters.get("too_small", 0),
            "left_out": counters.get("left_out", 0), "unreadable": counters.get("unreadable", 0),
            "truncated": truncated, "needed": needed, "free": free,
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

    # -- one run --------------------------------------------------------------------

    def _run(self) -> None:
        conn = db.connect(self.data_dir)
        job_id = None
        try:
            job_id = self._begin(conn)
            if self.job["mode"] == "verify":
                self._verify(conn)
            else:
                self._copy_all(conn)
            self._finish(conn, job_id, "completed")
        except Cancelled:
            self._finish(conn, job_id, "stopped")
        except Exception as exc:  # noqa: BLE001 — the thread must end tidily
            log.exception("import failed")
            self._set(message=f"{type(exc).__name__}: {exc}")
            self._finish(conn, job_id, "failed")
        finally:
            conn.close()

    def _begin(self, conn: sqlite3.Connection) -> int:
        j = self.job
        with conn:
            cur = conn.execute(
                "INSERT INTO import_jobs (sources, destination, mode, state, phase, started_at) "
                "VALUES (?, ?, ?, 'running', 'counting', ?)",
                (json.dumps(j["sources"]), j["destination"], j["mode"], j["started_at"]))
            job_id = int(cur.lastrowid)
            conn.execute("UPDATE import_jobs SET state = 'stopped', ended_at = ? "
                         "WHERE state = 'running' AND id != ?", (time.time(), job_id))
            if j["mode"] == "copy":
                conn.execute("DELETE FROM import_files WHERE status IN ('planned', "
                             "'plan-duplicate', 'plan-skip')")
        self._set(job_id=job_id)
        return job_id

    def _finish(self, conn: sqlite3.Connection, job_id: int | None, state: str) -> None:
        j = self.job
        phase = {"completed": "done", "stopped": "stopped", "failed": "failed"}[state]
        if state == "completed":
            message = self._summary(conn)
        elif state == "stopped":
            message = "Stopped. Press Start to carry on where it left off."
        else:
            message = j.get("message") or "The run failed."
        self._set(phase=phase, message=message, ended_at=time.time())
        self._sweep_partials()
        if job_id is not None:
            with conn:
                conn.execute(
                    "UPDATE import_jobs SET state = ?, phase = ?, ended_at = ?, message = ?, "
                    "total_files = ?, total_bytes = ?, bytes_copied = ? WHERE id = ?",
                    (state, phase, time.time(), message, j["total_files"], j["total_bytes"],
                     j["bytes_copied"], job_id))
        log.info("import %s: %s", state, message)

    def _summary(self, conn: sqlite3.Connection) -> str:
        j = self.job
        if j["mode"] == "verify":
            errors = conn.execute("SELECT COUNT(*) FROM import_files WHERE status = 'error' "
                                  "AND job_id = ?", (j["job_id"],)).fetchone()[0]
            return ("Audit finished: every archived file matches its hash." if not errors
                    else f"Audit finished: {errors:,} archived files did not match or are missing.")
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM import_files WHERE job_id = ? GROUP BY status",
            (j["job_id"],))}
        if j["mode"] == "dry-run":
            text = (f"Dry run finished: {counts.get('planned', 0):,} files would be archived, "
                    f"{counts.get('plan-duplicate', 0):,} are duplicates. Nothing was written.")
        else:
            text = (f"Finished: {counts.get('verified', 0):,} files archived and verified, "
                    f"{counts.get('duplicate', 0):,} duplicates left in place")
            if j["stepped_over"]:
                text += f", {j['stepped_over']:,} already done"
            text += "."
        if counts.get("error"):
            text += f" {counts['error']:,} could not be archived."
        if j.get("unreadable"):
            text += f" {j['unreadable']:,} folders could not be read."
        return text

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
        self._set(total_files=total, total_bytes=size, unreadable=counters.get("unreadable", 0))
        if not dry:
            done = conn.execute("SELECT COALESCE(SUM(size), 0) FROM import_files "
                                "WHERE status = 'verified'").fetchone()[0]
            free = free_space(j["destination"])
            needed = int(max(0, size - done) * 1.1)
            if free is not None and free < needed:
                raise RuntimeError(
                    f"Not enough room: the archive needs about {needed / 2**30:.1f} GB free "
                    f"and the destination has {free / 2**30:.1f} GB.")
            os.makedirs(long_path(j["destination"]), exist_ok=True)
            self._write_marker()
            self._sweep_partials()
            os.makedirs(long_path(self._partial_dir()), exist_ok=True)
        self._set(phase="copying", fresh_started=time.time())
        counters = {}
        for path, st, kind in self._walk(j["sources"], j["kinds"], j["destination"],
                                         self.gate, counters):
            self._one(conn, path, st, kind)
            with self._lock:
                self.job["processed"] += 1
        if not dry:
            self._sweep_partials()

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
        row = conn.execute("SELECT status, destination FROM import_files WHERE source = ?",
                           (src,)).fetchone()
        if row is not None and row["status"] in TERMINAL and (
                not row["destination"] or is_within(row["destination"], j["destination"])):
            with self._lock:
                self.job["stepped_over"] += 1
            return
        if row is None:
            with conn:
                conn.execute(
                    "INSERT INTO import_files (source, name, size, mtime, status, job_id, "
                    "updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (src, name, st.st_size, st.st_mtime, j["job_id"], time.time()))
        tmp = None
        try:
            if st.st_size == 0:
                self._mark(conn, src, "plan-skip" if dry else "skipped",
                           error="The file is empty.")
                return
            taken, source = capture_date(src, kind, st)
            folder = target_folder(j["destination"], taken)
            when = taken.strftime("%Y-%m-%d %H:%M:%S") if taken else None
            src_hash = None
            # Hash up front only when it can settle a duplicate now; otherwise
            # the hash is taken while copying, which reads the file once.
            if dry or self._size_known(conn, st.st_size):
                src_hash = hash_file(src, self.gate)
                dup = self._duplicate_of(conn, src_hash, src)
                if dup:
                    self._mark(conn, src, "plan-duplicate" if dry else "duplicate",
                               hash=src_hash, taken=when, date_source=source, duplicate_of=dup)
                    return
            if dry:
                candidate, identical = self._unique_path(conn, folder, name, src_hash,
                                                         st.st_size, planning=True)
                if identical:
                    self._mark(conn, src, "plan-duplicate", hash=src_hash, taken=when,
                               date_source=source, duplicate_of=candidate)
                else:
                    self._mark(conn, src, "planned", hash=src_hash, taken=when,
                               date_source=source, destination=candidate)
                return
            tmp = os.path.join(self._partial_dir(), f"{uuid.uuid4().hex}.tmp")
            digest, count = self._copy_and_hash(src, tmp)
            if src_hash and digest != src_hash:
                raise OSError("The file changed while it was being read.")
            if count != st.st_size and count != os.stat(long_path(src)).st_size:
                raise OSError("The file changed size while it was being read.")
            if not src_hash:
                dup = self._duplicate_of(conn, digest, src)
                if dup:
                    self._mark(conn, src, "duplicate", hash=digest, taken=when,
                               date_source=source, duplicate_of=dup)
                    return
            os.makedirs(long_path(folder), exist_ok=True)
            prior = row["destination"] if row is not None and row["status"] == "error" else None
            if prior and is_within(prior, j["destination"]) and os.path.isfile(long_path(prior)):
                # The audit found this run's own earlier copy damaged: the
                # fresh copy takes its place rather than a _1 beside it.
                final, identical = prior, self._same_bytes(conn, prior, digest, count)
            else:
                final, identical = self._unique_path(conn, folder, name, digest, count)
            if not identical:
                try:
                    shutil.copystat(long_path(src), long_path(tmp))
                except OSError:
                    pass
                os.replace(long_path(tmp), long_path(final))
                tmp = None
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
            self._mark(conn, src, "verified", hash=digest, dest_hash=digest, taken=when,
                       date_source=source, destination=final)
            self._copy_sidecars(src, final)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — recorded against the file, run goes on
            log.info("import: %s: %s", src, exc)
            self._mark(conn, src, "error", error=f"{exc}"[:300])
        finally:
            if tmp:
                try:
                    os.remove(long_path(tmp))
                except OSError:
                    pass

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

    def _duplicate_of(self, conn: sqlite3.Connection, digest: str, src: str) -> str | None:
        """The archived copy of these bytes, inside this destination, or None."""
        for row in conn.execute(
                "SELECT destination FROM import_files WHERE hash = ? AND source != ? "
                "AND status IN ('verified', 'planned') AND destination IS NOT NULL", (digest, src)):
            if is_within(row[0], self.job["destination"]):
                return row[0]
        return None

    def _unique_path(self, conn: sqlite3.Connection, folder: str, name: str, digest: str | None,
                     size: int, planning: bool = False) -> tuple[str, bool]:
        """A free name in *folder*, or the existing file when its bytes are
        the same (then nothing needs copying). Returns (path, identical)."""
        stem, ext = os.path.splitext(name)
        counter = 0
        while counter <= 9999:
            self.gate.check()
            candidate = os.path.join(folder, name if counter == 0 else f"{stem}_{counter}{ext}")
            if os.path.exists(long_path(candidate)):
                if self._same_bytes(conn, candidate, digest, size):
                    return candidate, True
            elif not (planning and conn.execute(
                    "SELECT 1 FROM import_files WHERE destination = ? AND status = 'planned'",
                    (candidate,)).fetchone()):
                return candidate, False
            counter += 1
        raise RuntimeError(f"No free name could be found for {name}.")

    def _same_bytes(self, conn: sqlite3.Connection, candidate: str, digest: str | None,
                    size: int) -> bool:
        try:
            if os.stat(long_path(candidate)).st_size != size:
                return False
        except OSError:
            return False
        if digest is None:
            return False
        row = conn.execute("SELECT dest_hash FROM import_files WHERE destination = ? "
                           "AND status = 'verified'", (candidate,)).fetchone()
        known = row[0] if row else None
        return (known or hash_file(candidate, self.gate)) == digest

    def _copy_and_hash(self, src: str, tmp: str) -> tuple[str, int]:
        h = hashlib.sha256()
        total = 0
        with open(long_path(src), "rb") as fi, open(long_path(tmp), "wb") as fo:
            while True:
                self.gate.check()
                chunk = fi.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                fo.write(chunk)
                total += len(chunk)
            fo.flush()
            os.fsync(fo.fileno())
        return h.hexdigest(), total

    def _copy_sidecars(self, src: str, final: str) -> None:
        """The .xmp/.aae/.thm/.json companions travel with the file, renamed
        to match if it was suffixed. Never fatal: losing a sidecar is bad,
        failing the photo over one is worse."""
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
            if os.path.exists(long_path(target)):
                continue
            try:
                shutil.copy2(long_path(companion), long_path(target))
            except OSError as exc:
                log.info("import: sidecar not copied %s: %s", companion, exc)

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


# --- reports for the console ------------------------------------------------------------


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM import_files GROUP BY status")}
    return {
        "verified": counts.get("verified", 0), "duplicates": counts.get("duplicate", 0),
        "skipped": counts.get("skipped", 0), "errors": counts.get("error", 0),
        "planned": counts.get("planned", 0), "plan_duplicates": counts.get("plan-duplicate", 0),
        "plan_skipped": counts.get("plan-skip", 0), "total_scanned": sum(counts.values()),
    }


def years(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [{"year": r[0] or UNDATED, "count": r[1]} for r in conn.execute(
        "SELECT substr(taken, 1, 4), COUNT(*) FROM import_files "
        "WHERE status IN ('verified', 'planned') GROUP BY 1 ORDER BY 1")]


def recent(conn: sqlite3.Connection, status: str | None, limit: int = 60) -> list[dict[str, Any]]:
    where, params = "", []
    if status == "planned":
        where, params = "WHERE status IN ('planned', 'plan-duplicate', 'plan-skip')", []
    elif status:
        where, params = "WHERE status = ?", [status]
    return [{"filename": r["name"], "status": r["status"], "destination_path": r["destination"],
             "source_path": r["source"], "duplicate_of": r["duplicate_of"], "error": r["error"],
             "size": r["size"], "date": r["taken"], "date_source": r["date_source"]}
            for r in conn.execute(
                f"SELECT * FROM import_files {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                [*params, limit])]


def retry_errors(conn: sqlite3.Connection) -> int:
    with conn:
        return conn.execute("UPDATE import_files SET status = 'pending', error = NULL "
                            "WHERE status = 'error'").rowcount


def clear_report(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM import_files")
        conn.execute("DELETE FROM import_jobs")


def last_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM import_jobs ORDER BY id DESC LIMIT 1").fetchone()


def manifest_rows(conn: sqlite3.Connection):
    """(source, destination, size, sha256, status, date) for every file, for a CSV."""
    for r in conn.execute("SELECT source, destination, size, dest_hash, status, taken "
                          "FROM import_files ORDER BY destination, source"):
        yield r["source"], r["destination"] or "", r["size"], r["dest_hash"] or "", \
            r["status"], r["taken"] or ""
