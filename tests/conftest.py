"""Shared helpers: a small library of real files, a scanned app, people by role."""

from __future__ import annotations

import os
import struct
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from ninaivu_lite import auth, create_app, db
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner


def make_jpeg(path: Path, taken: str | None = None, size=(640, 480), orientation: int | None = None,
              camera: tuple[str, str] | None = None, color=(200, 120, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    if taken:
        exif[0x0132] = taken
        exif.get_ifd(0x8769)[0x9003] = taken
    if orientation:
        exif[0x0112] = orientation
    if camera:
        exif[0x010F], exif[0x0110] = camera
    img.save(path, "JPEG", exif=exif.tobytes(), quality=80)
    return path


def make_mp4(path: Path, when: datetime) -> Path:
    """A minimal MP4: ftyp + moov/mvhd carrying a creation time (UTC)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    qt = int((when - datetime(1904, 1, 1)).total_seconds())
    mvhd_body = bytes([0, 0, 0, 0]) + struct.pack(">II", qt, qt) + b"\0" * 88
    mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom" + b"\0\0\0\1"
    path.write_bytes(ftyp + moov)
    return path


@pytest.fixture()
def library(tmp_path):
    """Photos/ with a few dated files in subfolders, and a data folder.

    beach.jpg, sunset.jpg   2019/            2019-05-12, EXIF (beach: Canon EOS 80D)
    IMG_20230115_091500.jpg family/pongal/   date from the file name only
    portrait.jpg            (top)            2021-08-01, rotated 90° by EXIF (600x400 stored)
    clip.mp4                family/          2022-03-04 from the MP4 header
    broken.jpg              (top)            unreadable; date from the file (2015-06-01)
    notes.txt, .hidden/secret.jpg            never indexed
    """
    root = tmp_path / "Photos"
    make_jpeg(root / "2019" / "beach.jpg", "2019:05:12 10:00:00", camera=("Canon", "Canon EOS 80D"))
    make_jpeg(root / "2019" / "sunset.jpg", "2019:05:12 18:30:00")
    make_jpeg(root / "family" / "pongal" / "IMG_20230115_091500.jpg")
    make_jpeg(root / "portrait.jpg", "2021:08:01 12:00:00", size=(600, 400), orientation=6)
    make_mp4(root / "family" / "clip.mp4", datetime(2022, 3, 4, 5, 6, 7))
    (root / "notes.txt").write_text("not a photo")
    (root / ".hidden").mkdir()
    make_jpeg(root / ".hidden" / "secret.jpg", "2020:01:01 00:00:00")
    broken = root / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff not really a jpeg")
    os.utime(broken, (datetime(2015, 6, 1).timestamp(),) * 2)
    return root, tmp_path / "data"


@pytest.fixture()
def app(library):
    """The app over the scanned sample library (6 items), with one admin."""
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root))
    scanner = Scanner(cfg.data_dir, cfg.folders)
    scanner.scan_once(db.connect(data))
    application = create_app(cfg, scanner=scanner, addresses=["192.168.1.20"])
    conn = db.connect(data)
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    return application


def sign_in(app, client, role="admin", username=None, **fields):
    """Make a person with *role* (or use the existing admin 'appa') and give
    *client* their session cookie. Returns the auth.User."""
    conn = db.connect(app.config["LITE"].data_dir)
    if role == "admin" and username is None:
        user = auth.get_user_by_name(conn, "appa")
    else:
        if role == "admin":
            fields.setdefault("password", "admin passphrase")
        user = auth.create_user(conn, username or f"{role}-user",
                                name=f"{role.title()} Person", role=role, **fields)
    token = auth.start_session(conn, user.id)
    client.set_cookie(auth.SESSION_COOKIE, token)
    return user


@pytest.fixture()
def admin(app):
    c = app.test_client()
    sign_in(app, c, "admin")
    return c


@pytest.fixture()
def family(app):
    c = app.test_client()
    sign_in(app, c, "family", username="amma")
    return c


@pytest.fixture()
def guest(app):
    c = app.test_client()
    sign_in(app, c, "guest", username="paati")
    return c


def ids(app) -> dict[str, int]:
    conn = db.connect(app.config["LITE"].data_dir)
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM assets")}
