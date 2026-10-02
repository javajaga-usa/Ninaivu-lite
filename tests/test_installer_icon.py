"""The Windows installer's icon: NSIS cannot read PNG-compressed icons, and an
installer built with one shows a blank icon. Every size must be a plain bitmap."""

from __future__ import annotations

import struct
from pathlib import Path

ICO = Path(__file__).resolve().parent.parent / "installers" / "windows" / "ninaivu-lite.ico"


def test_every_size_in_the_icon_is_a_bitmap_not_a_png():
    data = ICO.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count >= 4
    for i in range(count):
        size, offset = struct.unpack("<II", data[6 + 16 * i + 8:6 + 16 * i + 16])
        assert data[offset:offset + 4] != b"\x89PNG", "PNG-compressed icon: NSIS shows it blank"
        assert struct.unpack("<I", data[offset:offset + 4])[0] == 40, "not a bitmap (BITMAPINFOHEADER)"
        assert offset + size <= len(data)
