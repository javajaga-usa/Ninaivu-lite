"""Data safety, sixth audit (2026-10-11): the index version 11 step, Live
Photos and copies found by the scan, backups, the importer, and what is
read from files for On this day and Photos taken nearby."""

from __future__ import annotations

import os
import sqlite3
import time
import zipfile
from datetime import datetime

import pytest
from conftest import ids, make_jpeg
from PIL import Image, TiffImagePlugin
from test_gallery_api import all_items, rescan
from test_import import noisy_jpeg, run
from test_live_photos_duplicates import conn_of, make_clip
from test_on_this_day_nearby import names, place
from test_safe_upgrade_2026_10_08 import older_index
from test_share_api import share

from ninaivu_lite import backups, db, importer, media
from ninaivu_lite.scanner import Scanner


def pair_of(app, name: str) -> tuple:
    return tuple(conn_of(app).execute("SELECT live_of, live_id FROM assets WHERE name = ?",
                                      (name,)).fetchone())


def shown(client) -> list[int]:
    return [i[0] for i in all_items(client.get("/api/segments").json)]


def restarted(app) -> None:
    """A scan by a scanner just started, as after the program is started again."""
    lite = app.config["LITE"]
    Scanner(lite.data_dir, lite.folders).scan_once(conn_of(app))


# --- the version 11 step --------------------------------------------------------------------


def test_a_cut_in_the_middle_of_the_version_11_step_leaves_version_10_whole(tmp_path,
                                                                            monkeypatch):
    data = tmp_path / "data"
    older_index(data, 10)
    real = db._statements

    def cut(script):
        for number, statement in enumerate(real(script)):
            if script is db.MIGRATIONS[10] and number == 5:
                raise KeyboardInterrupt        # the power goes after the columns are added
            yield statement

    monkeypatch.setattr(db, "_statements", cut)
    with pytest.raises(KeyboardInterrupt):
        db.connect(data)
    raw = sqlite3.connect(str(data / db.DB_FILE))
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 10
        columns = {r[1] for r in raw.execute("PRAGMA table_info(assets)")}
        assert not columns & {"live_of", "live_id", "quick_hash", "content_hash"}
        assert raw.execute("SELECT name FROM sqlite_master WHERE name = 'assets_grid'").fetchone()
        assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        raw.close()
    monkeypatch.setattr(db, "_statements", real)
    conn = db.connect(data)            # the next start finishes it
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    finally:
        conn.close()


# --- backups taken before a change ------------------------------------------------------------


def test_a_copy_cut_short_is_cleared_and_never_listed_or_counted(tmp_path):
    data = tmp_path / "data"
    db.connect(data).close()
    folder = data / "backups"
    folder.mkdir()
    # A power cut while the copy before the update was being written: the
    # name it claimed (empty) and its half-written .tmp.
    left = folder / "before-update-from-index-10-2026-10-01-090000.zip"
    left.write_bytes(b"")
    half = folder / (left.name + ".tmp")
    half.write_bytes(b"PK half")
    daily_half = folder / "ninaivu-lite-2026-10-01.zip.tmp"
    daily_half.write_bytes(b"PK half")
    old = time.time() - 2 * backups.LEFTOVER_SECONDS
    for path in (left, half, daily_half):
        os.utime(path, (old, old))
    # One being written right now by another process looks the same, but is new.
    now = folder / "before-update-from-index-10-2026-10-11-090000.zip"
    now.write_bytes(b"")
    assert [b["name"] for b in backups.listing(data)] == []        # nothing empty offered
    made = backups.before_change(data, "deleting-person")
    assert made is not None and zipfile.is_zipfile(made)
    assert not left.exists() and not half.exists() and now.exists()
    backups.daily(data)
    assert not daily_half.exists()
    assert {b["name"] for b in backups.listing(data)} == {made.name,
                                                          f"ninaivu-lite-{datetime.now():%Y-%m-%d}.zip"}


def test_the_copy_before_an_update_outlives_later_copies(tmp_path):
    data = tmp_path / "data"
    db.connect(data).close()
    folder = data / "backups"
    folder.mkdir()
    update = folder / "before-update-from-index-10-2026-10-01-090000.zip"
    update.write_bytes(b"old")
    # An empty name claimed by a copy that was cut short takes no place either.
    (folder / "before-deleting-person-2026-10-02-090000.zip").write_bytes(b"")
    made = [backups.before_change(data, "deleting-person") for _ in range(backups.KEEP_BEFORE + 2)]
    left = {p.name for p in folder.glob("before-*.zip") if p.stat().st_size}
    # The only way back to the version before stays; the others are the newest five.
    assert left == {update.name, *(p.name for p in made[-backups.KEEP_BEFORE:])}
    # A newer update's copy takes its place.
    newer = backups.before_change(data, "update-from-index-11")
    left = {p.name for p in folder.glob("before-*.zip") if p.stat().st_size}
    assert update.name not in left and newer.name in left
    assert len(left) == backups.KEEP_BEFORE


# --- Live Photos ----------------------------------------------------------------------------


def test_a_long_video_saved_over_a_live_photos_clip_is_a_video_again(app, admin, library):
    root, _ = library
    make_jpeg(root / "p" / "IMG_1.JPG", "2024:01:15 10:00:00")
    clip = make_clip(root / "p" / "IMG_1.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    rescan(app)
    i = ids(app)
    assert pair_of(app, "IMG_1.MOV")[0] == i["IMG_1.JPG"]
    make_clip(clip, datetime(2024, 1, 15, 4, 30), 60)
    os.utime(clip, (time.time() + 10,) * 2)
    rescan(app)
    assert pair_of(app, "IMG_1.MOV") == (None, None)
    assert pair_of(app, "IMG_1.JPG") == (None, None)
    assert i["IMG_1.MOV"] in shown(admin)


def test_a_clip_kept_in_an_album_a_favourite_or_a_link_is_found_through_its_still(
        app, family, library):
    root, _ = library
    make_clip(root / "p" / "IMG_7.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    rescan(app)
    clip = ids(app)["IMG_7.MOV"]
    # A short video of its own (as every clip was before index version 11).
    album = family.post("/api/albums", json={"name": "Trip"}).json["id"]
    assert family.post(f"/api/albums/{album}/items", json={"ids": [clip]}).status_code == 200
    assert family.post(f"/api/asset/{clip}", json={"favorite": True}).status_code == 200
    token = share(family, "asset", clip)
    stranger = app.test_client()
    assert stranger.get(f"/api/share/{token}").status_code == 200

    make_jpeg(root / "p" / "IMG_7.JPG", "2024:01:15 10:00:00")
    rescan(app)
    still = ids(app)["IMG_7.JPG"]
    assert pair_of(app, "IMG_7.MOV")[0] == still
    (listed,) = family.get("/api/albums").json["albums"]
    assert listed["n"] == 1 and listed["cover_id"] == still
    assert family.get(f"/api/segments?album={album}").json["total"] == 1
    assert family.get(f"/api/asset/{still}").json["favorite"] is True
    app.config["LITE"].video_originals = True      # the fake clip cannot be remuxed
    r = stranger.get(f"/api/share/{token}")
    assert r.status_code == 200
    item = r.json["item"]
    assert item["id"] == still and item["is_live"] is True
    # The clip's own places are kept, for when the pair is undone.
    c = conn_of(app)
    assert c.execute("SELECT COUNT(*) FROM album_items WHERE asset_id = ?", (clip,)).fetchone()[0]


# --- HEIC read once pillow-heif is there ------------------------------------------------------


def test_a_heic_indexed_before_it_could_be_read_is_read_after_a_restart(app, admin, library,
                                                                         monkeypatch):
    root, _ = library
    img = Image.new("RGB", (400, 300), (10, 200, 30))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2018:07:04 10:00:00"
    path = root / "iphone" / "IMG_5.HEIC"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "HEIF", exif=exif.tobytes())
    real = Image.open

    def no_heif(fp, *args, **kwargs):
        if str(fp).lower().endswith(".heic"):
            raise OSError("cannot identify image file")
        return real(fp, *args, **kwargs)

    # Indexed by a Ninaivu Lite without pillow-heif (every installer before 1.13).
    monkeypatch.setattr(media.Image, "open", no_heif)
    rescan(app)
    row = conn_of(app).execute("SELECT * FROM assets WHERE name = 'IMG_5.HEIC'").fetchone()
    assert row["error"] == "unreadable" and row["date_source"] == "mtime"
    assert row["thumb"] == db.THUMB_NONE

    monkeypatch.setattr(media.Image, "open", real)
    restarted(app)
    row = conn_of(app).execute("SELECT * FROM assets WHERE name = 'IMG_5.HEIC'").fetchone()
    assert row["error"] is None and row["date_key"] == "2018-07-04"
    assert (row["width"], row["height"]) == (400, 300) and row["thumb"] == db.THUMB_OK
    # Read once a start: a scan after that leaves it alone.
    read = []
    monkeypatch.setattr(media, "describe", lambda *a: read.append(a) or {})
    app.config["SCANNER"].scan_once(conn_of(app))
    assert read == []


def test_a_photo_that_cannot_be_read_by_any_reader_is_not_read_every_start(app, monkeypatch):
    # broken.jpg is not a photograph at all; JPEG is no format a plugin adds.
    read = []
    real = media.describe
    monkeypatch.setattr(media, "describe", lambda *a: read.append(a[1]) or real(*a))
    restarted(app)
    assert "broken.jpg" not in read


# --- GPS, Photos taken nearby, On this day ----------------------------------------------------


def gps_photo(path, lat, lon) -> dict:
    img = Image.new("RGB", (60, 40))
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: lat, 3: "E", 4: lon})
    img.save(path, exif=exif.tobytes())
    return media.read_photo(str(path))


def test_a_damaged_gps_tag_is_no_place(tmp_path):
    r = TiffImagePlugin.IFDRational
    good = gps_photo(tmp_path / "a.jpg", (r(13), r(3), r(0)), (r(80), r(16), r(48)))
    assert (good["lat"], good["lon"]) == (13.05, 80.28)
    for lat, lon in (((r(233), r(0), r(0)), (r(80), r(0), r(0))),     # past the pole
                     ((r(13), r(0), r(0)), (r(451), r(0), r(0))),     # past the date line
                     ((r(1, 0), r(0), r(0)), (r(80), r(0), r(0)))):   # 1/0: not a number
        info = gps_photo(tmp_path / "b.jpg", lat, lon)
        assert "lat" not in info and "lon" not in info


def test_photos_taken_nearby_across_the_180th_meridian(app, admin):
    # Taveuni, Fiji: the 180th meridian runs through the island.
    i = place(app, beach_jpg=(-16.80, 179.998), sunset_jpg=(-16.80, -179.998),
              portrait_jpg=(-16.80, 179.98))
    near = sorted(names(app, admin.get(f"/api/segments?near={i['beach.jpg']}").json))
    assert near == ["beach.jpg", "sunset.jpg"]
    near = sorted(names(app, admin.get(f"/api/segments?near={i['sunset.jpg']}").json))
    assert near == ["beach.jpg", "sunset.jpg"]


def test_a_leap_days_photos_are_on_this_day_on_february_28_in_other_years(app, admin, library):
    root, _ = library
    make_jpeg(root / "leap" / "leap.jpg", "2020:02:29 10:00:00")
    make_jpeg(root / "leap" / "before.jpg", "2019:02:28 10:00:00")
    rescan(app)
    on = lambda day: sorted(names(app, admin.get(f"/api/segments?on_this_day={day}").json))  # noqa: E731
    assert on("2025-02-28") == ["before.jpg", "leap.jpg"]
    assert on("2028-02-28") == ["before.jpg"]          # a leap year has its own 29th
    assert on("2028-02-29") == ["leap.jpg"]
    assert on("2025-03-01") == []


# --- the importer ---------------------------------------------------------------------------


def big_clip(path, when, seconds):
    """A clip the importer takes (it leaves files under 24 KB)."""
    make_clip(path, when, seconds)
    with open(path, "ab") as f:
        f.write(os.urandom(40_000))
    return path


def test_an_import_keeps_a_live_photos_clip_with_its_still(tmp_path):
    dest = tmp_path / "Archive"
    day = dest / "2024" / "01" / "15"
    # Phone A's photograph of that day, of the same name, is in the archive already.
    noisy_jpeg(day / "IMG_0042.JPG", "2024:01:15 09:00:00", seed=9)
    phone = tmp_path / "PhoneB" / "DCIM"
    noisy_jpeg(phone / "IMG_0042.JPG", "2024:01:15 10:00:00", seed=2)
    big_clip(phone / "IMG_0042.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    # Just after midnight, and a clip whose header (in UTC) says the day before.
    noisy_jpeg(phone / "IMG_0043.JPG", "2024:01:16 00:10:00", seed=3)
    big_clip(phone / "IMG_0043.MOV", datetime(2024, 1, 14, 18, 40), 2.5)
    before = (day / "IMG_0042.JPG").read_bytes()
    run(tmp_path / "data", [tmp_path / "PhoneB"], dest)
    # B's still was named IMG_0042_1.JPG; its clip goes with it, not beside A's photo.
    assert (day / "IMG_0042_1.JPG").is_file() and (day / "IMG_0042_1.MOV").is_file()
    assert not (day / "IMG_0042.MOV").exists()
    assert (day / "IMG_0042.JPG").read_bytes() == before
    nxt = dest / "2024" / "01" / "16"
    assert (nxt / "IMG_0043.JPG").is_file() and (nxt / "IMG_0043.MOV").is_file()
    # Recorded where it went.
    rows = {os.path.basename(r["source"]): r["destination"] for r in db.connect(
        tmp_path / "data").execute("SELECT source, destination FROM import_files")}
    assert rows["IMG_0042.MOV"] == str(day / "IMG_0042_1.MOV")


def test_an_import_never_writes_over_a_file_that_comes_meanwhile(tmp_path, monkeypatch):
    src = tmp_path / "Card"
    noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    real = importer.Importer._unique_path
    came = []

    def free_then_taken(self, conn, folder, name, *args, **kwargs):
        found = real(self, conn, folder, name, *args, **kwargs)
        if not came and not kwargs.get("planning"):
            # Someone copies a photograph of that name there by hand just now.
            os.makedirs(folder, exist_ok=True)
            with open(found[0], "wb") as f:
                f.write(b"the family's own photograph")
            came.append(found[0])
        return found

    monkeypatch.setattr(importer.Importer, "_unique_path", free_then_taken)
    run(tmp_path / "data", [src], dest)
    day = dest / "2019" / "05" / "12"
    assert (day / "beach.jpg").read_bytes() == b"the family's own photograph"
    assert (day / "beach_1.jpg").read_bytes() == (src / "beach.jpg").read_bytes()


def test_publish_refuses_an_existing_name(tmp_path):
    tmp, final = tmp_path / "a.tmp", tmp_path / "a.jpg"
    tmp.write_bytes(b"new")
    final.write_bytes(b"old")
    assert importer.publish(str(tmp), str(final)) is False
    assert final.read_bytes() == b"old" and tmp.read_bytes() == b"new"
    final.unlink()
    assert importer.publish(str(tmp), str(final)) is True
    assert final.read_bytes() == b"new" and not tmp.exists()


# --- a Live Photo copied to a drive ---------------------------------------------------------


def test_an_export_keeps_a_live_photos_clip_with_its_renamed_still(app, admin, library,
                                                                    tmp_path, monkeypatch):
    from test_drives import fake_drive, wait_export

    from ninaivu_lite import drives
    root, _ = library
    make_jpeg(root / "iphone" / "IMG_0042.JPG", "2024:01:15 10:00:00", color=(1, 2, 3))
    clip = make_clip(root / "iphone" / "IMG_0042.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    usb = tmp_path / "USB"
    out = usb / "Ninaivu Lite" / root.name / "iphone"
    out.mkdir(parents=True)
    # On the drive already: another photograph of that name, and a video of
    # the same size as this clip beside it (the other photograph's own).
    (out / "IMG_0042.JPG").write_bytes(b"another photograph")
    theirs = os.urandom(clip.stat().st_size)
    (out / "IMG_0042.MOV").write_bytes(theirs)
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    app.config["DRIVES"] = drives.Watcher(lister=lambda: [fake_drive(usb)])
    assert admin.post("/api/admin/drives/export", json={"id": "usb1"}).status_code == 200
    assert wait_export(admin)["phase"] == "done"
    assert (out / "IMG_0042 (2).JPG").read_bytes() == (root / "iphone" / "IMG_0042.JPG").read_bytes()
    assert (out / "IMG_0042 (2).MOV").read_bytes() == clip.read_bytes()
    assert (out / "IMG_0042.MOV").read_bytes() == theirs
    assert (out / "IMG_0042.JPG").read_bytes() == b"another photograph"


# --- copies of the index are their owner's alone ----------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_copies_of_the_index_and_the_log_are_the_owners_alone(tmp_path, monkeypatch):
    import logging

    from ninaivu_lite import __main__ as cli
    old = os.umask(0o022)
    try:
        data = tmp_path / "data"
        db.connect(data).close()
        (data / "settings.json").write_text("{}", encoding="utf-8")
        folder = data / "backups"
        folder.mkdir(mode=0o755)
        earlier = folder / "ninaivu-lite-2026-01-01.zip"
        earlier.write_bytes(b"PK old")
        os.chmod(earlier, 0o644)
        made = [backups.daily(data), backups.before_change(data, "update-from-index-10")]
        for path in [*made, earlier]:
            assert path.stat().st_mode & 0o777 == 0o600, path
        assert folder.stat().st_mode & 0o777 == 0o700

        backups.restore(data, made[0])
        assert (data / db.DB_FILE).stat().st_mode & 0o077 == 0
        assert (data / "settings.json").stat().st_mode & 0o077 == 0

        root = logging.getLogger()
        handlers = list(root.handlers)
        try:
            cli.setup_logging(str(data))
            logs = data / "logs"
            assert logs.stat().st_mode & 0o777 == 0o700
            assert all(p.stat().st_mode & 0o077 == 0 for p in logs.iterdir())
        finally:
            for handler in root.handlers[:]:
                if handler not in handlers:
                    root.removeHandler(handler)
                    handler.close()
    finally:
        os.umask(old)
