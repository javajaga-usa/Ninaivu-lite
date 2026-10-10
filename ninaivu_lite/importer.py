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

import errno
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime
from typing import Any

from PIL import Image

from . import db, dates, media, parallel
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
#: Seconds between asking the library to look at what an import brought in.
SHOW_EVERY = 60


class Cancelled(Exception):
    """Stop was pressed."""


class Problem(Exception):
    """The run cannot go on, with a sentence for the console (see :func:`_say`)."""

    def __init__(self, said: dict[str, Any]) -> None:
        super().__init__(said["text"])
        self.said = said


class Incomplete(Problem):
    """The run went through what it could reach, but not everything it
    counted: a drive unplugged, a folder gone."""


#: Archives a source may hold that are left alone: a Google Takeout export
#: arrives as these and must be unpacked before its photographs can be found.
PACKED_EXTS = {".zip", ".tgz", ".tar", ".gz", ".7z", ".rar"}


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
    # The next megabyte is read while this one is hashed (parallel.chunks).
    with open(long_path(path), "rb") as f, \
            closing(parallel.chunks(f, CHUNK, gate.check if gate else None)) as read:
        for chunk in read:
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
    return dates.fallback_date(path, st, media.zone_near(path))


def still_done(row: sqlite3.Row, st: os.stat_result) -> bool:
    """Whether an earlier run's answer for a source still holds: the source is
    the file it was then (same size and modified time), and the archived copy
    it was filed as, or found already in the archive as, is still there at
    that size. A changed source or a lost copy is imported again; a copy
    whose bytes changed at the same size is for the audit (Verify) to find."""
    if row["size"] != st.st_size or row["mtime"] is None \
            or abs(row["mtime"] - st.st_mtime) >= 1:
        return False
    if row["status"] == "skipped":
        return True
    kept = row["destination"] if row["status"] == "verified" else row["duplicate_of"]
    try:
        return bool(kept) and os.stat(long_path(kept)).st_size == st.st_size
    except OSError:
        return False


def too_big(dest: str, size: int) -> bool:
    """Whether a "no room" failure for this file was its size, not the drive:
    there is clearly more room left than the file needed (FAT32 stops any
    one file at 4 GB and Windows calls that a full disk)."""
    folder = os.path.dirname(dest)
    while folder and not os.path.isdir(long_path(folder)):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    try:
        return shutil.disk_usage(folder).free > size + (1 << 20)
    except OSError:
        return False


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


ARCHIVE_NAME = "Ninaivu Archive"


def default_destination(library_root: str) -> str:
    """Where the archive is built unless the administrator chooses otherwise:
    inside the default library folder, so what comes in is indexed and shown
    to the family as it lands; without a library yet, under Pictures."""
    if library_root:
        return os.path.join(library_root, ARCHIVE_NAME)
    home = os.path.expanduser("~")
    pictures = os.path.join(home, "Pictures")
    return os.path.join(pictures if os.path.isdir(pictures) else home, ARCHIVE_NAME)


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


def _say(key: str, **vars: str) -> dict[str, Any]:
    """A sentence for the console: its English key, its values, and the two
    joined, so a Tamil console can translate the key and keep the path."""
    text = key
    for name, value in vars.items():
        text = text.replace("{" + name + "}", str(value))
    return {"key": key, "vars": vars, "text": text}


def validate(sources: list[str], destination: str, data_dir: str,
             library: list[str] = ()) -> list[dict[str, Any]]:
    """Why the job cannot start: sentences to show (see :func:`_say`), or []."""
    problems = []
    if not sources:
        problems.append(_say("Add at least one source folder."))
    if not destination:
        problems.append(_say("Choose a destination folder for the archive."))
    if problems:
        return problems
    for s in sources:
        if not os.path.isabs(s):
            problems.append(_say("Give the full path of the source folder, not “{path}”.", path=s))
        elif not os.path.isdir(long_path(s)):
            problems.append(_say("Source “{path}” is not a folder that can be opened.", path=s))
        elif any(is_within(s, root) for root in library if root):
            # Its photos are in the gallery already; archiving them beside the
            # library would show every one twice.
            problems.append(_say("Source “{path}” is in the library already. Importing it would "
                                 "show every photo twice: as it is, and as the archived copy.",
                                 path=s))
    if not os.path.isabs(destination):
        problems.append(_say("Give the full path of the destination folder, for example "
                             "D:\\Photo Archive."))
    elif os.path.exists(destination) and not os.path.isdir(destination):
        problems.append(_say("Destination exists but is not a folder: {path}", path=destination))
    elif is_within(destination, data_dir):
        problems.append(_say("The archive cannot be built inside Ninaivu Lite's own data folder."))
    for s in sources:
        if not os.path.isdir(long_path(s)):
            continue
        if norm(s) == norm(destination):
            problems.append(_say("Destination is the same folder as source “{path}”. "
                                 "The archive must be somewhere else.", path=s))
        elif is_within(s, destination):
            problems.append(_say("Source “{path}” is inside the destination, so all of it would "
                                 "be skipped as part of the archive. Choose a source outside it.",
                                 path=s))
    for a in sources:
        for b in sources:
            if a != b and os.path.isdir(a) and os.path.isdir(b) and norm(a) != norm(b) \
                    and is_within(a, b):
                problems.append(_say("Source “{path}” is already covered by source “{other}”.",
                                     path=a, other=b))
    return problems


def notices(sources: list[str], destination: str) -> list[dict[str, Any]]:
    out = []
    for s in sources:
        if destination and os.path.isdir(s) and norm(s) != norm(destination) \
                and is_within(destination, s):
            out.append(_say("The archive “{destination}” sits inside source “{path}”. It is "
                            "skipped during the scan, so files already archived are not read "
                            "back in.", destination=destination, path=s))
    return out


def said_text(message: Any) -> str:
    """The English of a sentence made by :func:`_say` (or a plain string)."""
    if isinstance(message, dict):
        return str(message.get("text", ""))
    return str(message or "")


def stored_said(message: str | None) -> Any:
    """A job's message as stored in import_jobs: a sentence from :func:`_say`
    as JSON, or plain English from before 1.6.1."""
    if message and message.startswith("{"):
        try:
            found = json.loads(message)
            if isinstance(found, dict) and "key" in found:
                return found
        except ValueError:
            pass
    return message or ""


def human_size(n: int) -> str:
    """'3.6 MB' below a gigabyte, '1.2 GB' above: '0.0 GB' says nothing."""
    if n >= 2 ** 30:
        return f"{n / 2 ** 30:.1f} GB"
    return f"{max(n, 0) / 2 ** 20:.1f} MB"


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
        #: Called with the destination as files land and when a run ends, so
        #: the library can look at them (set by the app).
        self.on_files = None

    # -- lifecycle --------------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, sources: list[str], destination: str, kinds: list[str], mode: str,
              library: list[str] = ()) -> None:
        """Begin a run. Raises ValueError with a sentence when it cannot.
        *library* is the library's folders: never walked as a source (their
        photographs are in the gallery already)."""
        with self._lock:
            if self.running:
                raise ValueError("A run is already going. Stop it first.")
            self.gate = Gate()
            self.job = {
                "mode": mode, "sources": list(sources), "destination": destination,
                "kinds": list(kinds), "phase": "counting", "message": "",
                "processed": 0, "stepped_over": 0, "total_files": 0, "total_bytes": 0,
                "bytes_copied": 0, "started_at": time.time(), "ended_at": None,
                "fresh_started": None, "unreadable": 0, "job_id": None, "plan_aside": {},
                "library": [f for f in library if f], "packed": 0,
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
            "job_message": said_text(j.get("message")),
            "job_said": j.get("message") or None,
            "destination": j.get("destination", ""),
            "processed": j.get("processed", 0),
            "stepped_over": j.get("stepped_over", 0),
            "total_files": j.get("total_files", 0),
            "total_bytes": j.get("total_bytes", 0),
            "bytes_copied": j.get("bytes_copied", 0),
            "eta_seconds": eta,
            "is_resume": bool(j.get("stepped_over")),
        }

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

    # -- one run --------------------------------------------------------------------

    def _run(self) -> None:
        parallel.background()      # the gallery first; the import gets the rest
        try:
            conn = db.connect(self.data_dir)
        except Exception as exc:  # noqa: BLE001 — said on the console, not left "counting"
            log.exception("import could not open the index")
            self._set(phase="failed", ended_at=time.time(),
                      message=_say("The run stopped with an error: {why}", why=str(exc)))
            return
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
        except Incomplete as exc:
            self._set(message=exc.said)
            self._finish(conn, job_id, "incomplete")
        except Problem as exc:
            self._set(message=exc.said)
            self._finish(conn, job_id, "failed")
        except Exception as exc:  # noqa: BLE001 — the thread must end tidily
            log.exception("import failed")
            self._set(message=_say("The run stopped with an error: {why}", why=str(exc)))
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
        phase = {"completed": "done", "stopped": "stopped", "failed": "failed",
                 "incomplete": "incomplete"}[state]
        if state == "completed":
            message = self._summary(conn)
        elif state == "stopped":
            message = _say("Stopped. Press Start to carry on where it left off.")
        else:
            message = j.get("message") or _say("The run failed.")
        self._set(phase=phase, message=message, ended_at=time.time())
        self._sweep_partials()
        if job_id is not None:
            with conn:
                conn.execute(
                    "UPDATE import_jobs SET state = ?, phase = ?, ended_at = ?, message = ?, "
                    "total_files = ?, total_bytes = ?, bytes_copied = ? WHERE id = ?",
                    ("stopped" if state == "incomplete" else state, phase, time.time(),
                     json.dumps(message, ensure_ascii=False), j["total_files"],
                     j["total_bytes"], j["bytes_copied"], job_id))
        log.info("import %s: %s", state, message["text"])
        if j["mode"] != "verify" and self.on_files is not None:
            self.on_files(j["destination"])

    def _summary(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """How the run went, as a sentence the console can translate: a key
        made of the parts that apply, and their numbers."""
        j = self.job
        if j["mode"] == "verify":
            errors = conn.execute("SELECT COUNT(*) FROM import_files WHERE status = 'error' "
                                  "AND job_id = ?", (j["job_id"],)).fetchone()[0]
            return (_say("Audit finished: every archived file matches its hash.") if not errors
                    else _say("Audit finished: {errors} archived files did not match or are "
                              "missing.", errors=f"{errors:,}"))
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM import_files WHERE job_id = ? GROUP BY status",
            (j["job_id"],))}
        parts: list[str] = []
        values: dict[str, Any] = {}
        if j["mode"] == "dry-run":
            for status, n in j.get("plan_aside", {}).items():
                counts[status] = counts.get(status, 0) + n
            parts.append("Dry run finished: {planned} files would be archived, {duplicates} are "
                         "duplicates. Nothing was written.")
            values.update(planned=f"{counts.get('planned', 0):,}",
                          duplicates=f"{counts.get('plan-duplicate', 0):,}")
        else:
            parts.append("Finished: {verified} files archived and verified, {duplicates} "
                         "duplicates left in place." if not j["stepped_over"] else
                         "Finished: {verified} files archived and verified, {duplicates} "
                         "duplicates left in place, {done} already done.")
            values.update(verified=f"{counts.get('verified', 0):,}",
                          duplicates=f"{counts.get('duplicate', 0):,}",
                          done=f"{j['stepped_over']:,}")
        if counts.get("error"):
            parts.append("{errors} could not be archived.")
            values["errors"] = f"{counts['error']:,}"
        if j.get("unreadable"):
            parts.append("{unreadable} folders could not be read.")
            values["unreadable"] = f"{j['unreadable']:,}"
        if j.get("packed"):
            parts.append("{packed} zip or other packed files were left alone: unpack them (a "
                         "Google Takeout export, say) and import the folder.")
            values["packed"] = f"{j['packed']:,}"
        said = [_say(part, **{k: v for k, v in values.items() if "{" + k + "}" in part})
                for part in parts]
        return {"key": said[0]["key"], "vars": said[0]["vars"],
                "text": " ".join(x["text"] for x in said), "more": said[1:]}

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
                  packed=counters.get("packed", 0))
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
            when = taken.strftime("%Y-%m-%d %H:%M:%S") if taken else None
            src_hash = None
            # Hash up front only when it can settle a duplicate now; otherwise
            # the hash is taken while copying, which reads the file once.
            if dry or self._size_known(conn, st.st_size):
                src_hash = hash_file(src, self.gate)
                dup = self._duplicate_of(conn, src_hash, src, st.st_size)
                if dup:
                    mark("plan-duplicate" if dry else "duplicate",
                         hash=src_hash, taken=when, date_source=source, duplicate_of=dup)
                    return
            if dry:
                candidate, identical = self._unique_path(conn, folder, name, src_hash,
                                                         st.st_size, planning=True, source=src)
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
                     size: int, planning: bool = False, source: str = "") -> tuple[str, bool]:
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
                    "SELECT 1 FROM import_files WHERE destination = ? AND status = 'planned' "
                    "AND source != ?", (candidate, source)).fetchone()):
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
                if os.path.lexists(long_path(target)):
                    raise FileExistsError(target)
                os.replace(long_path(partial), long_path(target))
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
