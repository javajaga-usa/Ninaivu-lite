"""Fixes ported from Ninaivu (its 10-12 October 2026 changes) that apply to Lite."""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
from pathlib import Path

from ninaivu_lite import api_gallery, auth, compress, db
from ninaivu_lite.app import create_app
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

from conftest import ids

STATIC = Path("ninaivu_lite/static")
TEMPLATES = Path("ninaivu_lite/templates")


# --- Safari on iOS 26: a solid strip under the status bar (Ninaivu 1.10.9) ----------------


def test_both_pages_start_with_a_strip_safari_takes_the_status_bar_colour_from():
    for page in ("index.html", "admin.html"):
        html = (TEMPLATES / page).read_text(encoding="utf-8")
        body = html.index("<body")
        strip = html.index('<div class="status-backdrop" aria-hidden="true"></div>')
        # The first thing in the page, before the bar it sits behind.
        assert body < strip
        assert html[html.index(">", body) + 1:strip].strip().startswith("<!--")
        assert "<header" not in html[body:strip] and "<div" not in html[body:strip]


def test_the_strip_is_fixed_solid_and_only_where_there_is_a_status_bar():
    css = (STATIC / "css/style.css").read_text(encoding="utf-8")
    rule = css[css.index("@media (hover: none), (max-width: 1099px) {\n  .status-backdrop"):]
    rule = rule[:rule.index("}\n}") + 3]
    for part in ("position: fixed", "top: 0", "z-index: 41", "pointer-events: none",
                 "background: var(--bg-elev)", "max(env(safe-area-inset-top, 0px), 6px)"):
        assert part in rule
    assert ".status-backdrop { display: none; }" in css
    # Under dialogs and the viewer, which sit far above it.
    assert "z-index: 200" in css[css.index(".viewer {"):][:80]
    # A transparent root counts as white to Safari.
    assert "html { height: 100%; background-color: var(--bg); }" in css


# --- the write-ahead log is cut back (Ninaivu 1.10.8) -------------------------------------


def test_the_write_ahead_log_has_a_size_limit(tmp_path):
    conn = db.connect(tmp_path)
    assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024
    assert db.JOURNAL_SIZE_LIMIT == 64 * 1024 * 1024


def test_a_burst_leaves_no_large_log_behind(tmp_path):
    conn = db.connect(tmp_path)
    if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        return                                   # a filesystem without WAL: nothing to cut
    conn.execute("PRAGMA journal_size_limit=65536")   # small, so the test stays small
    conn.execute("CREATE TABLE burst (b BLOB)")
    reader = sqlite3.connect(str(tmp_path / db.DB_FILE))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM assets").fetchone()   # holds the log open
    with conn:
        conn.executemany("INSERT INTO burst VALUES (?)", [(b"x" * 4096,) for _ in range(800)])
    wal = tmp_path / (db.DB_FILE + "-wal")
    assert wal.stat().st_size > 2 * 1024 * 1024
    reader.rollback()
    reader.close()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with conn:
        conn.execute("INSERT INTO burst VALUES (x'00')")
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    assert wal.stat().st_size <= 65536 + 64 * 1024


# --- large answers built in memory are compressed (Ninaivu 1.10.8) ------------------------


def test_an_answer_built_in_memory_past_the_file_ceiling_is_gzipped(app):
    big = json.dumps([{"i": i, "name": f"photo-{i:07d}.jpg"} for i in range(160_000)])
    assert len(big) > compress.MAX_BYTES

    @app.get("/api/test-big")
    def test_big():
        return app.response_class(big, mimetype="application/json")

    r = app.test_client().get("/api/test-big", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip"
    assert gzip.decompress(r.data).decode() == big
    assert len(r.data) < len(big) // 4


def test_a_file_keeps_the_ceiling(app, tmp_path):
    from flask import send_file
    path = tmp_path / "big.txt"
    path.write_text("a" * (compress.MAX_BYTES + 10))

    @app.get("/api/test-file")
    def test_file():
        return send_file(path, mimetype="text/plain")

    r = app.test_client().get("/api/test-file", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in r.headers
    assert len(r.data) == compress.MAX_BYTES + 10


# --- the gallery's page in one step (Ninaivu 1.10.8) --------------------------------------


def _sql():
    cols = ", ".join(f"a.{c}" for c in api_gallery.GRID_COLUMNS)
    return f"SELECT {cols} FROM assets a ORDER BY {api_gallery.SORTS['date_desc']} LIMIT ? OFFSET ?"


def test_the_page_read_in_one_step_is_the_page_read_row_by_row(app):
    conn = db.connect(app.config["LITE"].data_dir)
    # A REAL that must come back exactly, and a text with quotes in it.
    conn.execute("UPDATE assets SET duration = 12.345678901234567, color = '\"#abc' "
                 "WHERE kind = 'video'")
    conn.commit()
    cur = conn.cursor()
    cur.row_factory = None
    sql = _sql()
    one = api_gallery.page_in_one_step(cur, sql, [100, 0], api_gallery.GRID_COLUMNS)
    rows = [list(r) for r in cur.execute(sql, [100, 0])]
    assert len(one) == len(rows) >= 5
    for got, want in zip(one, rows, strict=True):
        # A REAL to its last digit or one step from it: the SQLite that comes
        # with Python 3.10 on Windows and macOS rounds the 17th digit its own
        # way (the gallery shows a video's length in whole seconds anyway).
        assert [round(v, 12) if isinstance(v, float) else v for v in got] == \
            [round(v, 12) if isinstance(v, float) else v for v in want]
    assert any(isinstance(r[5], float) and abs(r[5] - 12.345678901234567) < 1e-12 for r in one)
    assert api_gallery.page_in_one_step(cur, sql, [100, 1000], api_gallery.GRID_COLUMNS) == []


def test_a_column_of_bytes_falls_back_to_the_row_by_row_read(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER, b BLOB)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, b"\x00\x01"), (2, None)])
    rows = api_gallery.page_in_one_step(conn.cursor(), "SELECT id, b FROM t ORDER BY id",
                                        [], ("id", "b"))
    assert [tuple(r) for r in rows] == [(1, b"\x00\x01"), (2, None)]


def test_the_gallery_answers_the_same(app, admin, monkeypatch):
    one = admin.get("/api/segments?sort=date_asc").get_json()
    assert one["total"] == one["returned"] >= 5

    def row_by_row(cur, page_sql, params, columns):
        return cur.execute(page_sql, params).fetchall()

    monkeypatch.setattr(api_gallery, "page_in_one_step", row_by_row)
    app.config["REMEMBERED_PAGES"] = db.Remembered(app.config["LITE"].data_dir)
    assert admin.get("/api/segments?sort=date_desc").get_json() == \
        admin.get("/api/segments?sort=date_desc").get_json()
    before = admin.get("/api/segments?sort=date_asc&limit=3").get_json()
    monkeypatch.undo()
    app.config["REMEMBERED_PAGES"] = db.Remembered(app.config["LITE"].data_dir)
    assert admin.get("/api/segments?sort=date_asc&limit=3").get_json() == before


# --- adding to an album counts it in one query (Ninaivu 1.10.8) ---------------------------


def test_adding_to_an_album_counts_what_this_person_may_see(app, admin, family):
    every = ids(app)
    album = family.post("/api/albums", json={"name": "Pongal"}).get_json()["id"]
    r = family.post(f"/api/albums/{album}/items", json={"ids": [every["beach.jpg"]]})
    assert r.get_json() == {"ok": True, "count": 1}
    r = family.post(f"/api/albums/{album}/items",
                    json={"ids": [every["sunset.jpg"], every["beach.jpg"]]})
    assert r.get_json()["count"] == 2
    # Hidden from the family afterwards: their count leaves it out.
    assert admin.post("/api/visibility", json={"ids": [every["sunset.jpg"]],
                                               "visibility": "hidden"}).status_code == 200
    r = family.post(f"/api/albums/{album}/items", json={"ids": [every["beach.jpg"]]})
    assert r.get_json()["count"] == 1


# --- exactly one first administrator (Ninaivu 1.10.4) -------------------------------------


def test_two_setup_forms_at_once_make_one_administrator(library, monkeypatch):
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root))
    application = create_app(cfg, scanner=Scanner(cfg.data_dir, cfg.folders))
    real = auth.create_user

    def slow(*args, **kwargs):
        time.sleep(0.3)                  # the password's hash: both have checked by now
        return real(*args, **kwargs)

    monkeypatch.setattr(auth, "create_user", slow)
    answers: list[int] = []

    def send(name):
        c = application.test_client()
        r = c.post("/api/auth/setup", json={"username": name, "password": "admin passphrase",
                                            "name": name.title()})
        answers.append(r.status_code)

    threads = [threading.Thread(target=send, args=(n,)) for n in ("appa", "amma")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(answers) == [200, 409]
    assert auth.count_admins(db.connect(data)) == 1


# --- the viewer lets go of what it has paged past (Ninaivu 1.10.8) ------------------------


def test_the_viewer_lets_go_of_pictures_it_has_paged_past():
    js = (STATIC / "js/viewer.js").read_text(encoding="utf-8")
    assert "this.warming = new Map();" in js
    assert "new Image().src" not in js           # an unkept fetch nobody can stop
    assert "coolDown(keep = new Set())" in js
    go = js[js.index("  async goTo(index)"):js.index("  /* -- rendering")]
    assert "this.coolDown(new Set([id, ...ahead" in go
    assert "this.warming.set(neighbour, image)" in go
    clear = js[js.index("  clearStage() {"):js.index("  coolDown(keep")]
    assert "if (!img.complete) img.removeAttribute('src');" in clear
    close = js[js.index("  close() {"):js.index("  closeMoreMenu(")]
    assert "this.clearStage();" in close and "this.coolDown();" in close
    assert "this.stage.innerHTML = '';" not in close
