"""What an import is made of: its rules and constants, what a file is and
when it was taken, where a source and an archive may be, the checks a job
passes before it starts, and the gate that pauses and stops it.

The engine itself is :mod:`importer`, which walks the sources
(:mod:`import_walk`) and copies and checks every file (:mod:`import_copy`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
from contextlib import closing
from datetime import datetime
from typing import Any

from PIL import Image

from . import dates, media, parallel
from .dates import long_path

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
#: A Live Photo: a still and its short video of the same name (see scanner._pair_live).
LIVE_STILL_EXTS = {".heic", ".heif", ".jpg", ".jpeg"}
LIVE_CLIP_EXTS = {".mov", ".mp4"}
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


def publish(tmp: str, final: str) -> bool:
    """Move a finished copy *tmp* to *final*, never over a file there: False,
    and nothing moved, when one is. A hard link (or, on Windows, a rename)
    refuses an existing name in the same step; a drive without hard links
    (FAT, exFAT, some network drives) is checked just before instead."""
    if sys.platform == "win32":
        try:
            os.rename(long_path(tmp), long_path(final))
        except FileExistsError:
            return False
        return True
    try:
        os.link(long_path(tmp), long_path(final))
    except FileExistsError:
        return False
    except OSError:
        if os.path.lexists(long_path(final)):
            return False
        os.replace(long_path(tmp), long_path(final))
        return True
    os.remove(long_path(tmp))
    return True


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
