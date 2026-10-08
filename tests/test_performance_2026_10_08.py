"""Performance review of 2026-10-08 (A120-A122): thumbnails a few at a time,
library counts remembered until the index changes, and every script module
at an address the browser can keep."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from PIL import Image

from ninaivu_lite import db, media
from ninaivu_lite import scanner as scanner_module
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg

# --- A120: thumbnails a few at a time -------------------------------------------------------


def _library(tmp_path: Path, count: int) -> tuple[Scanner, sqlite3.Connection]:
    root = tmp_path / "Photos"
    for i in range(count):
        make_jpeg(root / f"p{i:03}.jpg", f"2020:01:{1 + i % 28:02d} 10:{i % 60:02d}:00",
                  size=(320, 240), color=(i * 3 % 256, 90, 160))
    data = tmp_path / "data"
    return Scanner(data, [str(root)]), db.connect(data)


def test_a120_workers_make_every_thumbnail_and_record_them_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c = _library(tmp_path, 60)
    recorded: list[float] = []
    record = s._record

    def noted(conn, row, *made):
        if "s" in made[0]:
            recorded.append(row["captured_at"])
        record(conn, row, *made)

    monkeypatch.setattr(s, "_record", noted)
    s.scan_once(c)
    rows = c.execute("SELECT id, thumb, large, color FROM assets").fetchall()
    assert len(rows) == 60
    assert all(r["thumb"] == db.THUMB_OK and r["large"] == 1 and r["color"] for r in rows)
    for r in rows:
        for size in ("s", "l"):
            assert media.thumb_path(s.thumbs_dir, r["id"], size).is_file()
    assert recorded == sorted(recorded, reverse=True)      # the top of the timeline first
    assert s.snapshot()["thumbs_left"] == 0


def test_a120_one_worker_and_three_make_the_same_thumbnails(tmp_path, monkeypatch):
    made = {}
    for workers in (1, 3):
        monkeypatch.setattr(scanner_module, "THUMB_WORKERS", workers)
        s, c = _library(tmp_path / str(workers), 12)
        s.scan_once(c)
        made[workers] = sorted((r["name"], r["thumb"], r["large"], r["color"]) for r in c.execute(
            "SELECT name, thumb, large, color FROM assets"))
    assert made[1] == made[3]


def test_a120_a_rescan_asked_for_stops_the_workers_early(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c = _library(tmp_path, 40)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)                                   # indexed, no thumbnails yet
    del s._make_thumbnails
    started: list[int] = []
    render = s._render

    def slow(row, sizes):
        started.append(row["id"])
        if len(started) == 5:
            s.rescan()                               # somebody pressed Rescan
        return render(row, sizes)

    monkeypatch.setattr(s, "_render", slow)
    s._make_thumbnails(c)
    left = c.execute("SELECT COUNT(*) FROM assets WHERE thumb = 0").fetchone()[0]
    assert left > 0                                  # it stopped for the walk ...
    assert len(started) < 20                         # ... without making the rest of the page


def test_a120_a_huge_picture_is_made_alone(tmp_path, monkeypatch):
    """A picture read whole (not a JPEG read at an eighth) is made one at a
    time, so three at once cannot run a small computer out of memory."""
    big = tmp_path / "big.png"
    Image.new("RGB", (500, 400), (10, 200, 30)).save(big)
    small = make_jpeg(tmp_path / "small.jpg", "2020:01:01 10:00:00", size=(1600, 1200))
    monkeypatch.setattr(media, "HEAVY_PIXELS", 150_000)
    held: list[bool] = []
    real = media.save_thumbnails

    def saving(*args, **kwargs):
        held.append(media._HEAVY.locked())
        return real(*args, **kwargs)

    monkeypatch.setattr(media, "save_thumbnails", saving)
    assert media.make_thumbnails(str(big), "picture", tmp_path / "t", 1, ("l",))[0]
    assert media.make_thumbnails(str(small), "picture", tmp_path / "t", 2, ("s",))[0]
    # The PNG is read whole, 500x400 = 200,000 pixels; the larger JPEG,
    # asked for at 256, is read at a quarter (400x300) and goes with others.
    assert held == [True, False]
    assert not media._HEAVY.locked()


def test_a120_workers_leave_a_core_for_the_gallery():
    assert 1 <= scanner_module.THUMB_WORKERS <= 3


# --- A121: counts remembered until the index changes ---------------------------------------


def test_a121_remembered_until_another_connection_commits(tmp_path):
    c = db.connect(tmp_path)
    memory = db.Remembered(tmp_path)
    worked: list[int] = []

    def count():
        worked.append(1)
        return c.execute("SELECT COUNT(*) FROM folders").fetchone()[0]

    assert memory.get("n", count) == 0
    assert memory.get("n", count) == 0 and len(worked) == 1
    other = db.connect(tmp_path)
    with other:
        other.execute("INSERT INTO folders (path) VALUES ('/photos')")
    assert memory.get("n", count) == 1 and len(worked) == 2
    assert memory.get("n", count) == 1 and len(worked) == 2


def test_a121_an_answer_worked_out_while_the_index_changed_is_not_kept(tmp_path):
    c = db.connect(tmp_path)
    memory = db.Remembered(tmp_path)
    other = db.connect(tmp_path)
    worked: list[int] = []

    def count_while_writing():
        worked.append(1)
        n = c.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        if len(worked) == 1:
            with other:
                other.execute("INSERT INTO folders (path) VALUES ('/later')")
        return n

    assert memory.get("n", count_while_writing) == 0
    assert memory.get("n", count_while_writing) == 1 and len(worked) == 2


def test_a121_never_creates_an_index_that_is_not_there(tmp_path):
    memory = db.Remembered(tmp_path / "nowhere")
    assert memory.get("n", lambda: 7) == 7
    assert not (tmp_path / "nowhere" / db.DB_FILE).exists()


def test_a121_from_several_threads_at_once(tmp_path):
    c = db.connect(tmp_path)
    with c:
        c.execute("INSERT INTO folders (path) VALUES ('/photos')")
    memory = db.Remembered(tmp_path)
    answers: list[int] = []

    def ask():
        own = db.connect(tmp_path)
        for _ in range(50):
            answers.append(memory.get("n", lambda: own.execute(
                "SELECT COUNT(*) FROM folders").fetchone()[0]))
        own.close()

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert answers == [1] * 200


def test_a121_counts_follow_every_change_at_once(app, admin, family):
    """A favourite, a visibility change and a scan each show in the counts
    straight away: nothing remembered outlives a change to the index."""
    i = ids(app)
    first = family.get("/api/status/stats").json["stats"]
    grid = family.get("/api/segments").json
    assert family.get("/api/segments").json == grid
    assert family.get("/api/status/stats").json["stats"] == first
    family.post(f"/api/asset/{i['beach.jpg']}", json={"favorite": True})
    assert family.get("/api/status/stats").json["stats"]["favorites"] == first["favorites"] + 1
    flags = {item[0]: item[3] for day in family.get("/api/segments").json["segments"]
             for item in day["items"]}
    assert flags[i["beach.jpg"]] & 1
    assert admin.get("/api/status/stats").json["stats"]["favorites"] == 0
    admin.post("/api/visibility", json={"ids": [i["sunset.jpg"]], "visibility": "hidden"})
    after = family.get("/api/status/stats").json["stats"]
    assert after["count"] == first["count"] - 1
    years = {y["year"]: y["count"] for y in family.get("/api/facets").json["years"]}
    assert years["2019"] == 1                         # beach only: sunset is hidden now
    root = Path(app.config["LITE"].folders[0])
    make_jpeg(root / "2019" / "new.jpg", "2019:06:01 10:00:00")
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))
    assert family.get("/api/status/stats").json["stats"]["count"] == after["count"] + 1
    assert family.get("/api/segments").json["total"] == grid["total"]      # -1 hidden, +1 new
    assert len(family.get("/api/segments?q=new").json["segments"]) == 1
    years = {y["year"]: y["count"] for y in family.get("/api/facets").json["years"]}
    assert years["2019"] == 2
    overview = admin.get("/api/admin/overview").json
    assert overview["stats"]["count"] == overview["library"]["folders"][0]["count"]


def test_a121_a_random_order_is_never_remembered(app, admin):
    orders = {tuple(item[0] for day in admin.get("/api/segments?sort=random").json["segments"]
                    for item in day["items"]) for _ in range(30)}
    assert len(orders) > 1


def test_a121_a_page_too_large_to_keep_is_still_answered(app, admin, monkeypatch):
    from ninaivu_lite import common
    monkeypatch.setattr(common, "PAGE_KEEP_BYTES", 10)
    first = admin.get("/api/segments").json
    assert first["total"] > 0 and admin.get("/api/segments").json == first


# --- A122: every module at an address the browser can keep ---------------------------------


def _import_map(page: str) -> tuple[str, dict]:
    found = re.search(r'<script type="importmap">(.*?)</script>', page, re.S)
    assert found, "no import map"
    return found.group(1), json.loads(found.group(1))


def test_a122_pages_map_every_module_to_its_versioned_address(app, admin):
    static = Path(app.static_folder)
    for path in ("/", "/admin"):
        response = app.test_client().get(path)
        page = response.get_data(as_text=True)
        text, mapping = _import_map(page)
        imports = mapping["imports"]
        for name in ("app.js", "api.js", "grid.js", "i18n.js", "admin.js", "sudar/sudar.js"):
            url = f"/static/js/{name}"
            digest = hashlib.sha256((static / "js" / name).read_bytes()).hexdigest()[:12]
            assert imports[url] == f"{url}?v={digest}"
        assert imports["/static/i18n/ta.json"].startswith("/static/i18n/ta.json?v=")
        # Before the first script, or the page's modules would load unmapped.
        assert page.index('type="importmap"') < page.index('type="module"')
        # The policy allows this one inline script and still no other.
        policy = response.headers["Content-Security-Policy"]
        allowed = "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        assert f"script-src 'self' '{allowed}';" in policy
        assert "unsafe-inline" not in policy.split("script-src")[1].split(";")[0]


def test_a122_a_mapped_module_is_kept_for_a_year(app):
    c = app.test_client()
    _text, mapping = _import_map(c.get("/").get_data(as_text=True))
    response = c.get(mapping["imports"]["/static/js/api.js"])
    assert response.status_code == 200
    assert "immutable" in response.headers["Cache-Control"]


def test_a122_share_page_has_the_map_too(app, admin):
    i = ids(app)
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": i["beach.jpg"]}).json
    token = token.get("token") or token["share"]["token"]
    page = app.test_client().get(f"/share/{token}").get_data(as_text=True)
    assert _import_map(page)[1]["imports"]["/static/js/share.js"].startswith("/static/js/share.js?v=")


def test_a122_translations_are_fetched_at_their_mapped_address(app):
    script = (Path(app.static_folder) / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "import.meta.resolve?.(plain) ?? plain" in script
    assert "fetch(stringsUrl(code)" in script
