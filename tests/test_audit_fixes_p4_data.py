"""Regression tests for the project audit of 2026-10-06, data integrity, import
and scanning: A23 (the app's side), A25, A26, A35, A41-A47 and A63. Each test
sets up the scenario the audit described and checks that it no longer happens."""

from __future__ import annotations

import os
import sqlite3
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_jpeg, make_mp4
from PIL import Image
from test_import import noisy_jpeg, rows, run, sha

from ninaivu_lite import __main__ as cli
from ninaivu_lite import config, db, drives, importer, media, net, phones, scanner
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner


def same_size_picture(path: Path, seed: int) -> Path:
    """A BMP: a different picture of the same size is the same number of bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.effect_noise((200, 200), seed).convert("RGB").save(path)
    return path


# --- A25: a changed file whose copy was stopped is copied next time -----------------------


def test_a25_a_copy_stopped_part_way_is_done_next_time(tmp_path, monkeypatch):
    card, dest, data = tmp_path / "Card", tmp_path / "Archive", tmp_path / "data"
    photo = same_size_picture(card / "DCIM" / "IMG_0001.BMP", 50)
    os.utime(photo, (time.time() - 86400,) * 2)
    run(data, [card], dest)
    assert rows(data)["IMG_0001.BMP"]["status"] == "verified"
    same_size_picture(photo, 90)                     # edited in place, same size
    real_copy = importer.Importer._copy_and_hash

    def stopped(self, src, tmp):
        raise importer.Cancelled()

    monkeypatch.setattr(importer.Importer, "_copy_and_hash", stopped)
    run(data, [card], dest)
    monkeypatch.setattr(importer.Importer, "_copy_and_hash", real_copy)
    engine = run(data, [card], dest)
    assert "already done" not in engine.job["message"]
    assert sha(photo) in {sha(p) for p in dest.rglob("*.BMP")}


# --- A26: a phone copy that may be cut short is never taken as imported ---------------------


def _phone() -> drives.Drive:
    return drives.Drive("p1", "::{phone}", "Pixel", 0, 0, kind="phone", shell=True)


def test_a26_the_fetch_script_never_trusts_a_copy_it_cannot_measure():
    script = phones.FETCH_SCRIPT
    assert "$size -le 0 -or" not in script            # an old copy of unknown size is not "fetched"
    assert '""failed"":1' in script and "NL_FAILED" in script and "NL_UNSURE" in script
    assert "Remove-Item -LiteralPath $file" in script


def test_a26_unsure_and_failed_copies_are_not_listed_as_imported(tmp_path, monkeypatch):
    data, dest = tmp_path / "data", tmp_path / "Archive"
    drive = _phone()
    mirror = phones.mirror_for(str(data), drive)
    rel_ok = os.path.join("Internal storage", "DCIM", "ok.jpg")
    rel_unsure = os.path.join("Internal storage", "DCIM", "unsure.jpg")
    rel_failed = os.path.join("Internal storage", "DCIM", "failed.jpg")

    def fetch(self, drive, mirror_path):
        for seed, rel in enumerate((rel_ok, rel_unsure, rel_failed), start=1):
            noisy_jpeg(Path(mirror_path) / rel, "2024:01:01 10:00:00", seed=seed)
        listing = Path(phones.imported_list(str(data), drive))
        listing.parent.mkdir(parents=True, exist_ok=True)
        Path(phones.imported_list(str(data), drive, "unsure")).write_text(
            rel_unsure + "\n", encoding="utf-8-sig")
        Path(phones.imported_list(str(data), drive, "failed")).write_text(
            rel_failed + "\n", encoding="utf-8")
        return True

    monkeypatch.setattr(phones.PhoneImport, "_fetch", fetch)
    job = phones.PhoneImport()
    job.start(drive, mirror, str(dest), importer.Importer(data), str(data))
    job.wait(120)
    state = job.progress()
    assert state["phase"] == "done", state
    assert state["message"]["vars"]["failed"] == "1"
    archived = {p.name for p in dest.rglob("*.jpg")}
    assert archived == {"ok.jpg", "unsure.jpg"}       # the cut-short copy is not archived
    listed = Path(phones.imported_list(str(data), drive)).read_text(encoding="utf-8").split("\n")
    assert rel_ok in listed and rel_unsure not in listed and rel_failed not in listed


# --- A35: the Linux system service looks for every desktop user's drives -----------------


def test_a35_a_system_account_looks_in_every_users_media_folder(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "geteuid", lambda: 997, raising=False)
    monkeypatch.setenv("USER", "ninaivu-lite")
    listing = {"/media": ["/media/amma"], "/run/media": ["/run/media/appa"],
               "/run/user": ["/run/user/1000"]}
    monkeypatch.setattr(drives, "_subfolders", lambda parent: listing.get(parent, []))
    parents = drives._posix_parents()
    assert "/media/amma" in parents and "/run/media/appa" in parents
    assert "/media/ninaivu-lite" not in parents
    seen = []
    monkeypatch.setattr(os, "getuid", lambda: 997, raising=False)
    real = drives._gvfs_phones

    def gvfs(parent=None):
        if parent is not None:
            seen.append(parent)
            return []
        return real()

    monkeypatch.setattr(drives, "_gvfs_phones", gvfs)
    gvfs()
    assert seen == [os.path.join("/run/user/1000", "gvfs")]


def test_a35_a_desktop_user_still_looks_in_their_own(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setenv("USER", "amma")
    parents = drives._posix_parents()
    assert parents[:2] == [os.path.join("/media", "amma"), os.path.join("/run/media", "amma")]


# --- A41: a photo and a video of the same moment are filed on the same day ------------------


def jpeg_with_offset(path: Path, taken: str, offset: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = taken
    sub[0x9011] = offset
    Image.new("RGB", (64, 64)).save(path, "JPEG", exif=exif.tobytes())
    return path


def test_a41_a_video_is_read_on_the_clocks_of_the_photos_beside_it(tmp_path):
    # Pongal morning in Chennai: 08:00 IST is 02:30 UTC, which the video's header keeps.
    folder = tmp_path / "Phone" / "Camera"
    jpeg_with_offset(folder / "IMG_1.jpg", "2023:01:15 08:00:00", "+05:30")
    video = make_mp4(folder / "VID_1.mp4", datetime(2023, 1, 15, 2, 30))
    taken, source = importer.capture_date(str(video), "video", video.stat())
    assert source == "container" and taken == datetime(2023, 1, 15, 8, 0)
    info = media.describe(str(video), video.name, "video", video.stat())
    assert scanner.day_of(info["taken_at"]) == "2023-01-15"
    assert scanner.from_timestamp(info["taken_at"]).hour == 8


def test_a41_without_a_nearby_offset_nothing_changes(tmp_path):
    folder = tmp_path / "Phone"
    jpeg_with_offset(folder / "IMG_1.jpg", "2019:01:15 08:00:00", "+05:30")   # years away
    video = make_mp4(folder / "VID_1.mp4", datetime(2023, 1, 15, 2, 30))
    utc = datetime(2023, 1, 15, 2, 30, tzinfo=timezone.utc)
    assert media.zone_near(str(video))(utc) is None
    taken, _ = importer.capture_date(str(video), "video", video.stat())
    assert taken == utc.astimezone().replace(tzinfo=None)      # this computer's time, as before


# --- A42: one bad file does not stop the scan ------------------------------------------------


def jpeg_with_huge_iso(path: Path) -> Path:
    """EXIF with an ISO stored as a 64-bit number larger than SQLite can hold."""
    tiff = (b"II*\x00" + struct.pack("<I", 8)
            + struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
            + struct.pack("<H", 1) + struct.pack("<HHII", 0x8827, 16, 1, 44) + struct.pack("<I", 0)
            + struct.pack("<Q", 2 ** 63 + 5))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64)).save(path, "JPEG", exif=b"Exif\x00\x00" + tiff)
    return path


def test_a42_a_damaged_sidecar_or_tag_does_not_stop_the_scan(tmp_path):
    root, data = tmp_path / "Photos", tmp_path / "data"
    make_jpeg(root / "a" / "good.jpg", "2020:01:01 00:00:00")
    make_jpeg(root / "b" / "nested.jpg", "2020:01:01 00:00:00")
    (root / "b" / "nested.jpg.json").write_text("[" * 100000 + "]" * 100000)
    jpeg_with_huge_iso(root / "b" / "huge.jpg")
    make_jpeg(root / "c" / "later.jpg", "2020:01:01 00:00:00")
    s = Scanner(data, [str(root)])
    s.scan_once(db.connect(data))
    names = {r[0] for r in db.connect(data).execute("SELECT name FROM assets")}
    assert names == {"good.jpg", "nested.jpg", "huge.jpg", "later.jpg"}
    assert s.snapshot()["state"] == "idle"


def test_a42_describe_never_raises(tmp_path, monkeypatch):
    photo = make_jpeg(tmp_path / "x.jpg", "2020:01:01 00:00:00")

    def boom(path):
        raise RuntimeError("a bug in a reader")

    monkeypatch.setattr(media, "read_photo", boom)
    monkeypatch.setattr(media.dates, "fallback_date", lambda *a, **k: boom(a))
    info = media.describe(str(photo), photo.name, "picture", photo.stat())
    assert info["taken_at"] and info["taken_source"] == "mtime"


# --- A43: a linked folder whose drive is unplugged keeps its photos -------------------------


def test_a43_an_offline_linked_folder_is_not_marked_missing(tmp_path):
    root, data, ext = tmp_path / "Photos", tmp_path / "data", tmp_path / "Disk" / "Wedding"
    make_jpeg(root / "home.jpg", "2020:01:01 00:00:00")
    make_jpeg(ext / "w1.jpg", "2019:01:01 00:00:00")
    try:
        os.symlink(ext, root / "Wedding", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("links cannot be made here")
    s = Scanner(data, [str(root)])
    conn = db.connect(data)
    s.scan_once(conn)
    assert conn.execute("SELECT missing FROM assets WHERE name = 'w1.jpg'").fetchone()[0] == 0
    os.rename(tmp_path / "Disk", tmp_path / "Unplugged")
    s.scan_once(conn)
    assert conn.execute("SELECT missing FROM assets WHERE name = 'w1.jpg'").fetchone()[0] == 0
    assert s.snapshot()["unreadable"] == 1


# --- A44: a folder named while the server runs is not silently dropped ---------------------


def test_a44_folders_named_while_running_are_not_saved_behind_its_back(tmp_path, monkeypatch,
                                                                        capsys):
    data, photos = tmp_path / "data", tmp_path / "Photos"
    photos.mkdir()
    Config(data_dir=str(data), folders=[]).save()
    monkeypatch.setattr(net, "already_running", lambda port: True)
    code = cli.main([str(photos), "--data", str(data), "--no-browser"])
    assert code == 2
    assert "not added" in capsys.readouterr().err
    assert Config.load(data).folders == []


# --- A45: copying to a FAT or exFAT drive --------------------------------------------------


def test_a45_names_differing_only_in_case_do_not_overwrite_each_other(tmp_path):
    drive = tmp_path / "USB"
    claimed: set[str] = set()
    first = drives._place(str(drive / "IMG_1.JPG"), 100, claimed)
    second = drives._place(str(drive / "img_1.jpg"), 200, claimed)
    assert first == str(drive / "IMG_1.JPG")
    assert second == str(drive / "img_1 (2).jpg")


def test_a45_only_a_missing_name_is_free(tmp_path, monkeypatch):
    def refused(path, *a, **k):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(drives.os, "stat", refused)
    with pytest.raises(PermissionError):
        drives._place(str(tmp_path / "USB" / "a.jpg"), 100, set())


def test_a45_an_unreadable_folder_is_counted_not_skipped(tmp_path, monkeypatch):
    root, usb, data = tmp_path / "Photos", tmp_path / "USB", tmp_path / "data"
    make_jpeg(root / "ok" / "a.jpg", "2020:01:01 00:00:00")
    make_jpeg(root / "locked" / "b.jpg", "2020:01:01 00:00:00")
    usb.mkdir()
    real_scandir = os.scandir

    def scandir(path):
        if os.path.basename(str(path)) == "locked":
            raise PermissionError(13, "Permission denied", path)
        return real_scandir(path)

    monkeypatch.setattr(drives.os, "scandir", scandir)
    exporter = drives.Exporter()
    exporter.start(drives.Drive("u", str(usb), "USB", 0, 0), [str(root)], str(data))
    exporter.wait(60)
    state = exporter.progress()
    assert state["copied"] == 1 and state["errors"] == 1
    assert "could not be copied" in state["message"]["key"]


# --- A46: a dry run keeps the record of an earlier import -----------------------------------


def test_a46_a_dry_run_does_not_erase_an_earlier_import(tmp_path):
    card, dest, data = tmp_path / "Card", tmp_path / "Archive", tmp_path / "data"
    photo = noisy_jpeg(card / "IMG_0001.JPG", "2019:05:12 10:00:00", seed=1)
    run(data, [card], dest)
    before = rows(data)["IMG_0001.JPG"]
    assert before["status"] == "verified"
    noisy_jpeg(photo, "2019:05:12 10:00:00", seed=7)              # the card's file changed
    engine = run(data, [card], dest, mode="dry-run")
    after = rows(data)["IMG_0001.JPG"]
    assert after["status"] == "verified"
    assert after["destination"] == before["destination"] and after["size"] == before["size"]
    assert "1 files would be archived" in engine.job["message"]
    run(data, [card], dest)                                        # the real run still copies it
    assert sha(photo) in {sha(p) for p in dest.rglob("*.JPG")}


# --- A47: a failed open of the index is not the end of scanning ----------------------------


def test_a47_the_scanner_tries_again_when_the_index_cannot_be_opened(tmp_path, monkeypatch,
                                                                     library):
    root, data = library
    real = db.connect
    calls = []

    def flaky(data_dir):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(data_dir)

    monkeypatch.setattr(scanner, "RETRY_EVERY", 0.05)
    monkeypatch.setattr(scanner.db, "connect", flaky)
    s = Scanner(data, [str(root)])
    s.start()
    try:
        deadline = time.time() + 30
        while s.snapshot()["last_finished"] is None and time.time() < deadline:
            time.sleep(0.05)
    finally:
        s.stop()
    assert s.snapshot()["last_finished"] is not None and len(calls) == 2


def test_a47_an_import_that_cannot_open_the_index_says_so(tmp_path, monkeypatch):
    def locked(data_dir):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(importer.db, "connect", locked)
    (tmp_path / "Card").mkdir()
    engine = run(tmp_path / "data", [tmp_path / "Card"], tmp_path / "Archive")
    assert engine.progress()["phase"] == "failed"
    assert "locked" in engine.progress()["job_message"]


def test_a47_a_lock_on_the_journal_mode_does_not_leave_wal(tmp_path, monkeypatch):
    db.connect(tmp_path).close()

    class Locked(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("PRAGMA journal_mode="):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    real = sqlite3.connect
    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **k: real(*a, factory=Locked, **k))
    conn = db.connect(tmp_path)
    monkeypatch.setattr(db.sqlite3, "connect", real)
    conn.close()
    plain = sqlite3.connect(str(tmp_path / db.DB_FILE))
    assert plain.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    plain.close()


# --- A63: migrations across processes, a newer index, damaged settings ----------------------


def test_a63_a_migration_another_process_finished_first_is_not_run_twice(tmp_path, monkeypatch):
    path = str(tmp_path / db.DB_FILE)
    monkeypatch.setattr(db, "_MIGRATE_LOCK", threading.RLock())
    other = sqlite3.connect(path, timeout=30)
    raced = []

    class Late(sqlite3.Connection):
        def execute(self, sql, *args):
            cur = super().execute(sql, *args)
            if sql == "PRAGMA user_version" and not raced:
                # Read, then another process brings the index up to date
                # before this one gets to it.
                raced.append(1)
                stale = cur.fetchall()
                db.migrate(other)
                return SimpleNamespace(fetchone=lambda: stale[0])
            return cur

    conn = sqlite3.connect(path, timeout=30, factory=Late)
    assert db.migrate(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    conn.close()
    other.close()


def test_a63_an_index_from_a_newer_version_is_refused(tmp_path, capsys):
    db.connect(tmp_path).close()
    plain = sqlite3.connect(str(tmp_path / db.DB_FILE))
    plain.execute(f"PRAGMA user_version={len(db.MIGRATIONS) + 1}")
    plain.commit()
    plain.close()
    with pytest.raises(db.NewerIndex):
        db.connect(tmp_path)
    assert cli.main(["--data", str(tmp_path), "--no-browser"]) == 2
    assert "newer Ninaivu Lite" in capsys.readouterr().err


def test_a63_every_damaged_settings_file_is_kept(tmp_path):
    (tmp_path / "settings.json").write_text("{ first")
    Config.load(tmp_path)
    (tmp_path / "settings.json").write_text("{ second")
    Config.load(tmp_path)
    kept = sorted(p.read_text() for p in tmp_path.glob("settings.json.damaged*"))
    assert kept == ["{ first", "{ second"]
    assert (tmp_path / "settings.json.damaged").read_text() == "{ second"


# --- A23: the data folder is private (Linux and macOS) -------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps it in the user's own folder")
def test_a23_the_data_folder_and_index_are_for_their_owner_only(tmp_path):
    data = tmp_path / "data"
    config.make_private(data)
    assert data.stat().st_mode & 0o777 == 0o700
    db.connect(data).close()
    assert (data / db.DB_FILE).stat().st_mode & 0o077 == 0
    # An existing data folder made with the usual umask is tightened too.
    os.chmod(data, 0o755)
    config.make_private(data)
    assert data.stat().st_mode & 0o777 == 0o700
    # Any other existing folder is left as it is.
    other = tmp_path / "other"
    other.mkdir(mode=0o755)
    os.chmod(other, 0o755)
    config.make_private(other)
    assert other.stat().st_mode & 0o777 == 0o755
