"""Live Photos (a still and its short video, one photo in the gallery) and the
console's read-only list of copies, both found by the scan and kept in the
index (version 11). Neither ever touches a file."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import struct
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from conftest import ids, make_jpeg, sign_in
from test_gallery_api import all_items, rescan, set_vis
from test_safe_upgrade_2026_10_08 import older_index
from test_share_api import share

from ninaivu_lite import api_admin, copies, db, export, scanner
from ninaivu_lite.dates import container_duration

STATIC = Path(__file__).resolve().parent.parent / "ninaivu_lite" / "static"


def make_clip(path: Path, when: datetime, seconds: float) -> Path:
    """A minimal QuickTime movie whose header says how long it plays."""
    path.parent.mkdir(parents=True, exist_ok=True)
    qt = int((when - datetime(1904, 1, 1)).total_seconds())
    body = bytes(4) + struct.pack(">IIII", qt, qt, 600, int(seconds * 600)) + b"\0" * 80
    mvhd = struct.pack(">I4s", 8 + len(body), b"mvhd") + body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"qt  " + b"\0\0\0\0"
    path.write_bytes(ftyp + moov)
    return path


@pytest.fixture()
def live(app, library):
    """iphone/IMG_1234.JPG + IMG_1234.MOV (2.5 s, a Live Photo) and
    IMG_2000.JPG + img_2000.mov (30 s, a video of its own), scanned."""
    root, _ = library
    make_jpeg(root / "iphone" / "IMG_1234.JPG", "2024:01:15 10:00:00")
    make_clip(root / "iphone" / "IMG_1234.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    make_jpeg(root / "iphone" / "IMG_2000.JPG", "2024:01:16 10:00:00")
    make_clip(root / "iphone" / "img_2000.mov", datetime(2024, 1, 16, 4, 30), 30)
    rescan(app)
    return ids(app)


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


# --- Live Photos ---------------------------------------------------------------------


def test_the_movie_header_says_how_long_a_video_plays(tmp_path):
    assert container_duration(make_clip(tmp_path / "a.mov", datetime(2024, 1, 1), 2.5)) == 2.5
    assert container_duration(tmp_path / "missing.mov") is None
    (tmp_path / "junk.mov").write_bytes(b"not a movie at all")
    assert container_duration(tmp_path / "junk.mov") is None


def test_a_live_photo_is_one_photo_that_plays(app, admin, family, live):
    still, motion = live["IMG_1234.JPG"], live["IMG_1234.MOV"]
    c = conn_of(app)
    row = c.execute("SELECT live_of, duration FROM assets WHERE id = ?", (motion,)).fetchone()
    assert (row["live_of"], row["duration"]) == (still, 2.5)
    # A long video beside a photo of the same name stays a video of its own.
    assert c.execute("SELECT live_of FROM assets WHERE id = ?",
                     (live["img_2000.mov"],)).fetchone()[0] is None

    for client in (admin, family):
        item = client.get(f"/api/asset/{still}").json
        assert item["is_live"] is True and item["live_src"] == f"/api/file/{motion}"
        played = client.get(item["live_src"])
        assert played.status_code == 200 and played.mimetype == "video/quicktime"
        # Not in the grid, not openable on its own.
        assert client.get(f"/api/asset/{motion}").status_code == 404
        tiles = {i[0]: i for i in all_items(client.get("/api/segments").json)}
        assert still in tiles and motion not in tiles and live["img_2000.mov"] in tiles
        # The tile carries the mark (flag 4); the other photo does not.
        assert tiles[still][3] & 4 and not tiles[live["IMG_2000.JPG"]][3] & 4
    assert admin.get(f"/api/asset/{live['IMG_2000.JPG']}").json["is_live"] is False


def test_the_hidden_clip_is_in_no_count_search_folder_or_month(app, admin, live):
    stats = admin.get("/api/status").json["stats"]
    # The sample library's 6, two photos and one long video.
    assert (stats["count"], stats["videos"], stats["live"]) == (9, 2, 1)
    assert admin.get("/api/segments?q=IMG_1234").json["total"] == 1
    assert admin.get("/api/segments?folder=iphone").json["total"] == 3
    assert admin.get("/api/segments?kind=video&folder=iphone").json["total"] == 1
    months = admin.get("/api/months").json["months"]
    assert {"month": "2024-01", "count": 3} in months
    folders = {f["name"]: f["count"] for f in admin.get("/api/facets").json["folders"]}
    assert folders["iphone"] == 3
    overview = admin.get("/api/admin/overview").json
    assert overview["stats"]["count"] == 9 and overview["stats"]["live"] == 1
    assert overview["library"]["folders"][0]["count"] == 9
    tree = {f["path"]: f["count"] for f in admin.get("/api/admin/folders").json["folders"]}
    assert tree["iphone"] == 3
    assignable = admin.get("/api/admin/assignable").json["folders"]
    assert assignable[0]["count"] == 9


def test_the_clip_is_sent_only_to_whoever_may_see_its_still(app, admin, family, guest, live):
    still, motion = live["IMG_1234.JPG"], live["IMG_1234.MOV"]
    assert guest.get(f"/api/file/{motion}").status_code == 404        # the still is Family
    r = admin.post("/api/visibility", json={"ids": [still], "visibility": "hidden"})
    assert r.status_code == 200 and r.json["updated"] == 1
    # The clip went with its still, so it is hidden too, and comes back on undo.
    assert conn_of(app).execute("SELECT visibility FROM assets WHERE id = ?",
                                (motion,)).fetchone()[0] == db.VIS_HIDDEN
    assert family.get(f"/api/file/{motion}").status_code == 404
    assert admin.get(f"/api/file/{motion}").status_code == 200
    assert admin.post("/api/visibility/undo",
                      json={"batch_id": r.json["batch_id"]}).status_code == 200
    assert family.get(f"/api/file/{motion}").status_code == 200

    admin.post("/api/visibility", json={"ids": [still], "visibility": "public"})
    app.config["LITE"].video_originals = True     # the fake clip cannot be remuxed
    item = guest.get(f"/api/asset/{still}").json
    assert item["is_live"] and guest.get(item["live_src"]).status_code == 200
    # A clip somebody had hidden on its own stays hidden from those below.
    set_vis(app, ["IMG_1234.MOV"], db.VIS_HIDDEN)
    assert guest.get(f"/api/asset/{still}").json["is_live"] is False
    assert guest.get(f"/api/file/{motion}").status_code == 404


def test_an_assigned_folder_keeps_its_clips_to_itself(app, live, library):
    root, _ = library
    c = app.test_client()
    sign_in(app, c, "family", username="chithi", library=str(root / "2019"))
    assert c.get(f"/api/file/{live['IMG_1234.MOV']}").status_code == 404


def test_a_share_link_plays_the_live_photo(app, family, live):
    still, motion = live["IMG_1234.JPG"], live["IMG_1234.MOV"]
    token = share(family, "asset", still)
    stranger = app.test_client()
    # The fake clip cannot be remuxed, and without ffmpeg (or this setting) a
    # link is sent no video at all, the Live Photo's included.
    app.config["LITE"].video_originals = True
    item = stranger.get(f"/api/share/{token}").json["item"]
    assert item["is_live"] and item["live_src"] == f"/api/share/{token}/file/{motion}"
    assert stranger.get(item["live_src"]).status_code == 200
    # Only the clip that goes with the shared still.
    assert stranger.get(f"/api/share/{token}/file/{live['img_2000.mov']}").status_code == 404


def test_a_zip_of_a_live_photo_carries_its_clip(app, family, live):
    r = family.get(f"/api/download/zip?ids={live['IMG_1234.JPG']}")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert sorted(z.namelist()) == ["IMG_1234.JPG", "IMG_1234.MOV"]


def test_a_still_taken_away_leaves_its_video_a_video(app, admin, live, library):
    root, _ = library
    os.remove(root / "iphone" / "IMG_1234.JPG")
    rescan(app)
    shown = [i[0] for i in all_items(admin.get("/api/segments").json)]
    assert live["IMG_1234.MOV"] in shown
    assert conn_of(app).execute("SELECT live_of FROM assets WHERE id = ?",
                                (live["IMG_1234.MOV"],)).fetchone()[0] is None


def test_the_pair_needs_the_same_folder_and_name(app, admin, library):
    root, _ = library
    make_jpeg(root / "a" / "IMG_8.jpg", "2024:02:01 10:00:00")
    make_clip(root / "b" / "IMG_8.MOV", datetime(2024, 2, 1), 2)
    make_clip(root / "a" / "IMG_9.MOV", datetime(2024, 2, 1), 2)
    rescan(app)
    assert conn_of(app).execute("SELECT COUNT(*) FROM assets WHERE live_of IS NOT NULL "
                                "OR live_id IS NOT NULL").fetchone()[0] == 0


def test_the_files_are_never_touched(app, live, library):
    root, _ = library
    folder = root / "iphone"
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
    rescan(app)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
    assert before == after


def test_the_export_says_which_clip_goes_with_which_photo(app, live, library):
    root, _ = library
    out = export.build(conn_of(app), app.config["LITE"])
    assert out["live_photos"] == [{"photo": {"folder": str(root), "path": "iphone/IMG_1234.JPG"},
                                   "video": {"folder": str(root), "path": "iphone/IMG_1234.MOV"}}]


def test_the_viewer_has_a_live_badge_in_both_languages():
    viewer = (STATIC / "js" / "viewer.js").read_text(encoding="utf-8")
    assert "live-badge" in viewer and "item.live_src" in viewer
    for code in ("en", "ta"):
        table = json.loads((STATIC / "i18n" / f"{code}.json").read_text(encoding="utf-8"))
        assert "Play the Live Photo" in table and "LIVE" in table


# --- the index, from version 10 ------------------------------------------------------------


def test_index_version_10_is_brought_to_11_with_a_copy_first(tmp_path):
    data = tmp_path / "data"
    older_index(data, 10)
    old = sqlite3.connect(str(data / db.DB_FILE))
    old.execute("INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at, "
                "date_key, date_source) VALUES (1, '', 'a.jpg', 'jpg', 'picture', 10, 1, 1, "
                "'1970-01-01', 'mtime')")
    old.commit()
    old.close()
    conn = db.connect(data)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) == 11
        row = conn.execute("SELECT name, live_of, live_id, quick_hash, content_hash "
                           "FROM assets").fetchone()
        assert tuple(row) == ("a.jpg", None, None, None, None)
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"assets_grid", "assets_counts", "assets_size", "assets_copies"} <= indexes
        grid = [r[2] for r in conn.execute("PRAGMA index_info(assets_grid)")]
        assert grid[:3] == ["missing", "live_of", "captured_at"]
    finally:
        conn.close()
    assert len(list((data / "backups").glob("before-update-from-index-10-*.zip"))) == 1


def test_the_grid_still_reads_its_page_from_one_index(app):
    c = conn_of(app)
    plan = " ".join(r[3] for r in c.execute(
        "EXPLAIN QUERY PLAN SELECT a.id, a.width, a.height, a.kind, a.visibility, a.duration, "
        "a.thumb, a.thumb_v, a.color, a.date_key, a.live_id FROM assets a WHERE a.missing = 0 AND "
        "a.live_of IS NULL AND a.visibility <= 1 ORDER BY a.captured_at DESC, a.id DESC LIMIT 10"))
    assert "COVERING INDEX assets_grid" in plan and "TEMP B-TREE" not in plan


# --- copies ------------------------------------------------------------------------------


@pytest.fixture()
def dupes(app, library):
    """backup/beach.jpg, a copy of 2019/beach.jpg; and two files of the same
    size and the same ends that differ in the middle (not copies)."""
    root, _ = library
    (root / "backup").mkdir()
    (root / "backup" / "beach.jpg").write_bytes((root / "2019" / "beach.jpg").read_bytes())
    body = bytearray(os.urandom(400_000))
    (root / "scans").mkdir()
    (root / "scans" / "one.png").write_bytes(bytes(body))
    body[200_000] ^= 0xFF
    (root / "scans" / "two.png").write_bytes(bytes(body))
    rescan(app)
    return ids(app)


def test_copies_are_listed_with_where_each_one_is(app, admin, dupes, library):
    root, _ = library
    r = admin.get("/api/admin/duplicates")
    assert r.status_code == 200
    data = r.json
    size = (root / "2019" / "beach.jpg").stat().st_size
    assert (data["total_groups"], data["extra_copies"], data["extra_bytes"]) == (1, 1, size)
    assert data["pending"] == 0 and (data["page"], data["pages"]) == (1, 1)
    (group,) = data["groups"]
    assert group["count"] == 2 and group["size"] == size
    where = sorted((i["dir"], i["name"]) for i in group["items"])
    assert where == [("2019", "beach.jpg"), ("backup", "beach.jpg")]
    first = group["items"][0]
    assert first["folder"] == str(root / "2019") and first["open"] == f"/?photo={first['id']}"
    assert first["date"] == "2019-05-12"
    # The two that only look alike were read whole, and are not copies.
    c = conn_of(app)
    hashes = [r[0] for r in c.execute("SELECT content_hash FROM assets WHERE dir = 'scans'")]
    assert len(hashes) == 2 and all(hashes) and hashes[0] != hashes[1]


def test_only_an_administrator_sees_the_copies(app, family, guest, dupes):
    assert family.get("/api/admin/duplicates").status_code == 403
    assert guest.get("/api/admin/duplicates").status_code == 403
    assert app.test_client().get("/api/admin/duplicates").status_code == 401


def test_each_file_is_read_once_and_again_only_when_it_changes(app, admin, dupes, library,
                                                               monkeypatch):
    root, _ = library
    read = []
    real = copies.quick_hash
    monkeypatch.setattr(copies, "quick_hash", lambda path, size: read.append(path) or real(path, size))
    rescan(app)
    assert read == []
    copy = root / "backup" / "beach.jpg"
    copy.write_bytes(copy.read_bytes() + b"edited")
    os.utime(copy, (time.time() + 5,) * 2)
    rescan(app)
    assert admin.get("/api/admin/duplicates").json["total_groups"] == 0


def test_copies_never_change_a_file(app, dupes, library):
    root, _ = library
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    conn = conn_of(app)
    conn.execute("UPDATE assets SET quick_hash = NULL, content_hash = NULL")
    conn.commit()
    rescan(app)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_a_rescan_asked_for_stops_the_reading(app, dupes):
    s = app.config["SCANNER"]
    conn = conn_of(app)
    conn.execute("UPDATE assets SET quick_hash = NULL, content_hash = NULL")
    conn.commit()
    s._wake.set()
    try:
        assert s._find_copies(conn) == 0
    finally:
        s._wake.clear()
    assert s._find_copies(conn) > 0


def test_the_list_comes_a_page_at_a_time(app, admin, dupes, library, monkeypatch):
    root, _ = library
    (root / "backup" / "sunset.jpg").write_bytes((root / "2019" / "sunset.jpg").read_bytes())
    rescan(app)
    monkeypatch.setattr(api_admin, "COPIES_PAGE", 1)
    first = admin.get("/api/admin/duplicates").json
    second = admin.get("/api/admin/duplicates?page=2").json
    assert (first["pages"], len(first["groups"]), len(second["groups"])) == (2, 1, 1)
    assert first["groups"][0]["items"][0]["id"] != second["groups"][0]["items"][0]["id"]
    assert admin.get("/api/admin/duplicates?page=99").json["page"] == 2


def test_the_copies_list_is_quick_on_100000_files(app):
    conn = conn_of(app)
    folder = conn.execute("SELECT id FROM folders").fetchone()[0]
    rows = []
    for n in range(100_000):
        # One in a hundred has a copy; every size is shared by twenty files.
        same = n - 1 if n % 100 == 1 else n
        rows.append((folder, f"d{n % 50}", f"f{n}.jpg", "jpg", "picture", 1000 + (same % 5000),
                     1.0, 1.0, "2020-01-01", "mtime", f"q{same}", f"h{same}"))
    with conn:
        conn.executemany(
            "INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at, "
            "date_key, date_source, quick_hash, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
    began = time.perf_counter()
    found = copies.totals(lambda sql, params: [tuple(r) for r in conn.execute(sql, params)])
    page = copies.groups(conn, 0, 20)
    took = time.perf_counter() - began
    assert found["groups"] == 1000 and len(page) == 20
    assert took < 3.0, took


def test_the_console_lists_copies_read_only_and_opens_them_in_the_gallery():
    admin_js = (STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    start = admin_js.index("function renderDuplicates")
    body = admin_js[start:admin_js.index("async function loadAssignable")]
    assert "item.open" in body and "delete" not in body.lower()
    page = (STATIC.parent / "templates" / "admin.html").read_text(encoding="utf-8")
    assert 'data-tab="duplicates"' in page and 'data-panel="duplicates"' in page
    assert "openAskedFor" in (STATIC / "js" / "app.js").read_text(encoding="utf-8")


def test_the_scan_pairs_before_it_makes_thumbnails():
    """The clip has no tile of its own, so it is never in the thumbnail queue."""
    assert "live_of IS NULL" in scanner.Scanner._QUEUE["small"][0]
