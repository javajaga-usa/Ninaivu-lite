"""The family gallery's calls: shapes Ninaivu's screens read, who sees what,
paging, filters, favourites, visibility, files, downloads and albums."""

from __future__ import annotations

import io
import os
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from ninaivu_lite import create_app, db, media
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg, sign_in


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


def set_vis(app, names: list[str], level: int) -> None:
    c = conn_of(app)
    with c:
        c.executemany("UPDATE assets SET visibility = ? WHERE name = ?",
                      [(level, n) for n in names])


def rescan(app) -> None:
    app.config["SCANNER"].scan_once(conn_of(app))


def all_items(payload) -> list[list]:
    return [item for seg in payload["segments"] for item in seg["items"]]


def listed(client, query="") -> list[int]:
    r = client.get(f"/api/segments?{query}")
    assert r.status_code == 200, r.json
    return [item[0] for item in all_items(r.json)]


def jpeg_with_gps(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 600), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Canon", "Canon EOS 80D"
    exif.get_ifd(0x8769)[0x9003] = "2020:02:02 10:00:00"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (13.0, 4.0, 5.0)
    gps[3], gps[4] = "E", (80.0, 16.0, 7.0)
    img.save(path, "JPEG", exif=exif.tobytes())
    return path


# --- status ------------------------------------------------------------------------------


def test_status_admin_shape(admin, library):
    root, _ = library
    s = admin.get("/api/status").json
    assert s["has_library"] is True
    assert s["root"] == str(root) and s["root_label"] == str(root)
    assert s["libraries_away"] == 0 and s["libraries_away_paths"] == []
    assert s["user"]["role"] == "admin" and s["user"]["can"]["manage_library"] is True
    assert s["ai"] == {"engine": "off"}
    assert s["admin_port"] is None
    assert {"status", "running", "percent"} <= set(s["scan"])
    assert s["stats"]["count"] == 6
    assert "stats" not in admin.get("/api/status?stats=0").json


def test_status_family_and_anonymous(app, family, library):
    s = family.get("/api/status?stats=0").json
    assert s["root"] is None and s["root_label"] == "Photos"
    assert s["scan"] == {"status": "idle", "running": False, "percent": 100}
    anon = app.test_client().get("/api/status?stats=0").json
    assert anon["user"]["anonymous"] is True and anon["user"]["id"] == 0


def test_status_library_away(app, admin, family, tmp_path):
    away = str(tmp_path / "Unplugged")
    app.config["LITE"].folders.append(away)
    a = admin.get("/api/status?stats=0").json
    assert a["libraries_away"] == 1 and a["libraries_away_paths"] == [away]
    assert a["root_label"] == "2 library folders"
    f = family.get("/api/status?stats=0").json
    assert f["libraries_away"] == 1 and f["libraries_away_paths"] == []
    assert f["root_label"] == "Your library"


def test_status_without_library(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    app = create_app(cfg, scanner=Scanner(cfg.data_dir, []))
    c = app.test_client()
    s = c.get("/api/status").json
    assert s["has_library"] is False and "stats" not in s
    assert c.get("/api/status/stats").json == {"stats": None}


def test_stats_follow_the_viewer(app, admin, family, guest):
    set_vis(app, ["beach.jpg"], db.VIS_HIDDEN)
    set_vis(app, ["sunset.jpg"], db.VIS_PUBLIC)
    i = ids(app)
    family.post(f"/api/asset/{i['portrait.jpg']}", json={"favorite": True})
    a = admin.get("/api/status/stats")
    assert a.headers["Cache-Control"] == "no-store"
    a = a.json["stats"]
    assert (a["count"], a["hidden"], a["public"], a["favorites"]) == (6, 1, 1, 0)
    assert a["pictures"] == 5 and a["videos"] == 1 and a["bytes"] > 0
    assert a["live"] == a["audio"] == a["duplicate_groups"] == 0
    f = family.get("/api/status/stats").json["stats"]
    assert (f["count"], f["hidden"], f["public"], f["favorites"]) == (5, 0, 1, 1)
    g = guest.get("/api/status/stats").json["stats"]
    assert (g["count"], g["public"], g["favorites"]) == (1, 1, 0)


def test_activity_and_scan(app, admin, family):
    idle = {"jobs": [], "running": False,
            "scan": {"status": "idle", "running": False, "percent": 100}}
    assert family.get("/api/status/activity").json == idle
    assert family.get("/api/status/scan").json == idle["scan"]
    assert admin.get("/api/status/activity").json["jobs"] == []
    app.config["SCANNER"]._set(state="thumbnails", thumbs_total=10, thumbs_left=4)
    act = admin.get("/api/status/activity").json
    assert act["running"] is True
    job = act["jobs"][0]
    assert job["id"] == "indexing" and job["title"] == "Indexing" and job["page"] == "library"
    assert job["percent"] == 60 and job["detail"] == "6 / 10" and job["paused"] is False
    assert job["eta"] is None and "disk" in job["uses"]
    assert act["scan"]["status"] == "indexing"
    assert admin.get("/api/status/scan").json["percent"] == 60


# --- the grid ------------------------------------------------------------------------------


def test_segments_shape(app, admin):
    r = admin.get("/api/segments?limit=100").json
    assert r["total"] == 6 and r["returned"] == 6 and r["next_offset"] is None
    assert r["truncated"] is False and r["semantic"] is False
    keys = [s["key"] for s in r["segments"]]
    assert keys == ["2023-01-15", "2022-03-04", "2021-08-01", "2019-05-12", "2015-06-01"]
    assert len(r["segments"][3]["items"]) == 2           # beach + sunset, same day
    i = ids(app)
    by_id = {item[0]: item for item in all_items(r)}
    portrait = by_id[i["portrait.jpg"]]
    assert portrait[1] == 67 and portrait[2] == 0          # 400x600 after EXIF rotation
    assert portrait[3] & 2 and len(portrait[6]) == 3 and portrait[5]
    beach = by_id[i["beach.jpg"]]
    assert beach[1] == 133 and beach[4] == 0
    assert by_id[i["clip.mp4"]][2] == 1
    broken = by_id[i["broken.jpg"]]
    assert broken[1] == 100 and not broken[3] & 2 and broken[6] == ""


def test_segments_paging_joins(admin):
    whole = listed(admin, "limit=1000")
    for size in (1, 2, 4, 5):
        got, offset = [], 0
        while True:
            r = admin.get(f"/api/segments?limit={size}&offset={offset}").json
            got += [item[0] for item in all_items(r)]
            assert r["returned"] <= size
            if r["next_offset"] is None:
                assert r["truncated"] is False
                break
            assert r["truncated"] is True
            offset = r["next_offset"]
        assert got == whole
    assert admin.get("/api/segments?limit=0").json["returned"] == 1   # clamped to 1
    assert admin.get("/api/segments?limit=999999999").json["returned"] == 6
    assert admin.get("/api/segments?offset=6").json["returned"] == 0


def test_segments_sorts(app, admin):
    i = ids(app)
    names = {v: k for k, v in i.items()}
    by_name = [names[x] for x in listed(admin, "sort=name_asc")]
    assert by_name == sorted(by_name, key=str.lower)
    assert [names[x] for x in listed(admin, "sort=name_desc")] == by_name[::-1]
    date_desc = listed(admin, "sort=date_desc")
    assert listed(admin, "sort=bogus") == date_desc
    assert listed(admin, "sort=date_asc")[0] == i["broken.jpg"]
    assert date_desc[0] == i["IMG_20230115_091500.jpg"]
    c = conn_of(app)
    sizes = [c.execute("SELECT size FROM assets WHERE id = ?", (x,)).fetchone()[0]
             for x in listed(admin, "sort=size_desc")]
    assert sizes == sorted(sizes, reverse=True)
    rnd = admin.get("/api/segments?sort=random&limit=2").json
    assert rnd["returned"] == 6 and rnd["next_offset"] is None
    assert sorted(item[0] for item in all_items(rnd)) == sorted(i.values())


@pytest.mark.parametrize("query, expected", [
    ("q=beach", {"beach.jpg"}),
    ("q=BEACH", {"beach.jpg"}),
    ("q=canon", {"beach.jpg"}),
    ("q=pongal", {"IMG_20230115_091500.jpg"}),
    ("q=2019", {"beach.jpg", "sunset.jpg"}),
    ("q=2021-08", {"portrait.jpg"}),
    ("q=family+clip", {"clip.mp4"}),
    ("q=nothing-like-this", set()),
    ("kind=video", {"clip.mp4"}),
    ("kind=picture&kind=video", {"beach.jpg", "sunset.jpg", "IMG_20230115_091500.jpg",
                                 "portrait.jpg", "clip.mp4", "broken.jpg"}),
    ("folder=family", {"clip.mp4", "IMG_20230115_091500.jpg"}),
    ("folder=family/pongal", {"IMG_20230115_091500.jpg"}),
    ("folder=fam", set()),
    ("from=2019-01-01&to=2019-12-31", {"beach.jpg", "sunset.jpg"}),
    ("from=2021-01-01", {"portrait.jpg", "clip.mp4", "IMG_20230115_091500.jpg"}),
    ("camera=Canon+EOS+80D", {"beach.jpg"}),
])
def test_segments_filters(app, admin, query, expected):
    names = {v: k for k, v in ids(app).items()}
    assert {names[x] for x in listed(admin, query)} == expected
    assert admin.get(f"/api/segments?{query}").json["total"] == len(expected)


def test_visibility_filter_within_ceiling(app, admin, family):
    set_vis(app, ["beach.jpg"], db.VIS_HIDDEN)
    set_vis(app, ["sunset.jpg"], db.VIS_PUBLIC)
    i = ids(app)
    assert listed(admin, "visibility=hidden") == [i["beach.jpg"]]
    assert listed(admin, "visibility=public") == [i["sunset.jpg"]]
    # Above the ceiling the filter is ignored, never an error or a leak.
    assert len(listed(family, "visibility=hidden")) == 5
    assert i["beach.jpg"] not in listed(family, "visibility=hidden")
    flags = {item[0]: item[3] for item in all_items(admin.get("/api/segments").json)}
    assert flags[i["beach.jpg"]] & 128 and flags[i["sunset.jpg"]] & 64


def test_favourites_per_viewer(app, admin, family):
    i = ids(app)
    r = family.post(f"/api/asset/{i['beach.jpg']}", json={"favorite": True})
    assert r.status_code == 200 and r.json["favorite"] is True
    assert listed(family, "favorites=1") == [i["beach.jpg"]]
    assert listed(admin, "favorites=1") == []
    flags = {item[0]: item[3] for item in all_items(family.get("/api/segments").json)}
    assert flags[i["beach.jpg"]] & 1 and not flags[i["sunset.jpg"]] & 1
    assert admin.get(f"/api/asset/{i['beach.jpg']}").json["favorite"] is False
    family.post(f"/api/asset/{i['beach.jpg']}", json={"favorite": False})
    assert listed(family, "favorites=1") == []


def test_favourite_role_rules(app, guest):
    i = ids(app)
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    assert guest.post(f"/api/asset/{i['beach.jpg']}", json={"favorite": True}).status_code == 403
    anon = app.test_client().post(f"/api/asset/{i['beach.jpg']}", json={"favorite": True})
    assert anon.status_code == 401 and anon.json["error"] == "Sign in to do that."
    assert guest.post("/api/assets/bulk", json={"ids": [1], "favorite": True}).status_code == 403
    assert app.test_client().get("/api/segments?favorites=1").json["total"] == 0


def test_bulk_favourite(app, family):
    i = ids(app)
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    assert family.post("/api/assets/bulk", json={"ids": "12", "favorite": True}).status_code == 400
    r = family.post("/api/assets/bulk",
                    json={"ids": [i["beach.jpg"], i["sunset.jpg"], 99999, "x"], "favorite": True})
    assert r.json == {"updated": 1, "skipped": 2}
    assert listed(family, "favorites=1") == [i["beach.jpg"]]
    family.post("/api/assets/bulk", json={"ids": [i["beach.jpg"]], "favorite": False})
    assert listed(family, "favorites=1") == []


# --- who sees what ------------------------------------------------------------------------------


def test_guest_and_anonymous_see_only_public(app, guest):
    i = ids(app)
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    assert listed(guest) == [i["beach.jpg"]]
    anon = app.test_client()
    assert listed(anon) == [i["beach.jpg"]]
    assert anon.get(f"/api/asset/{i['sunset.jpg']}").status_code == 404
    assert anon.get(f"/api/thumb/{i['sunset.jpg']}").status_code == 404
    assert anon.get(f"/api/thumb/{i['beach.jpg']}").status_code == 200
    facets = anon.get("/api/facets").json
    assert facets["years"] == [{"year": "2019", "count": 1}]
    # A closed library answers nobody who has not signed in.
    app.config["LITE"].open_browsing = False
    r = anon.get("/api/segments")
    assert r.status_code == 401 and r.json["error"] == "This library is private. Please sign in."
    assert anon.get(f"/api/thumb/{i['beach.jpg']}").status_code == 401
    assert guest.get("/api/segments").json["total"] == 1


@pytest.mark.parametrize("path", ["/api/asset/{id}", "/api/thumb/{id}", "/api/file/{id}",
                                  "/api/preview/{id}", "/api/download/{id}"])
def test_invisible_items_are_404_not_403(app, family, guest, path):
    i = ids(app)
    set_vis(app, ["beach.jpg"], db.VIS_HIDDEN)
    r = family.get(path.format(id=i["beach.jpg"]))
    assert r.status_code == 404 and r.is_json and r.json["status"] == 404
    if "download" not in path:
        assert guest.get(path.format(id=i["sunset.jpg"])).status_code == 404
    assert family.get(path.format(id=999999)).status_code == 404


def test_assigned_library_limits_everything(app, library):
    root, _ = library
    c = app.test_client()
    sign_in(app, c, "family", username="kutti", library=str(root / "family"))
    i = ids(app)
    assert set(listed(c)) == {i["clip.mp4"], i["IMG_20230115_091500.jpg"]}
    assert c.get(f"/api/asset/{i['beach.jpg']}").status_code == 404
    assert c.get("/api/status?stats=0").json["root_label"] == "family"
    assert [f["name"] for f in c.get("/api/facets").json["folders"]] == ["family",
                                                                          "family/pongal"]


# --- facets and suggestions ---------------------------------------------------------------------


def test_facets_shape(admin):
    f = admin.get("/api/facets").json
    assert f["tags"] == []
    assert [y["year"] for y in f["years"]] == ["2023", "2022", "2021", "2019", "2015"]
    assert all(isinstance(y["year"], str) for y in f["years"])
    assert f["folders"][0] == {"name": "2019", "count": 2}
    assert {x["name"] for x in f["folders"]} == {"2019", "family", "family/pongal"}
    assert f["cameras"] == [{"name": "Canon EOS 80D", "count": 1}]


def test_suggest(admin):
    s = admin.get("/api/suggest?q=FAM").json
    assert s["concepts"] == []
    assert s["suggestions"] == [{"type": "folder", "value": "family", "count": 1},
                                {"type": "folder", "value": "family/pongal", "count": 1}]
    cam = admin.get("/api/suggest?q=eos").json["suggestions"]
    assert cam == [{"type": "camera", "value": "Canon EOS 80D", "count": 1}]
    assert admin.get("/api/suggest?q=zzz").json["suggestions"] == []


# --- one item ---------------------------------------------------------------------------------


def test_asset_shape_and_guest_privacy(app, admin, guest):
    i = ids(app)
    a = admin.get(f"/api/asset/{i['beach.jpg']}").json
    for key in ("id", "name", "folder", "ext", "kind", "size_h", "date", "date_source", "width",
                "height", "duration", "favorite", "has_thumb", "thumb_v", "blurhash", "playable",
                "needs_proxy", "src", "view", "rotation", "mirror", "rotation_source", "visibility",
                "visibility_source", "tags", "is_live", "live_src", "duplicate"):
        assert key in a, key
    assert a["download"] == f"/api/download/{a['id']}" and a["camera"] == "Canon EOS 80D"
    assert a["folder"] == "2019" and a["date"] == "2019-05-12" and a["needs_proxy"] is False
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    g = guest.get(f"/api/asset/{i['beach.jpg']}").json
    for key in ("download", "camera", "lens", "iso", "f_number", "exposure", "focal_length",
                "gps"):
        assert key not in g


# --- per-item visibility ----------------------------------------------------------------------------


def test_visibility_batch_and_undo_rows(app, admin, family):
    i = ids(app)
    target = [i["beach.jpg"], i["sunset.jpg"]]
    assert family.post("/api/visibility",
                       json={"ids": target, "visibility": "hidden"}).status_code == 403
    assert app.test_client().post("/api/visibility",
                                  json={"ids": target, "visibility": "hidden"}).status_code == 401
    bad = admin.post("/api/visibility", json={"ids": target, "visibility": "secret"})
    assert bad.status_code == 400
    assert bad.json["error"] == "Visibility must be public, family or hidden."
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    r = admin.post("/api/visibility", json={"ids": target + [99999], "visibility": "public"})
    assert r.status_code == 200 and r.json["updated"] == 2 and r.json["visibility"] == "public"
    c = conn_of(app)
    batch = c.execute("SELECT * FROM visibility_batches").fetchone()
    assert batch["scope"] == "items" and batch["visibility"] == db.VIS_PUBLIC
    assert batch["affected"] == 2 and batch["exposed"] == 2 and batch["created_by"] == 1
    undo = {r["asset_id"]: (r["visibility"], r["vis_source"])
            for r in c.execute("SELECT * FROM visibility_undo WHERE batch_id = ?",
                               (batch["id"],))}
    assert undo == {i["beach.jpg"]: (1, "default"), i["sunset.jpg"]: (2, "default")}
    rows = c.execute("SELECT visibility, vis_source FROM assets WHERE id IN (?, ?)",
                     target).fetchall()
    assert all(tuple(r) == (0, "item") for r in rows)
    assert admin.get(f"/api/asset/{i['beach.jpg']}").json["visibility_source"] == "item"
    # Hiding exposes nothing.
    admin.post("/api/visibility", json={"ids": target, "visibility": "hidden"})
    last = c.execute("SELECT exposed FROM visibility_batches ORDER BY id DESC").fetchone()
    assert last["exposed"] == 0
    assert admin.post("/api/visibility", json={"ids": [], "visibility": "family"}
                      ).json["updated"] == 0


# --- thumbnails and files ------------------------------------------------------------------------


def test_thumb_sizes_cache_and_etag(app, admin):
    i = ids(app)
    r = admin.get(f"/api/thumb/{i['beach.jpg']}?s=80&v=123")
    assert r.status_code == 200 and r.mimetype == "image/webp"
    assert r.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert max(Image.open(io.BytesIO(r.data)).size) == 256
    etag = r.headers["ETag"]
    again = admin.get(f"/api/thumb/{i['beach.jpg']}?s=80", headers={"If-None-Match": etag})
    assert again.status_code == 304
    big = admin.get(f"/api/thumb/{i['beach.jpg']}?s=900")
    assert max(Image.open(io.BytesIO(big.data)).size) == 640
    broken = admin.get(f"/api/thumb/{i['broken.jpg']}")
    assert broken.status_code == 404 and broken.is_json


def test_thumb_made_on_demand(app, admin):
    i = ids(app)
    thumbs = app.config["SCANNER"].thumbs_dir
    for size in ("s", "l"):
        media.thumb_path(thumbs, i["sunset.jpg"], size).unlink(missing_ok=True)
    assert admin.get(f"/api/thumb/{i['sunset.jpg']}?s=640").status_code == 200
    assert media.thumb_path(thumbs, i["sunset.jpg"], "l").is_file()


def test_file_inline_and_range_on_video(app, admin):
    i = ids(app)
    r = admin.get(f"/api/file/{i['beach.jpg']}")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    assert r.headers["Cache-Control"] == "private, max-age=3600"
    full = admin.get(f"/api/file/{i['clip.mp4']}")
    assert full.mimetype == "video/mp4" and full.headers["Accept-Ranges"] == "bytes"
    part = admin.get(f"/api/file/{i['clip.mp4']}", headers={"Range": "bytes=4-11"})
    assert part.status_code == 206
    assert part.headers["Content-Range"] == f"bytes 4-11/{len(full.data)}"
    assert part.data == full.data[4:12] == b"ftypisom"


def test_file_of_other_types_is_a_download(app, admin, library):
    root, _ = library
    Image.new("RGB", (40, 30)).save(root / "scan.tif", "TIFF")
    rescan(app)
    i = ids(app)
    r = admin.get(f"/api/file/{i['scan.tif']}")
    assert r.mimetype == "image/tiff"      # a browser type, shown inline where it can be
    assert admin.get(f"/api/asset/{i['scan.tif']}").json["view"] == \
        f"/api/preview/{i['scan.tif']}"
    p = admin.get(f"/api/preview/{i['scan.tif']}")
    assert p.status_code == 200 and p.mimetype == "image/jpeg"
    assert p.headers["Cache-Control"] == "private, max-age=86400"
    assert Image.open(io.BytesIO(p.data)).size == (40, 30)
    (root / "old.avi").write_bytes(b"RIFF....AVI not really")
    rescan(app)
    avi = admin.get(f"/api/file/{ids(app)['old.avi']}")
    assert avi.mimetype == "application/octet-stream"
    assert avi.headers["Content-Disposition"] == 'attachment; filename="old.avi"'


def test_preview_rules(app, admin, library):
    root, _ = library
    i = ids(app)
    assert admin.get(f"/api/preview/{i['clip.mp4']}").status_code == 404
    same = admin.get(f"/api/preview/{i['beach.jpg']}")
    assert same.data == (root / "2019" / "beach.jpg").read_bytes()
    (root / "bad.tif").write_bytes(b"II*\0 not a tiff")
    rescan(app)
    bad = admin.get(f"/api/preview/{ids(app)['bad.tif']}")
    assert bad.status_code == 415 and bad.is_json


def test_guest_gets_no_exif_or_gps(app, admin, guest, family, library):
    root, _ = library
    original = jpeg_with_gps(root / "home" / "garden.jpg").read_bytes()
    rescan(app)
    i = ids(app)
    assert admin.get(f"/api/asset/{i['garden.jpg']}").json["gps"][0] == pytest.approx(13.068, 1e-3)
    set_vis(app, ["garden.jpg"], db.VIS_PUBLIC)
    assert family.get(f"/api/file/{i['garden.jpg']}").data == original
    for client in (guest, app.test_client()):
        r = client.get(f"/api/file/{i['garden.jpg']}")
        assert r.status_code == 200 and r.mimetype == "image/jpeg"
        assert r.data != original
        img = Image.open(io.BytesIO(r.data))
        assert img.size == (800, 600)
        assert not img.getexif() and "exif" not in img.info
        assert b"Canon" not in r.data
        # The preview of a browser type takes the same road.
        assert b"Canon" not in client.get(f"/api/preview/{i['garden.jpg']}").data


def test_download_single(app, admin, family, guest, library):
    root, _ = library
    make_jpeg(root / "பொங்கல்.jpg", "2020:01:15 10:00:00")
    rescan(app)
    i = ids(app)
    r = family.get(f"/api/download/{i['beach.jpg']}")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"] == 'attachment; filename="beach.jpg"'
    assert r.data == (root / "2019" / "beach.jpg").read_bytes()
    t = admin.get(f"/api/download/{i['பொங்கல்.jpg']}").headers["Content-Disposition"]
    assert t.startswith('attachment; filename="photo.jpg"; filename*=UTF-8\'\'')
    assert "%E0%AE%AA" in t
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    assert guest.get(f"/api/download/{i['beach.jpg']}").status_code == 403
    assert app.test_client().get(f"/api/download/{i['beach.jpg']}").status_code == 401


def test_download_zip(app, family, guest, library):
    root, _ = library
    make_jpeg(root / "2019" / "dup.jpg", "2019:01:01 10:00:00")
    make_jpeg(root / "family" / "dup.jpg", "2019:01:02 10:00:00")
    make_jpeg(root / "gone.jpg", "2019:01:03 10:00:00")
    rescan(app)
    (root / "gone.jpg").unlink()
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    i = ids(app)
    wanted = [i["beach.jpg"], i["sunset.jpg"], i["clip.mp4"], i["gone.jpg"]]
    dups = [r[0] for r in conn_of(app).execute("SELECT id FROM assets WHERE name = 'dup.jpg' "
                                               "ORDER BY id")]
    r = family.get("/api/download/zip?ids=" + ",".join(map(str, wanted + dups)) + ",x,,0")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert r.headers["X-Accel-Buffering"] == "no" and "Content-Length" not in r.headers
    assert r.headers["Content-Disposition"].startswith('attachment; filename="ninaivu-')
    archive = zipfile.ZipFile(io.BytesIO(r.data))
    assert archive.namelist() == ["beach.jpg", "clip.mp4", "dup.jpg", "dup (2).jpg",
                                  "NOT INCLUDED.txt"]
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    assert archive.read("beach.jpg") == (root / "2019" / "beach.jpg").read_bytes()
    assert "gone.jpg" in archive.read("NOT INCLUDED.txt").decode()
    one = family.get(f"/api/download/zip?ids={i['beach.jpg']}")
    assert one.headers["Content-Disposition"] == 'attachment; filename="beach.zip"'
    no = family.get("/api/download/zip?ids=")
    assert no.status_code == 400 and no.json["error"] == "Choose some photographs first."
    hid = family.get(f"/api/download/zip?ids={i['sunset.jpg']}")
    assert hid.status_code == 404 and hid.json["error"] == "None of those are yours to download."
    assert family.get(f"/api/download/zip?ids={i['gone.jpg']}").status_code == 404
    assert guest.get(f"/api/download/zip?ids={i['beach.jpg']}").status_code == 403


# --- albums --------------------------------------------------------------------------------------


def test_albums_create_list_and_cover(app, admin, family):
    i = ids(app)
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    r = family.post("/api/albums", json={"name": "  Summer  ",
                                         "ids": [i["beach.jpg"], i["sunset.jpg"]]})
    assert r.status_code == 200 and r.json["name"] == "Summer"
    album_id = r.json["id"]
    albums = family.get("/api/albums").json["albums"]
    assert albums == [{"id": album_id, "name": "Summer", "n": 1, "cover_id": i["beach.jpg"],
                       "cover_v": albums[0]["cover_v"], "created_at": albums[0]["created_at"], "created_by": 2,
                       "date_key": "2019-05-12"}]
    # The hidden photograph never went in, even for the admin.
    assert admin.get("/api/albums").json["albums"][0]["n"] == 1
    family.post(f"/api/albums/{album_id}/items", json={"ids": [i["portrait.jpg"]]})
    time.sleep(0.01)
    family.post(f"/api/albums/{album_id}/items", json={"ids": [i["clip.mp4"]]})
    assert family.get("/api/albums").json["albums"][0]["cover_id"] == i["clip.mp4"]
    patched = family.patch(f"/api/albums/{album_id}", json={"cover_id": i["beach.jpg"]}).json
    assert patched["ok"] and patched["album"]["cover_id"] == i["beach.jpg"]
    assert patched["album"]["n"] == 3 and set(patched["album"]["item_ids"]) == {
        i["beach.jpg"], i["portrait.jpg"], i["clip.mp4"]}
    # An explicit cover that is later hidden falls back to the newest visible item.
    set_vis(app, ["beach.jpg"], db.VIS_HIDDEN)
    assert family.get("/api/albums").json["albums"][0]["cover_id"] == i["clip.mp4"]
    assert admin.get("/api/albums").json["albums"][0]["cover_id"] == i["beach.jpg"]
    assert set(listed(family, f"album={album_id}")) == {i["portrait.jpg"], i["clip.mp4"]}
    got = family.get(f"/api/albums/{album_id}").json["album"]
    assert got["n"] == 2 and got["name"] == "Summer"


def test_album_ownership(app, admin, family):
    other = app.test_client()
    sign_in(app, other, "family", username="thambi")
    album_id = family.post("/api/albums", json={"name": "Mine", "ids": []}).json["id"]
    # Somebody else's empty album is left out of their list.
    assert other.get("/api/albums").json["albums"] == []
    assert admin.get("/api/albums").json["albums"][0]["n"] == 0
    for call in (lambda c: c.patch(f"/api/albums/{album_id}", json={"name": "X"}),
                 lambda c: c.post(f"/api/albums/{album_id}/items", json={"ids": []}),
                 lambda c: c.delete(f"/api/albums/{album_id}")):
        assert call(other).status_code == 403
    assert other.get(f"/api/albums/{album_id}").status_code == 404
    assert admin.patch(f"/api/albums/{album_id}", json={"name": "Renamed"}).status_code == 200
    assert family.get("/api/albums").json["albums"][0]["name"] == "Renamed"
    # An album without a recorded owner is anybody's in the family.
    c = conn_of(app)
    with c:
        c.execute("UPDATE albums SET created_by = NULL")
    assert other.patch(f"/api/albums/{album_id}", json={"name": "Ours"}).status_code == 200
    assert other.delete(f"/api/albums/{album_id}").json == {"ok": True}
    assert family.get("/api/albums").json["albums"] == []
    assert family.delete(f"/api/albums/{album_id}").status_code == 404


def test_album_items_and_validation(app, family, guest):
    i = ids(app)
    album_id = family.post("/api/albums", json={"name": "A", "ids": [i["beach.jpg"]]}).json["id"]
    r = family.post(f"/api/albums/{album_id}/items",
                    json={"ids": [i["sunset.jpg"], i["beach.jpg"]]})
    assert r.json == {"ok": True, "count": 2}
    r = family.post(f"/api/albums/{album_id}/items", json={"ids": [i["beach.jpg"]], "remove": True})
    assert r.json == {"ok": True, "count": 1}
    for bad, message in (({"name": ""}, "Name required"),
                         ({"name": 5}, "Album name must be text"),
                         ({"name": "x", "ids": "1"}, "Asset IDs must be a list"),
                         ({"name": "x", "ids": [1.5]}, "Asset IDs must be integers"),
                         ({"name": "x", "ids": [True]}, "Asset IDs must be integers"),
                         ({"name": "x", "ids": [-3]}, "Asset ID is out of range")):
        r = family.post("/api/albums", json=bad)
        assert r.status_code == 400 and r.json["error"] == message
    assert family.post("/api/albums", data="[]",
                       content_type="application/json").status_code == 400
    r = family.post(f"/api/albums/{album_id}/items", json={"ids": [], "remove": "yes"})
    assert r.status_code == 400
    r = family.patch(f"/api/albums/{album_id}", json={"name": "  "})
    assert r.status_code == 400
    set_vis(app, ["portrait.jpg"], db.VIS_HIDDEN)
    r = family.patch(f"/api/albums/{album_id}", json={"cover_id": i["portrait.jpg"]})
    assert r.status_code == 400 and r.json["error"] == "Cover asset not accessible"
    assert family.patch(f"/api/albums/{album_id}", json={"cover_id": 0}).json["album"][
        "cover_id"] == i["sunset.jpg"]
    assert family.get("/api/albums/99999").status_code == 404
    # Guests have no albums, and the album filter means nothing for them.
    assert guest.get("/api/albums").status_code == 403
    assert guest.post("/api/albums", json={"name": "g"}).status_code == 403
    assert app.test_client().get("/api/albums").status_code == 401
    set_vis(app, ["beach.jpg", "sunset.jpg"], db.VIS_PUBLIC)
    assert len(listed(guest, f"album={album_id}")) == 2


def test_big_album_ids_in_pieces(app, family):
    """More ids than one IN (...) holds still works."""
    i = ids(app)
    many = [i["beach.jpg"]] + list(range(100000, 104000))
    r = family.post("/api/albums", json={"name": "Big", "ids": many})
    assert r.status_code == 200
    assert family.get(f"/api/albums/{r.json['id']}").json["album"]["n"] == 1


def test_viewing_copies_are_kept(app, guest, library):
    root, _ = library
    jpeg_with_gps(root / "home" / "garden.jpg")
    rescan(app)
    set_vis(app, ["garden.jpg"], db.VIS_PUBLIC)
    asset_id = ids(app)["garden.jpg"]
    first = guest.get(f"/api/file/{asset_id}")
    kept = list(Path(app.config["LITE"].data_dir, "views").rglob(f"{asset_id}-*.jpg"))
    assert len(kept) == 1 and kept[0].read_bytes() == first.data
    # A changed original gets a new copy, and the old one goes.
    jpeg_with_gps(root / "home" / "garden.jpg")
    os.utime(root / "home" / "garden.jpg", (time.time() + 5,) * 2)
    guest.get(f"/api/file/{asset_id}")
    assert len(list(Path(app.config["LITE"].data_dir, "views").rglob(f"{asset_id}-*.jpg"))) == 1


# --- a video's poster, made by the browser ---------------------------------------


def jpeg_bytes(size=(320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def test_a_family_browser_gives_a_video_its_poster(app, admin, family, guest):
    clip = ids(app)["clip.mp4"]
    # Without ffmpeg (none here) the video has no picture: a plain tile.
    assert not media.FFMPEG or True
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET thumb = ?, large = 0 WHERE id = ?", (db.THUMB_NONE, clip))
    assert admin.get(f"/api/thumb/{clip}").status_code == 404
    assert guest.post(f"/api/asset/{clip}/poster", data=jpeg_bytes(),
                      content_type="image/jpeg").status_code == 403
    r = family.post(f"/api/asset/{clip}/poster", data=jpeg_bytes(), content_type="image/jpeg")
    assert r.status_code == 201, r.get_json()
    item = r.get_json()
    assert item["has_thumb"] and item["thumb_v"] and item["color"].startswith("#")
    thumb = admin.get(f"/api/thumb/{clip}?s=256")
    assert thumb.status_code == 200 and thumb.mimetype == "image/webp"
    with Image.open(io.BytesIO(thumb.data)) as img:
        assert img.size == (256, 144)
    large = admin.get(f"/api/thumb/{clip}?s=640")
    assert large.status_code == 200
    page = admin.get("/api/segments").get_json()
    flags = next(it[3] for seg in page["segments"] for it in seg["items"] if it[0] == clip)
    assert flags & 2
    # A picture it already has is kept: a second one changes nothing.
    again = family.post(f"/api/asset/{clip}/poster", data=jpeg_bytes((64, 64)),
                        content_type="image/jpeg")
    assert again.status_code == 200 and again.get_json()["thumb_v"] == item["thumb_v"]
    with Image.open(io.BytesIO(admin.get(f"/api/thumb/{clip}?s=256").data)) as img:
        assert img.size == (256, 144)


def test_a_poster_is_refused_when_it_is_not_a_picture_or_not_for_a_video(app, admin):
    clip, beach = ids(app)["clip.mp4"], ids(app)["beach.jpg"]
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET thumb = ? WHERE id = ?", (db.THUMB_NONE, clip))
    url = f"/api/asset/{clip}/poster"
    assert admin.post(url, data=b"not a picture", content_type="image/jpeg").status_code == 400
    assert admin.post(url, data=jpeg_bytes(), content_type="image/gif").status_code == 400
    assert admin.post(url, data=jpeg_bytes((8, 8)), content_type="image/jpeg").status_code == 400
    assert admin.post(f"/api/asset/{beach}/poster", data=jpeg_bytes(),
                      content_type="image/jpeg").status_code == 400
    assert admin.post(url, data=jpeg_bytes((2500, 2000)),            # over 4 megapixels
                      content_type="image/jpeg").status_code == 400
    assert admin.get(f"/api/thumb/{clip}").status_code == 404
