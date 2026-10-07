"""One server per data folder, known to the operating system.

The server holds a lock on ``server.lock`` in the data folder for as long as
it runs. A restore looks at that lock rather than asking over the network,
which misses a server listening on another address (or in a container), and
a second server on the same folder would share the index and overwrite the
other's settings. The lock goes with the process, however it ends.
"""

from __future__ import annotations

import os
from pathlib import Path

LOCK_FILE = "server.lock"

_held: dict[str, object] = {}


def _try(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _open(data_dir: str | Path):
    path = Path(data_dir) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")       # noqa: SIM115 — held for the life of the server
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")          # Windows locks a byte that exists
        handle.flush()
    return handle


def acquire(data_dir: str | Path) -> bool:
    """Take the lock for this process; False if another process has it."""
    key = str(Path(data_dir).resolve())
    if key in _held:
        return True
    try:
        handle = _open(data_dir)
    except OSError:
        return True                  # a folder we cannot write: nothing to guard
    if not _try(handle):
        handle.close()
        return False
    _held[key] = handle
    return True


def release(data_dir: str | Path) -> None:
    handle = _held.pop(str(Path(data_dir).resolve()), None)
    if handle is not None:
        _release(handle)
        handle.close()


def held_elsewhere(data_dir: str | Path) -> bool:
    """Whether a server (another process) is running on this data folder."""
    if str(Path(data_dir).resolve()) in _held:
        return False
    if not (Path(data_dir) / LOCK_FILE).exists():
        return False
    try:
        handle = _open(data_dir)
    except OSError:
        return False
    try:
        if not _try(handle):
            return True
        _release(handle)
        return False
    finally:
        handle.close()


INSTANCE_FILE = "instance-id"


def instance_id(data_dir: str | Path) -> str:
    """A name for this data folder, made once: the server says it in
    /api/health, so a panel or a second start can tell its own library from
    another copy's (an installed one and a portable one on one computer)
    answering on the same port."""
    import secrets
    path = Path(data_dir) / INSTANCE_FILE
    try:
        found = path.read_text(encoding="ascii").strip()
        if found:
            return found
    except (OSError, ValueError):
        pass
    made = secrets.token_hex(8)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(made, encoding="ascii")
        os.replace(tmp, path)
    except OSError:
        pass
    return made


def known_instance(data_dir: str | Path) -> str | None:
    """This data folder's name if it has one yet (never makes one)."""
    try:
        return (Path(data_dir) / INSTANCE_FILE).read_text(encoding="ascii").strip() or None
    except (OSError, ValueError):
        return None
