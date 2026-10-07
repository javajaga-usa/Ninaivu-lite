"""Pendrives, external hard drives and phones on a cable: which are plugged
in now, and copying the library onto a drive.

    connected()      the drives a person carried in: USB sticks, memory cards,
                     external hard drives, phones (see phones.py); never the
                     computer's own disks
    Watcher          which of them nobody has been asked about yet
    Exporter         the library's photos and videos onto one, in a thread

Standard library only. On Windows the drive letters come from kernel32 and
each drive is asked which bus it hangs off, so a USB hard drive (which Windows
calls "fixed", like the disk inside) is still known as one that was carried
in. Windows' "insert a disk" box is turned off while asking, because an empty
card reader would otherwise raise it. On macOS a drive is a folder in
/Volumes; on Linux, one under /media or /run/media.

Nothing here is written to the drive except by the Exporter, which only adds:
it never deletes or overwrites a file that is already there.
"""

from __future__ import annotations

import hashlib
import logging
import errno
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from .dates import long_path

# The Control Panel imports this module, so the importer (and with it Pillow)
# is loaded only by the export, which runs in the server.

log = logging.getLogger(__name__)

#: The folder on the drive that an export goes into.
EXPORT_FOLDER = "Ninaivu Lite"
#: How long one reading of the drives is good for: the console and the panel
#: ask every few seconds, and asking Windows about every letter is not free.
CACHE_SECONDS = 2.0
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Drive:
    id: str          # stable while the same drive stays plugged in
    path: str        # its root: E:\ or /Volumes/NAME
    label: str       # its name, or the path when it has none
    total: int
    free: int
    #: "drive", or "phone" for a phone on a USB cable
    kind: str = "drive"
    #: a phone Windows shows only in Explorer (MTP): no path to open, so its
    #: photos are fetched through the Windows shell (phones.py)
    shell: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def is_within(child: str, parent: str) -> bool:
    """As importer.is_within, without loading the importer."""
    def norm(path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _say(key: str, **vars: str) -> dict[str, Any]:
    from .importer import _say as say
    return say(key, **vars)


def _drive_id(path: str, label: str, serial: str | int, total: int) -> str:
    raw = f"{path}|{label}|{serial}|{total}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:16]


def _usage(path: str) -> tuple[int, int]:
    try:
        u = shutil.disk_usage(path)
        return u.total, u.free
    except OSError:
        return 0, 0


# --- Windows -------------------------------------------------------------------------

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
#: STORAGE_BUS_TYPE values of a drive that was plugged in: 1394, USB, SD, MMC.
CARRIED_BUSES = {4, 7, 12, 13}
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
SEM_FAILCRITICALERRORS = 0x0001


def _windows_bus(letter: str) -> int | None:
    """Which bus the drive hangs off (STORAGE_DEVICE_DESCRIPTOR.BusType), or None.

    Opened with no access rights, which needs no administrator and does not
    wake a sleeping disk."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = wintypes.HANDLE(-1).value
    handle = kernel32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)
    if not handle or handle == invalid:
        return None
    try:
        query = (ctypes.c_uint32 * 3)(0, 0, 0)  # StorageDeviceProperty, PropertyStandardQuery
        out = (ctypes.c_ubyte * 1024)()
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(handle, IOCTL_STORAGE_QUERY_PROPERTY,
                                      ctypes.byref(query), ctypes.sizeof(query),
                                      ctypes.byref(out), ctypes.sizeof(out),
                                      ctypes.byref(returned), None)
        if not ok or returned.value < 32:
            return None
        return int.from_bytes(bytes(out[28:32]), "little")
    finally:
        kernel32.CloseHandle(handle)


def _windows_drives() -> list[Drive]:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    # No "There is no disk in the drive" box for an empty card reader.
    old_mode = ctypes.c_uint(0)
    try:
        set_mode = kernel32.SetThreadErrorMode
        set_mode(SEM_FAILCRITICALERRORS, ctypes.byref(old_mode))
        restore = lambda: set_mode(old_mode.value, None)  # noqa: E731
    except AttributeError:
        previous = kernel32.SetErrorMode(SEM_FAILCRITICALERRORS)
        restore = lambda: kernel32.SetErrorMode(previous)  # noqa: E731
    system = (os.environ.get("SystemDrive") or "C:")[:1].upper()
    found = []
    try:
        mask = kernel32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            letter = chr(ord("A") + i)
            if letter in "AB" or letter == system:
                continue
            root = f"{letter}:\\"
            kind = kernel32.GetDriveTypeW(root)
            if kind == DRIVE_FIXED:
                try:
                    carried = _windows_bus(letter) in CARRIED_BUSES
                except Exception:  # noqa: BLE001 — unknown is "the computer's own"
                    carried = False
                if not carried:
                    continue
            elif kind != DRIVE_REMOVABLE:
                continue
            name = ctypes.create_unicode_buffer(261)
            serial = ctypes.c_uint32(0)
            # Fails for an empty card reader: nothing to ask about.
            if not kernel32.GetVolumeInformationW(root, name, 261, ctypes.byref(serial),
                                                  None, None, None, 0):
                continue
            total, free = _usage(root)
            label = name.value.strip()
            found.append(Drive(_drive_id(root, label, serial.value, total), root,
                               label or root, total, free))
    finally:
        restore()
    return found


# --- macOS and Linux -----------------------------------------------------------------


def _mount_drives(parents: list[str]) -> list[Drive]:
    found = []
    seen = set()
    for parent in parents:
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            continue
        for name in names:
            path = os.path.join(parent, name)
            if name.startswith(".") or not os.path.isdir(path):
                continue
            real = os.path.realpath(path)
            # macOS lists its own disk in /Volumes too, as a link to /.
            if real == "/" or real in seen or not os.path.ismount(real):
                continue
            seen.add(real)
            try:
                serial = os.stat(real).st_dev
            except OSError:
                continue
            total, free = _usage(real)
            found.append(Drive(_drive_id(real, name, serial, total), real, name, total, free))
    return found


#: Below this, a Linux account is a system one (useradd --system): the
#: service's own account, which has no desktop and no /media folder of its own.
SYSTEM_UID_MAX = 999


def _system_account() -> bool:
    return sys.platform.startswith("linux") and hasattr(os, "geteuid") \
        and os.geteuid() <= SYSTEM_UID_MAX


def _subfolders(parent: str) -> list[str]:
    try:
        return [os.path.join(parent, n) for n in sorted(os.listdir(parent))
                if not n.startswith(".")]
    except OSError:
        return []


def _gvfs_phones(parent: str | None = None) -> list[Drive]:
    """Linux: a phone on a cable, opened by the desktop (GNOME's gvfs) under
    /run/user/<uid>/gvfs as mtp:host=… or gphoto2:host=…: plain folders. The
    system service looks in every desktop user's, where it is let in."""
    if parent is None:
        if not hasattr(os, "getuid"):
            return []
        if _system_account():
            return [p for user in _subfolders("/run/user")
                    for p in _gvfs_phones(os.path.join(user, "gvfs"))]
        parent = f"/run/user/{os.getuid()}/gvfs"
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    found = []
    for name in names:
        if not name.startswith(("mtp:", "gphoto2:")):
            continue
        path = os.path.join(parent, name)
        label = name.split("=", 1)[-1].replace("_", " ") or name
        found.append(Drive(_drive_id(path, label, "phone", 0), path, label, 0, 0, kind="phone"))
    return found


def _posix_parents() -> list[str]:
    if sys.platform == "darwin":
        return ["/Volumes"]
    if _system_account():
        # The system service (a root install) is nobody's desktop: the drives
        # are mounted for whoever is signed in, under /media/<them> or
        # /run/media/<them>, so every such folder is looked in.
        return [*_subfolders("/media"), *_subfolders("/run/media"), "/media"]
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    parents = [os.path.join("/media", user), os.path.join("/run/media", user)] if user else []
    parents.append("/media")
    return parents


def connected() -> list[Drive]:
    """The drives plugged in now; [] when they cannot be read."""
    try:
        if sys.platform == "win32":
            from . import phones
            return _windows_drives() + phones.listed()
        found = _mount_drives(_posix_parents())
        if sys.platform.startswith("linux"):
            found += _gvfs_phones()
        return found
    except Exception:  # noqa: BLE001 — a prompt is a nicety; never break the caller
        log.exception("could not list the drives")
        return []


# --- who has been asked ----------------------------------------------------------------


class Watcher:
    """The drives plugged in, and which of them still need the question.

    A drive is asked about once each time it is plugged in: answered (or set
    aside with Not now), it is not asked about again until it is taken out
    and put back."""

    def __init__(self, lister=connected) -> None:
        self.lister = lister
        self.lock = threading.Lock()
        self.answered: set[str] = set()
        self.cached: list[Drive] = []
        self.read_at = 0.0

    def drives(self) -> list[Drive]:
        with self.lock:
            now = time.monotonic()
            if now - self.read_at >= CACHE_SECONDS:
                self.cached = self.lister()
                self.read_at = now
                present = {d.id for d in self.cached}
                self.answered &= present     # taken out: ask again next time
            return list(self.cached)

    def find(self, drive_id: str) -> Drive | None:
        return next((d for d in self.drives() if d.id == drive_id), None)

    def pending(self) -> list[Drive]:
        drives = self.drives()
        with self.lock:
            return [d for d in drives if d.id not in self.answered]

    def answer(self, drive_id: str) -> None:
        with self.lock:
            self.answered.add(drive_id)


# --- copying the library onto a drive -------------------------------------------------------


def export_root(drive_path: str) -> str:
    return os.path.join(drive_path, EXPORT_FOLDER)


def _targets(folders: list[str], root: str) -> list[tuple[str, str]]:
    """Each library folder and where it goes on the drive: by its own name,
    with a number added when two folders share one."""
    out, used = [], set()
    for folder in folders:
        base = os.path.basename(os.path.normpath(folder)) or "Library"
        base = base.rstrip(":\\/") or "Library"
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} ({n})", n + 1
        used.add(name.lower())
        out.append((folder, os.path.join(root, name)))
    return out


class Exporter:
    """One export at a time, on its own thread; the console polls progress()."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.state: dict[str, Any] = self._blank()

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {"running": False, "phase": "", "drive": "", "destination": "", "total": 0,
                "done": 0, "copied": 0, "skipped": 0, "errors": 0, "bytes_total": 0,
                "bytes_done": 0, "message": None, "finished_at": None}

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def progress(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.state, running=self.running)

    def _set(self, **values: Any) -> None:
        with self.lock:
            self.state.update(values)

    def start(self, drive: Drive, folders: list[str], data_dir: str) -> None:
        if self.running:
            raise RuntimeError("busy")
        self.cancel.clear()
        root = export_root(drive.path)
        self.state = {**self._blank(), "running": True, "phase": "counting",
                      "drive": drive.label, "destination": root}
        self.thread = threading.Thread(target=self._run, args=(list(folders), root, data_dir),
                                       name="drive-export", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.cancel.set()

    def wait(self, timeout: float = 60) -> None:
        if self.thread is not None:
            self.thread.join(timeout)

    def _walk(self, folders: list[str], root: str, data_dir: str):
        """(source, target, size) for every photo and video in the library.
        A folder or file that cannot be read is logged and counted as an
        error, not left out without a word."""
        from .importer import IGNORE_DIRS, kind_by_extension
        for folder, target in _targets(folders, root):
            stack = [folder]
            while stack:
                here = stack.pop()
                try:
                    with os.scandir(long_path(here)) as it:
                        entries = sorted(it, key=lambda e: e.name)
                except OSError as exc:
                    log.warning("export: could not read %s: %s", here, exc)
                    self._error()
                    continue
                rel = os.path.relpath(here, folder)
                subdirs = []
                for entry in entries:
                    name, src = entry.name, os.path.join(here, entry.name)
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            # Linked folders are listed but not followed, as os.walk does.
                            if not entry.is_symlink() and name not in IGNORE_DIRS \
                                    and not is_within(src, root) and not is_within(src, data_dir):
                                subdirs.append(src)
                            continue
                        if not kind_by_extension(name):
                            continue
                        size = os.stat(long_path(src)).st_size
                    except OSError as exc:
                        log.warning("export: could not read %s: %s", src, exc)
                        self._error()
                        continue
                    yield src, os.path.normpath(os.path.join(target, rel, name)), size
                stack.extend(reversed(subdirs))

    def _error(self) -> None:
        with self.lock:
            self.state["errors"] += 1

    def _run(self, folders: list[str], root: str, data_dir: str) -> None:
        try:
            plan = []
            for item in self._walk(folders, root, data_dir):
                if self.cancel.is_set():
                    raise InterruptedError
                plan.append(item)
            # What is already there (same name, same size) is not copied again,
            # so an export to the same drive next month only adds what is new.
            todo, claimed, skipped = [], set(), 0
            for src, dest, size in plan:
                try:
                    place = _place(dest, size, claimed)
                except OSError as exc:
                    log.warning("export: could not check %s: %s", dest, exc)
                    self._error()
                    continue
                if place:
                    todo.append((src, place, size))
                else:
                    skipped += 1
            need = sum(size for _, _, size in todo)
            self._set(phase="copying", total=len(plan), skipped=skipped,
                      done=len(plan) - len(todo), bytes_total=need)
            try:
                free = shutil.disk_usage(os.path.dirname(root) or root).free
            except OSError:
                free = None
            # A drive with nothing free (0) is the fullest of all, not unknown.
            if need and free is not None and need > free:
                self._set(phase="failed", message=_say(
                    "Not enough room on the drive: {need} needed, {free} free.",
                    need=_size(need), free=_size(free)))
                return
            for src, dest, size in todo:
                if self.cancel.is_set():
                    raise InterruptedError
                try:
                    _copy(src, dest, self.cancel)
                    with self.lock:
                        self.state["copied"] += 1
                except InterruptedError:
                    raise
                except OSError as exc:
                    log.warning("export: could not copy %s: %s", src, exc)
                    if exc.errno == errno.ENOSPC:
                        self._set(phase="failed", message=_say(
                            "The drive is full. What was copied stays on it; make room and "
                            "copy again to finish."))
                        return
                    with self.lock:
                        self.state["errors"] += 1
                with self.lock:
                    self.state["done"] += 1
                    self.state["bytes_done"] += size
            s = self.progress()
            counts = {"copied": f"{s['copied']:,}", "skipped": f"{s['skipped']:,}",
                      "errors": f"{s['errors']:,}"}
            if s["errors"]:
                key = ("Copied {copied} photos and videos to the drive. {errors} could not be "
                       "copied; see the log.")
            elif s["skipped"]:
                key = "Copied {copied} photos and videos to the drive. {skipped} were there already."
            else:
                key = "Copied {copied} photos and videos to the drive."
            # Taken out without ejecting, a drive can lose what was written last.
            key += " Eject the drive before you unplug it."
            self._set(phase="done", message=_say(key, **counts))
        except InterruptedError:
            self._set(phase="stopped", message=_say("Stopped. What was copied stays on the drive."))
        except OSError as exc:
            log.warning("export to %s failed: %s", root, exc)
            self._set(phase="failed", message=_say(
                "Could not write to the drive. Is it still plugged in, and not read-only?"))
        finally:
            self._set(running=False, finished_at=time.time())


def _place(dest: str, size: int, claimed: set[str] | None = None) -> str | None:
    """Where a file of *size* goes: *dest*, or "name (2).jpg" when a different
    file already has that name; None when it is there already.

    *claimed* holds the names this export has already settled on, compared
    without case: on a FAT or exFAT drive (most pendrives) IMG_1.JPG and
    img_1.jpg are one file, and the second would overwrite the first. Only
    a name that is not there is free; any other error is raised."""
    claimed = set() if claimed is None else claimed
    stem, ext = os.path.splitext(dest)
    for n in range(1, 1000):
        candidate = dest if n == 1 else f"{stem} ({n}){ext}"
        key = os.path.normcase(candidate).lower()
        if key in claimed:
            continue
        try:
            there = os.stat(long_path(candidate)).st_size == size
        except FileNotFoundError:
            claimed.add(key)
            return candidate
        if there:
            claimed.add(key)
            return None
    raise OSError(f"No free name for {dest}")


def _copy(src: str, dest: str, cancel: threading.Event) -> None:
    """Copy through a .partial file, so a drive pulled out mid-copy leaves no
    half photo under the real name; then keep the original's dates."""
    folder = os.path.dirname(dest)
    os.makedirs(long_path(folder), exist_ok=True)
    tmp = dest + ".partial"
    try:
        with open(long_path(src), "rb") as fin, open(long_path(tmp), "wb") as fout:
            while True:
                if cancel.is_set():
                    raise InterruptedError
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
            # On the drive itself, not in the computer's write cache: a drive
            # pulled out after "Copied" must not hold a damaged photo that
            # the next copy takes as already there.
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(long_path(src), long_path(tmp))
        os.replace(long_path(tmp), long_path(dest))
    except BaseException:
        try:
            os.remove(long_path(tmp))
        except OSError:
            pass
        raise


def _size(n: int) -> str:
    value = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "bytes" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{n} bytes"
