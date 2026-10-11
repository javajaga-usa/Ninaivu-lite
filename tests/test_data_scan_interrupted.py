"""Data safety (2026-10-11-b audit): what was decided about a photograph
moved in Explorer survives the move, a scan cut short included."""

from __future__ import annotations

import os
import time

from conftest import ids, make_jpeg

from ninaivu_lite import auth, db, media
from ninaivu_lite.scanner import Scanner


def test_data1_a_move_seen_by_a_scan_cut_short_keeps_albums_and_hidden(tmp_path, monkeypatch):
    root, data = tmp_path / "Photos", tmp_path / "data"
    for k in range(4):
        make_jpeg(root / "Trip" / f"p{k}.jpg", f"2020:01:0{k + 1} 10:00:00")
    conn = db.connect(data)
    scanner = Scanner(data, [str(root)])
    scanner.scan_once(conn)
    first = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM assets")}
    user = auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    with conn:
        conn.execute("INSERT INTO albums (name, created_by) VALUES ('Trip', ?)", (user.id,))
        conn.executemany("INSERT INTO album_items (album_id, asset_id) VALUES (1, ?)",
                         [(i,) for i in first.values()])
        conn.executemany("INSERT INTO user_assets (user_id, asset_id, favorite) VALUES (?, ?, 1)",
                         [(user.id, i) for i in first.values()])
        conn.executemany("UPDATE assets SET visibility = ?, vis_source = 'item' WHERE id = ?",
                         [(db.VIS_HIDDEN, i) for i in first.values()])
    (root / "Trip").rename(root / "2020 Trip")       # moved in Explorer
    time.sleep(1.1)
    # The computer is shut down while the scan is reading the moved photographs.
    real, seen = media.describe, []

    def describe(path, name, kind, st):
        seen.append(name)
        if len(seen) == 3:
            scanner._stop.set()
        return real(path, name, kind, st)

    monkeypatch.setattr(media, "describe", describe)
    monkeypatch.setattr("ninaivu_lite.scanner.THUMB_WORKERS", 1)
    scanner.scan_once(conn)
    monkeypatch.setattr(media, "describe", real)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE dir = '2020 Trip'").fetchone()[0] == 2
    time.sleep(2.5)                       # started again later
    # Started again: a new scanner, a full scan.
    again = Scanner(data, [str(root)])
    again.scan_once(conn)
    rows = conn.execute("SELECT id, visibility FROM assets WHERE missing = 0").fetchall()
    assert len(rows) == 4
    now = {r["id"] for r in rows}
    in_album = {r[0] for r in conn.execute("SELECT asset_id FROM album_items")}
    favourites = {r[0] for r in conn.execute("SELECT asset_id FROM user_assets WHERE favorite = 1")}
    assert in_album == now
    assert favourites == now
    assert all(r["visibility"] == db.VIS_HIDDEN for r in rows)
    # A walk that reached the end leaves no mark: the next one counts from itself.
    assert not (data / "scan-unfinished").exists()


def test_data2_a_profile_picture_stays_when_its_photo_is_moved_in_explorer(app, family, library):
    root, _data = library
    me = family.post("/api/me/avatar", json={"asset_id": ids(app)["beach.jpg"]}).get_json()
    assert me["avatar"]
    os.replace(root / "2019" / "beach.jpg", root / "family" / "beach.jpg")
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))
    assert family.get("/api/me").get_json()["avatar"]
    # The sign-in screen, which anyone who reaches it sees, still shows it.
    profiles = app.test_client().get("/api/auth/profiles").get_json()["profiles"]
    assert [p["avatar"] for p in profiles if p["name"] == me["name"]][0]
