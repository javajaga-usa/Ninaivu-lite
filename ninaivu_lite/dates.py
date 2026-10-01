"""When was this taken? (taken from Ninaivu's ``archive/dates.py``)

Evidence, strongest first:

1. EXIF ``DateTimeOriginal`` and friends (read in :mod:`media`).
2. The recording date inside a video: the QuickTime/MP4 ``mvhd`` header,
   Apple's ``com.apple.quicktime.creationdate``, or an AVI ``IDIT``/``ICRD``
   chunk. Read directly, so it works without ffmpeg.
3. A date written in the file name: ``IMG_20190512_123456``,
   ``IMG-20190512-WA0001`` (WhatsApp strips EXIF), ``PXL_…``, ``Screenshot_…``.
4. The file's own timestamp, the earlier of modified and created.

Each is checked for plausibility first: nothing before 1900 (1990 for clocks a
machine set) and nothing after tomorrow. Standard library only.
"""

from __future__ import annotations

import calendar
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone

MIN_YEAR = 1900
MIN_FS_YEAR = 1990
_FUTURE_SLACK = timedelta(days=1)
_EPOCH = datetime(1970, 1, 1)


def long_path(path: str) -> str:
    r"""Windows' ``\\?\`` prefix, so paths over 260 characters still open."""
    if sys.platform != "win32":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def latest_plausible() -> datetime:
    return datetime.now() + _FUTURE_SLACK


def plausible(dt: datetime | None, floor: int = MIN_YEAR) -> bool:
    """Could a real capture have this date? Future dates are dead clocks."""
    return dt is not None and dt.year >= floor and dt <= latest_plausible()


def _offset_at_epoch() -> timedelta:
    return datetime.fromtimestamp(86400) - (_EPOCH + timedelta(days=1))


def to_timestamp(dt: datetime) -> float:
    """POSIX timestamp for a naive local time, including before 1970 on Windows."""
    try:
        return dt.timestamp()
    except (OSError, OverflowError, ValueError):
        return (dt - _offset_at_epoch() - _EPOCH).total_seconds()


def from_timestamp(ts: float) -> datetime:
    """Naive local time for a POSIX timestamp, including negative ones."""
    try:
        return datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return _EPOCH + _offset_at_epoch() + timedelta(seconds=ts)


def _from_timestamp(ts: float | None, floor: int) -> datetime | None:
    if not ts:
        return None
    try:
        dt = from_timestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return dt if plausible(dt, floor) else None


def parse_exif_datetime(raw: object) -> float | None:
    """An EXIF date string as a timestamp, or None if absent or implausible."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip().strip("\x00").split(".")[0]
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            when = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return to_timestamp(when) if plausible(when) else None
    return None


def file_time(st: os.stat_result) -> float:
    """The earliest timestamp the file carries.

    Edited in place keeps its creation time; copied keeps its modification
    time. The older of the two is honest in both cases.
    """
    candidates = [st.st_mtime]
    for name in ("st_birthtime", "st_ctime"):
        value = getattr(st, name, None)
        if value:
            candidates.append(float(value))
    return min(c for c in candidates if c and c > 0)


# --- inside the file: MP4 / MOV / 3GP, and AVI -----------------------------

_QT_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
_ISO_TOP_LEVEL = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot", b"uuid"}
_STILL_BRANDS = (b"hei", b"avi", b"mif", b"msf", b"crx")
_APPLE_CREATION_KEY = b"com.apple.quicktime.creationdate"
_MAX_BOXES = 4096
_MAX_META_BYTES = 1024 * 1024


def container_date(path: str) -> datetime | None:
    """The recording date stored inside a video container, or None."""
    try:
        with open(long_path(os.fspath(path)), "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return None
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if head[4:8] in _ISO_TOP_LEVEL:
                if head[4:8] == b"ftyp" and head[8:11] in _STILL_BRANDS:
                    return None
                return _iso_date(f, size)
            if head[:4] == b"RIFF" and head[8:12] in (b"AVI ", b"AVIX"):
                return _avi_date(f, size)
    except (OSError, ValueError, struct.error):
        pass
    return None


def _boxes(f, start, end):
    pos, count = start, 0
    while pos + 8 <= end and count < _MAX_BOXES:
        count += 1
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        body = pos + 8
        if size == 1:
            large = f.read(8)
            if len(large) < 8:
                return
            size = struct.unpack(">Q", large)[0]
            body = pos + 16
        elif size == 0:
            size = end - pos
        if size < body - pos or pos + size > end:
            return
        yield kind, body, pos + size
        pos += size


def _iso_date(f, size):
    for kind, body, end in _boxes(f, 0, size):
        if kind == b"moov":
            return _moov_date(f, body, end)
    return None


def _moov_date(f, start, end):
    header_time = None
    apple = None
    for kind, body, box_end in _boxes(f, start, end):
        if kind == b"mvhd":
            f.seek(body)
            data = f.read(min(box_end - body, 32))
            if len(data) >= 12 and data[0] == 1:
                header_time = struct.unpack(">Q", data[4:12])[0]
            elif len(data) >= 8:
                header_time = struct.unpack(">I", data[4:8])[0]
        elif kind == b"meta":
            apple = apple or _apple_creation_date(f, body, box_end)
        elif kind == b"udta":
            for inner, inner_body, inner_end in _boxes(f, body, box_end):
                if inner == b"meta":
                    apple = apple or _apple_creation_date(f, inner_body, inner_end)
    # Apple's key is local wall-clock time, like EXIF; the movie header is UTC.
    if plausible(apple, MIN_FS_YEAR):
        return apple
    if header_time:
        try:
            utc = _QT_EPOCH + timedelta(seconds=header_time)
        except OverflowError:
            return None
        if utc.year >= MIN_FS_YEAR:
            return _from_timestamp(utc.timestamp(), MIN_FS_YEAR)
    return None


def _apple_creation_date(f, start, end):
    if end - start > _MAX_META_BYTES:
        return None
    f.seek(start)
    peek = f.read(8)
    if peek[4:8] != b"hdlr":
        start += 4
    index = None
    values = {}
    for kind, body, box_end in _boxes(f, start, end):
        if kind == b"keys":
            f.seek(body)
            data = f.read(box_end - body)
            if len(data) < 8:
                continue
            count = struct.unpack(">I", data[4:8])[0]
            pos = 8
            for number in range(1, count + 1):
                if pos + 8 > len(data):
                    break
                length = struct.unpack(">I", data[pos:pos + 4])[0]
                if length < 8:
                    break
                if data[pos + 8:pos + length] == _APPLE_CREATION_KEY:
                    index = number
                pos += length
        elif kind == b"ilst":
            for item, item_body, item_end in _boxes(f, body, box_end):
                for inner, inner_body, inner_end in _boxes(f, item_body, item_end):
                    if inner == b"data" and inner_end - inner_body > 8:
                        f.seek(inner_body + 8)
                        values[struct.unpack(">I", item)[0]] = f.read(
                            min(inner_end - inner_body - 8, 64))
    if index is None or index not in values:
        return None
    return _parse_wall_clock(values[index].decode("utf-8", "replace"))


_WALL_CLOCK = re.compile(
    r"(\d{4})[-:/](\d{2})[-:/](\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?")
_CTIME_STYLE = re.compile(
    r"([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(\d{4})")
_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}


def _parse_wall_clock(text: str) -> datetime | None:
    text = text.strip("\x00\r\n\t ")
    match = _WALL_CLOCK.search(text)
    try:
        if match:
            y, mo, d, h, mi, s = (int(v) if v else 0 for v in match.groups())
            return datetime(y, mo, d, h, mi, s)
        match = _CTIME_STYLE.search(text)
        if match and match.group(1).lower() in _MONTHS:
            return datetime(int(match.group(6)), _MONTHS[match.group(1).lower()],
                            int(match.group(2)), int(match.group(3)),
                            int(match.group(4)), int(match.group(5)))
    except ValueError:
        pass
    return None


def _avi_date(f, size):
    f.seek(4)
    riff_end = min(size, 8 + struct.unpack("<I", f.read(4))[0])
    found: dict[bytes, str] = {}

    def walk(start, end, depth):
        pos, count = start, 0
        while pos + 8 <= end and count < _MAX_BOXES:
            count += 1
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                return
            chunk, length = struct.unpack("<4sI", header)
            if chunk == b"LIST" and depth < 3:
                if f.read(4) in (b"hdrl", b"INFO", b"strl"):
                    walk(pos + 12, min(end, pos + 8 + length), depth + 1)
            elif chunk in (b"IDIT", b"ICRD") and chunk not in found:
                f.seek(pos + 8)
                found[chunk] = f.read(min(length, 64)).decode("latin-1")
            pos += 8 + length + (length & 1)

    walk(12, riff_end, 0)
    for chunk in (b"IDIT", b"ICRD"):
        if chunk in found:
            dt = _parse_wall_clock(found[chunk])
            if plausible(dt, MIN_FS_YEAR):
                return dt
    return None


# --- the file name ------------------------------------------------------------

_NAME_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})([-_.]?)(0[1-9]|1[0-2])\2(0[1-9]|[12]\d|3[01])"
    r"(?:[-_. T]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?(?!\d)")


def filename_date(name: str) -> datetime | None:
    """A date (and time, when present) written in a file name, or None.

    Eight digits with one separator style throughout, so a counter such as
    ``DSC_2012`` or ``P1000123`` is never mistaken for a date.
    """
    match = _NAME_DATE.search(os.path.basename(os.fspath(name)))
    if not match:
        return None
    y, _sep, mo, d, h, mi, s = match.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    return dt if plausible(dt) else None
