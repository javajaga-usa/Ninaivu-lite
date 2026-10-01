"""Choosing photo folders: the picker's starting points, listing a folder, and
deciding whether a folder may be added (rules from Ninaivu's ``server/config``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .dates import long_path

#: Never a photo library: the system's own folders, and a whole system drive.
FORBIDDEN = {
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys",
    "/usr", "/var", "/opt", "/private", "/System", "/Library", "/Applications", "/Volumes",
    "c:\\", "c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\programdata",
}
FORBIDDEN_KEYS = {f.lower() if f[1:2] == ":" else os.path.normpath(f) for f in FORBIDDEN}
UNBROWSABLE = ("/proc", "/sys", "/dev")
MAX_LISTED = 1000

_DRIVE_REMOVABLE, _DRIVE_CDROM = 2, 5


def _key(path: str) -> str:
    """A comparable form: case-folded with backslashes on Windows."""
    text = os.path.normpath(os.path.abspath(path))
    if sys.platform == "win32" or (len(text) > 1 and text[1] == ":"):
        text = text.replace("/", "\\").lower()
        if len(text) == 2 and text[1] == ":":
            text += "\\"
    return text


def _windows_drives() -> list[str]:
    """Drive letters, without touching them: a sleeping network drive or an
    empty card reader would otherwise stall the picker."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        mask = kernel32.GetLogicalDrives()
    except Exception:  # noqa: BLE001 — not Windows
        return []
    out = []
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(ord('A') + index)}:\\"
            if kernel32.GetDriveTypeW(root) != _DRIVE_CDROM:
                out.append(root)
    return out


def starting_points() -> list[dict[str, str]]:
    """Where the picker begins: your Pictures and friends, then every drive."""
    home = Path.home()
    found: list[str] = []
    for name in ("Pictures", "Photos", "Videos", "Movies", "OneDrive", "Desktop", "Documents"):
        if (home / name).is_dir():
            found.append(str(home / name))
    found.append(str(home))
    if sys.platform == "win32":
        found.extend(_windows_drives())
    else:
        for mount in ("/Volumes", "/media", "/mnt", "/run/media", "/srv"):
            if os.path.isdir(mount):
                found.append(mount)
    seen, out = set(), []
    for path in found:
        if _key(path) not in seen:
            seen.add(_key(path))
            out.append({"path": path, "name": os.path.basename(path.rstrip("\\/")) or path})
    return out


def list_dirs(path: str) -> dict:
    """The folders inside *path*, for the picker. Hidden and system folders left out."""
    path = os.path.abspath(path)
    if any(_key(path) == u or _key(path).startswith(u + "/") for u in UNBROWSABLE):
        raise PermissionError(path)
    dirs = []
    with os.scandir(long_path(path)) as entries:
        for entry in entries:
            name = entry.name
            if name.startswith((".", "$")) or name in ("System Volume Information", "@eaDir"):
                continue
            try:
                if entry.is_dir():
                    dirs.append(name)
            except OSError:
                continue
            if len(dirs) >= MAX_LISTED:
                break
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None,
            "dirs": sorted(dirs, key=str.lower)}


def problem(path: str, data_dir: str, existing: list[str]) -> str | None:
    """Why *path* cannot be added (a sentence to show), or None."""
    if not path or not os.path.isabs(path):
        return "Give the full path of the folder, for example D:\\Photos."
    if not os.path.isdir(long_path(path)):
        return "That folder does not exist on this computer."
    key = _key(path)
    if key in FORBIDDEN_KEYS:
        return "That is a system folder. Choose the folder that holds your photographs."
    data = _key(data_dir)
    if key == data or key.startswith(data + os.sep):
        return "That is Ninaivu Lite's own data folder."
    for other in existing:
        o = _key(other)
        if key == o or key.startswith(o + os.sep):
            return "That folder is already in the library."
        if o.startswith(key + os.sep):
            return "That folder contains one that is already in the library. Remove the inner one first."
    return None
