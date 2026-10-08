"""How much of the computer Ninaivu Lite may use at once, and the two ways it
does work side by side.

* :data:`WORKERS` threads look at and shrink photographs during a scan: one
  for every core but one, so the gallery always has a core of its own, and no
  more than half the memory holds at :data:`PER_WORKER_BYTES` each.
  A Raspberry Pi with four cores gets three, an eight-core laptop seven, a
  one- or two-core computer one, which is how it always worked.
* :func:`chunks` reads a file one chunk ahead on a second thread, so a copy
  or a check reads the next megabyte while the last one is being hashed or
  written. The file is still read once, start to end, in order: a slow USB
  stick or a sleeping hard disk is asked for nothing it was not asked for
  before, only sooner.

Pillow, OpenCV, ``hashlib`` and file reads and writes all let go of Python's
lock while they work, so these threads really do run on separate cores.
Only reading happens side by side: the index is written by one thread, and
nothing here ever writes to, moves or deletes a photograph.

Work nobody is waiting for (a scan, an import, a copy to a drive, a backup)
runs at a lower priority (:func:`background`), so when the computer is busy
the gallery, a guest's page and the Control Panel are served first.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import IO, Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: More than this many at once gains nothing a household library would notice
#: and makes the one thread that writes the index the bottleneck.
MAX_WORKERS = 16
#: Memory set aside for each worker. A phone photograph is read at a quarter
#: or an eighth of its size and needs a few megabytes; a picture just under
#: ``media.HEAVY_PIXELS`` (16 MP) read whole needs about 50 MB plus the copy
#: being shrunk. Bigger ones are made one at a time whatever this says.
PER_WORKER_BYTES = 128 * 1024 ** 2


def cores() -> int:
    """The cores this program may run on (fewer than the computer has when it
    is limited to some of them, as in a container or a Pi's service)."""
    count = getattr(os, "process_cpu_count", None)
    if count is not None:
        return count() or 1
    try:
        return len(os.sched_getaffinity(0)) or 1     # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def memory() -> int | None:
    """The computer's memory in bytes, or None when it cannot be told."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.total)
            return None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def workers(core_count: int | None = None, memory_bytes: int | None = None) -> int:
    """Threads for scan work on a computer with *core_count* cores and
    *memory_bytes* of memory: a core left free, half the memory at most, never more than :data:`MAX_WORKERS`, always at least one."""
    core_count = cores() if core_count is None else core_count
    count = min(MAX_WORKERS, core_count - 1)
    if memory_bytes:
        count = min(count, memory_bytes // 2 // PER_WORKER_BYTES)
    return max(1, count)


#: Worked out once, at start. Tests set it by hand.
WORKERS = workers(memory_bytes=memory())

#: How much lower than the program a background thread runs on Linux (a
#: "nice" of 10 more, at most 19).
BACKGROUND_NICE = 10
#: Windows: THREAD_PRIORITY_BELOW_NORMAL, and for ffmpeg started from such a
#: thread, BELOW_NORMAL_PRIORITY_CLASS.
_WIN_BELOW_NORMAL = -1
WIN_BELOW_NORMAL_CLASS = 0x00004000
#: macOS: QOS_CLASS_UTILITY, for long work the person can see but is not waiting on.
_MAC_UTILITY = 0x11
_marks = threading.local()


def background() -> None:
    """Run the calling thread, and on Linux and macOS the programs it starts,
    below the gallery's priority. Only ever lowers it, which needs no special
    rights; never fails. Called first thing by every background thread and
    worker."""
    _marks.background = True
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32                     # type: ignore[attr-defined]
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), _WIN_BELOW_NORMAL)
        elif sys.platform == "darwin":
            import ctypes

            libc = ctypes.CDLL(None)
            libc.pthread_set_qos_class_self_np(_MAC_UTILITY, 0)
        elif hasattr(os, "setpriority"):
            # On Linux a thread has its own niceness: this one only. The
            # program's own is the base, so a thread started by a background
            # thread (which inherits its niceness) is not lowered twice.
            base = os.getpriority(os.PRIO_PROCESS, os.getpid())
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(),
                           min(19, base + BACKGROUND_NICE))
    except Exception:  # noqa: BLE001 — a priority is a nicety, never a reason to stop
        pass


def in_background() -> bool:
    """Whether the calling thread has been through :func:`background`."""
    return getattr(_marks, "background", False)


def pool(width: int, name: str) -> ThreadPoolExecutor:
    """*width* background worker threads."""
    return ThreadPoolExecutor(width, thread_name_prefix=name, initializer=background)


def in_order(items: Iterable[T], work: Callable[[T], R], pool: ThreadPoolExecutor | None,
             width: int) -> Iterator[tuple[T, R]]:
    """(item, ``work(item)``) for each item, in the order given, *width* at a
    time on *pool* (or one by one on this thread without one). Closed early,
    the ones not started are dropped; at most *width* finish unrecorded."""
    if pool is None:
        for item in items:
            yield item, work(item)
        return
    window: deque = deque()
    try:
        for item in items:
            window.append((item, pool.submit(work, item)))
            # One more waiting than there are workers keeps every worker busy
            # while the caller writes the oldest one down.
            if len(window) > width:
                first, future = window.popleft()
                yield first, future.result()
        while window:
            first, future = window.popleft()
            yield first, future.result()
    finally:
        for _item, future in window:
            future.cancel()


def chunks(f: IO[bytes], size: int, check: Callable[[], Any] | None = None) -> Iterator[bytes]:
    """The file *f* in chunks of *size*, the next one read on a second thread
    while the caller works on this one. *check* is called before each chunk
    and may raise to stop. A file of one chunk, or a one-core computer, is
    read on this thread alone: a thread would cost more than it saves."""
    try:
        small = os.fstat(f.fileno()).st_size <= size
    except (OSError, AttributeError, ValueError):
        small = False
    if small or cores() < 2:
        while True:
            if check:
                check()
            chunk = f.read(size)
            if not chunk:
                return
            yield chunk
    with ThreadPoolExecutor(1, thread_name_prefix="read-ahead",
                            initializer=background if in_background() else None) as reader:
        # Leaving this block (finished, stopped or failed) waits for the one
        # read still running, so the file is never closed under it.
        ahead = reader.submit(f.read, size)
        while True:
            if check:
                check()
            chunk = ahead.result()
            if not chunk:
                return
            ahead = reader.submit(f.read, size)
            yield chunk
