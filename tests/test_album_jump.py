"""Jump to a year or month: /api/months lists only the months the grid, as
filtered, has photographs in, and the page has the button that uses it."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ninaivu_lite import db

from conftest import ids

PACKAGE = Path(__file__).resolve().parent.parent / "ninaivu_lite"


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


def test_months_lists_only_months_with_photographs_newest_first(admin):
    r = admin.get("/api/months")
    assert r.status_code == 200
    months = r.json["months"]
    assert [m["month"] for m in months] == ["2023-01", "2022-03", "2021-08", "2019-05", "2015-06"]
    assert months[3] == {"month": "2019-05", "count": 2}
    assert r.json["undated"] == 0


def test_months_follow_the_grid_filters(admin):
    assert [m["month"] for m in admin.get("/api/months?kind=video").json["months"]] == ["2022-03"]
    assert [m["month"] for m in admin.get("/api/months?folder=family").json["months"]] == [
        "2023-01", "2022-03"]
    year = admin.get("/api/months?from=2019-01-01&to=2019-12-31").json["months"]
    assert year == [{"month": "2019-05", "count": 2}]


def test_months_show_a_guest_only_what_they_may_see(app, guest):
    i = ids(app)
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_PUBLIC, i["beach.jpg"]))
    assert guest.get("/api/months").json == {"months": [{"month": "2019-05", "count": 1}],
                                             "undated": 0}


def test_undated_photographs_are_counted_apart(app, admin):
    i = ids(app)
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET date_key = '' WHERE id = ?", (i["portrait.jpg"],))
    body = admin.get("/api/months").json
    assert "2021-08" not in [m["month"] for m in body["months"]]
    assert body["undated"] == 1


def test_the_family_app_has_the_jump_button():
    page = (PACKAGE / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="jump-btn"' in page and 'id="jump-panel"' in page
    assert 'aria-controls="jump-panel"' in page
    app_js = (PACKAGE / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "from './jump.js'" in app_js
    assert "loadMoreIfNear({ force: true })" in app_js
    jump = (PACKAGE / "static" / "js" / "jump.js").read_text(encoding="utf-8")
    assert "api.months(" in jump


def test_the_jump_pill_is_not_blurred_on_its_own_layer():
    """A backdrop blur on the pill would draw its text soft on an iPhone (M15)."""
    css = (PACKAGE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for body in re.findall(r"\.jump-(?:btn|panel)\s*\{([^}]*)\}", css):
        assert "backdrop-filter" not in body
