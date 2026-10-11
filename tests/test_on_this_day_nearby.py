"""On this day (the same day of the year in earlier years), Photos taken
nearby (within a kilometre, for the family), and the installers carrying
HEIC support."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ninaivu_lite import db

from conftest import ids

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ninaivu_lite"


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


def names(app, body) -> list[str]:
    by_id = {v: k for k, v in ids(app).items()}
    return [by_id[item[0]] for s in body["segments"] for item in s["items"]]


# --- On this day ---------------------------------------------------------------------------


def test_on_this_day_is_the_same_day_in_earlier_years(app, admin):
    body = admin.get("/api/segments?on_this_day=2024-05-12").json
    assert sorted(names(app, body)) == ["beach.jpg", "sunset.jpg"]
    # Not that day's own year, nor a later one.
    assert admin.get("/api/segments?on_this_day=2019-05-12").json["total"] == 0
    assert admin.get("/api/segments?on_this_day=2024-05-13").json["total"] == 0


def test_on_this_day_ignores_a_malformed_day(admin):
    everything = admin.get("/api/segments").json["total"]
    for day in ("05-12", "2024-5-12", "x' OR 1=1 --", "2024-05-12T00"):
        assert admin.get("/api/segments", query_string={"on_this_day": day}).json["total"] == everything


def test_on_this_day_shows_a_guest_only_what_they_may_see(app, guest):
    i = ids(app)
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_PUBLIC, i["beach.jpg"]))
    assert names(app, guest.get("/api/segments?on_this_day=2024-05-12").json) == ["beach.jpg"]


def test_the_family_app_has_the_on_this_day_row():
    page = (PACKAGE / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="on-this-day"' in page and 'id="on-this-day-list"' in page
    app_js = (PACKAGE / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "from './on-this-day.js'" in app_js
    api_js = (PACKAGE / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "params.set('on_this_day'" in api_js and "params.set('near'" in api_js


# --- Taken nearby --------------------------------------------------------------------------


def place(app, **where):
    i = ids(app)
    c = conn_of(app)
    with c:
        for name, (lat, lon) in where.items():
            c.execute("UPDATE assets SET gps_lat = ?, gps_lon = ? WHERE id = ?",
                      (lat, lon, i[name.replace("_", ".")]))
    return i


def test_nearby_finds_what_was_taken_within_a_kilometre(app, admin):
    # Marina Beach; Santhome 1.5 km south is too far; 400 m east is near.
    i = place(app, beach_jpg=(13.0500, 80.2824), sunset_jpg=(13.0500, 80.2861),
              portrait_jpg=(13.0365, 80.2824))
    body = admin.get(f"/api/segments?near={i['beach.jpg']}").json
    assert sorted(names(app, body)) == ["beach.jpg", "sunset.jpg"]


def test_nearby_without_a_place_or_out_of_reach_shows_nothing(app, admin, family):
    i = place(app, beach_jpg=(13.05, 80.28), sunset_jpg=(13.05, 80.28))
    assert admin.get(f"/api/segments?near={i['portrait.jpg']}").json["total"] == 0
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_HIDDEN, i["beach.jpg"]))
    # The family cannot see the hidden photograph, so cannot ask around it.
    assert family.get(f"/api/segments?near={i['beach.jpg']}").json["total"] == 0


def test_nearby_is_not_for_guests(app, guest):
    i = place(app, beach_jpg=(13.05, 80.28), sunset_jpg=(13.05, 80.28))
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ?", (db.VIS_PUBLIC,))
    everything = guest.get("/api/segments").json["total"]
    # Ignored, as the other family-only filters are: a guest is never told
    # which photographs were taken in the same place.
    assert guest.get(f"/api/segments?near={i['beach.jpg']}").json["total"] == everything


def test_nearby_near_the_poles_does_not_fail(app, admin):
    i = place(app, beach_jpg=(89.9999, 10.0))
    assert admin.get(f"/api/segments?near={i['beach.jpg']}").json["total"] == 1


# --- HEIC in the installers ----------------------------------------------------------------


def test_every_installer_carries_pillow_heif():
    req = (ROOT / "requirements-installer.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in req and "pillow-heif" in req
    # Running from a checkout keeps the three packages.
    assert "heif" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for build in ("installers/windows/build.ps1", "installers/macos/build.sh",
                  "installers/linux/build.sh", "installers/docker/Dockerfile"):
        text = (ROOT / build).read_text(encoding="utf-8")
        assert "requirements-installer.txt" in text, build
    assert "!requirements-installer.txt" in (
        ROOT / "installers/docker/Dockerfile.dockerignore").read_text(encoding="utf-8")
