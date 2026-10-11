"""The Albums page reads each album from the album's side (PERF3): SQLite,
with no statistics to go by, started its albums queries from every photograph
the viewer may see and looked each one up in the album, three times an album.
On a 100,000-photo library with 61 albums the page took 4.5 s (42 ms now),
and opening one album 60-90 ms (10-20 ms now)."""

from __future__ import annotations

import re

from conftest import ids

from ninaivu_lite import api_albums, common, db


def _traced(monkeypatch) -> list[str]:
    """Every statement the albums routes run, with its values in place."""
    statements: list[str] = []

    def conn():
        c = common.conn()
        c.set_trace_callback(statements.append)
        return c

    monkeypatch.setattr(api_albums, "conn", conn)
    return statements


def test_album_queries_start_from_the_album(app, admin, family, monkeypatch):
    i = ids(app)
    album = admin.post("/api/albums", json={"name": "Pongal",
                                            "ids": [i["beach.jpg"], i["sunset.jpg"]]}).json["id"]
    statements = _traced(monkeypatch)
    for client in (admin, family):
        page = client.get("/api/albums").json["albums"]
        assert [a["n"] for a in page if a["id"] == album] == [2]
        one = client.get(f"/api/albums/{album}").json["album"]
        assert sorted(one["item_ids"]) == sorted([i["beach.jpg"], i["sunset.jpg"]])
    reading = [s for s in statements if "album_items" in s and "assets" in s]
    assert reading
    conn = db.connect(app.config["LITE"].data_dir)
    for sql in reading:
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        # A photograph is only ever looked up by its id, from an album's
        # item: never a walk of the photographs, by any index.
        walks = [step for step in plan if re.match(r"(SCAN|SEARCH) a\b", step)
                 and "INTEGER PRIMARY KEY" not in step]
        assert not walks, (sql, plan)
