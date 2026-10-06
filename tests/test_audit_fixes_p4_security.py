"""Server security findings of the 2026-10-06 audit: A20-A22, A40, A48-A52."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from ninaivu_lite import api_auth, api_gallery, auth, db, media, panel, updates

from conftest import ids, make_jpeg, sign_in


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


@pytest.fixture(autouse=True)
def fresh_throttles():
    """The sign-in limits are module-wide: every test starts with none used."""
    throttles = (api_auth.throttle_short, api_auth.throttle_long, api_auth.throttle_day)
    for throttle in throttles:
        throttle._fails.clear()
    yield
    for throttle in throttles:
        throttle._fails.clear()


def from_address(address):
    return {"environ_base": {"REMOTE_ADDR": address}}


# --- A20: a reused id never shows the old photograph's thumbnail ---------------------------------


def test_a20_a_reused_id_is_not_served_the_old_thumbnail(app, guest, library):
    root, _ = library
    c = conn_of(app)
    aid = ids(app)["sunset.jpg"]
    assert guest.get(f"/api/thumb/{aid}").status_code == 404      # Family: not for a guest
    old = media.thumb_path(app.config["SCANNER"].thumbs_dir, aid, "s")
    Image.new("RGB", (64, 64), (255, 0, 0)).save(old, "WEBP")       # the removed photo's pixels
    # The same id now belongs to another, Public, photograph not yet thumbnailed.
    make_jpeg(root / "2019" / "green.jpg", "2019:05:12 18:30:00", color=(0, 200, 0))
    with c:
        c.execute("UPDATE assets SET name = 'green.jpg', thumb = ?, large = 0, visibility = ? "
                  "WHERE id = ?", (db.THUMB_PENDING, db.VIS_PUBLIC, aid))
    r = guest.get(f"/api/thumb/{aid}")
    assert r.status_code == 200
    red, green, _blue = Image.open(io.BytesIO(r.data)).convert("RGB").getpixel((5, 5))
    assert green > 150 and red < 100
    assert c.execute("SELECT thumb FROM assets WHERE id = ?", (aid,)).fetchone()[0] == db.THUMB_OK


def test_a20_a_stale_thumbnail_of_a_file_away_is_not_served(app, guest):
    c = conn_of(app)
    aid = ids(app)["sunset.jpg"]
    old = media.thumb_path(app.config["SCANNER"].thumbs_dir, aid, "s")
    Image.new("RGB", (64, 64), (255, 0, 0)).save(old, "WEBP")
    with c:
        c.execute("UPDATE assets SET name = 'not-here.jpg', thumb = ?, visibility = ? "
                  "WHERE id = ?", (db.THUMB_PENDING, db.VIS_PUBLIC, aid))
    assert guest.get(f"/api/thumb/{aid}").status_code == 404


# --- A21: a folder's visibility history goes with the folder --------------------------------------


def test_a21_undo_does_not_reach_the_folder_that_got_the_id(app, admin, library, tmp_path):
    root, _ = library
    c = conn_of(app)
    r = admin.post("/api/visibility/folder",
                   json={"folder": "2019", "visibility": "public", "confirm": True})
    assert r.status_code == 200
    assert admin.post("/api/visibility/folder",
                      json={"folder": "2019", "visibility": "hidden"}).status_code == 200
    old_id = c.execute("SELECT id FROM folders").fetchone()[0]
    assert admin.delete("/api/admin/libraries",
                        query_string={"path": str(root), "force": "1"}).status_code == 200
    other = tmp_path / "Other"
    make_jpeg(other / "2019" / "old.jpg", "2019:01:01 00:00:00")
    assert admin.post("/api/library/root", json={"path": str(other)}).status_code == 200
    assert c.execute("SELECT id FROM folders WHERE path = ?",
                     (str(other),)).fetchone()[0] == old_id                # the same id
    assert c.execute("SELECT COUNT(*) FROM visibility_batches").fetchone()[0] == 0
    r = admin.post("/api/visibility/undo", json={})
    assert r.status_code == 409
    assert c.execute("SELECT COUNT(*) FROM folder_rules").fetchone()[0] == 0


def test_a21_upgrade_drops_history_of_removed_folders(tmp_path):
    c = db.connect(tmp_path)
    with c:
        c.execute("DROP TRIGGER visibility_folder_gone")
        c.execute("INSERT INTO folders (id, path, added_at) VALUES (1, '/now', ?)",
                  (time.time(),))
        for folder_id, made in ((1, time.time() - 86400),       # made for the folder removed
                                (7, time.time()),               # its folder is gone
                                (1, time.time() + 5),           # this folder's own
                                (None, time.time() - 86400)):   # chosen photos, no folder
            c.execute("INSERT INTO visibility_batches (created_at, scope, folder_id, visibility) "
                      "VALUES (?, ?, ?, 2)", (made, "items" if folder_id is None else "folder",
                                              folder_id))
    c.execute(f"PRAGMA user_version = {db.MIGRATIONS.index(_migration_8())}")
    db.migrate(c)
    left = c.execute("SELECT folder_id FROM visibility_batches ORDER BY id").fetchall()
    assert [r[0] for r in left] == [1, None]
    with c:
        c.execute("DELETE FROM folders WHERE id = 1")
    assert [r[0] for r in c.execute("SELECT folder_id FROM visibility_batches")] == [None]


def _migration_8() -> str:
    return next(m for m in db.MIGRATIONS if "visibility_folder_gone" in m)


# --- A22: sign-in limits ------------------------------------------------------------------------------


def test_a22_strangers_cannot_lock_the_admin_out_of_this_computer(app):
    c = app.test_client()
    for n in range(25):
        c.post("/api/auth/login", json={"username": "appa", "password": f"wrong {n}"},
               **from_address(f"10.0.0.{n + 1}"))
    r = app.test_client().post("/api/auth/login",
                               json={"username": "appa", "password": "admin passphrase"},
                               **from_address("127.0.0.1"))
    assert r.status_code == 200
    # Elsewhere on the network the account is still resting.
    r = app.test_client().post("/api/auth/login",
                               json={"username": "appa", "password": "admin passphrase"},
                               **from_address("10.0.1.1"))
    assert r.status_code == 429


def test_a22_login_and_profile_picker_share_one_count(app):
    admin = auth.get_user_by_name(conn_of(app), "appa")
    c = app.test_client()
    for n in range(20):
        c.post("/api/auth/login", json={"username": "appa", "password": f"wrong {n}"},
               **from_address(f"10.0.0.{n + 1}"))
    r = app.test_client().post("/api/auth/enter",
                               json={"id": admin.id, "secret": "admin passphrase"},
                               **from_address("10.0.2.1"))
    assert r.status_code == 429


def test_a22_a_pin_gets_few_guesses_a_day_and_failures_are_logged(app, caplog):
    person = auth.create_user(conn_of(app), "amma", name="Amma", role="family", pin="4826")
    for _ in range(api_auth.throttle_day.limit):
        api_auth.throttle_day.fail(f"account|{person.id}")
    r = app.test_client().post("/api/auth/enter", json={"id": person.id, "secret": "4826"},
                               **from_address("10.0.3.1"))
    assert r.status_code == 429
    api_auth.throttle_day._fails.clear()
    with caplog.at_level(logging.WARNING, logger="ninaivu_lite.api_auth"):
        r = app.test_client().post("/api/auth/enter", json={"id": person.id, "secret": "1111"},
                                   **from_address("10.0.3.2"))
    assert r.status_code == 401
    assert any("10.0.3.2" in rec.getMessage() for rec in caplog.records)


# --- A40: a link never starts copying the library ----------------------------------------------------


def test_a40_a_drive_link_asks_before_exporting():
    source = Path(api_gallery.__file__).with_name("static").joinpath("js", "drives.js")
    text = source.read_text(encoding="utf-8")
    body = re.search(r"async followLink\(\) \{(.*?)\n  \}\n", text, re.S).group(1)
    assert "this.run(" not in body
    assert "this.ask(drive)" in body


# --- A48: videos for guests: few ffmpeg at once, temporary names, a capped views/ ---------------


def _public_clip(app) -> int:
    aid = ids(app)["clip.mp4"]
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_PUBLIC, aid))
    return aid


def test_a48_busy_ffmpeg_answers_503_rather_than_waiting(app, guest, monkeypatch):
    aid = _public_clip(app)
    called = []
    monkeypatch.setattr(media, "strip_video", lambda *a: called.append(a) or False)
    for _ in range(2):
        assert api_gallery.FFMPEG_SLOTS.acquire(blocking=False)
    try:
        r = guest.get(f"/api/file/{aid}")
    finally:
        for _ in range(2):
            api_gallery.FFMPEG_SLOTS.release()
    assert r.status_code == 503 and not called
    # The same video already being copied: wait a little, then say busy.
    monkeypatch.setattr(api_gallery, "STRIP_WAIT", 0.05)
    lock = api_gallery._strip_locks.setdefault(aid, threading.Lock())
    with lock:
        assert guest.get(f"/api/file/{aid}").status_code == 503
    assert not called


def test_a48_the_temporary_copy_survives_another_requests_clean_up(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    folder = tmp_path / "views" / "05"
    folder.mkdir(parents=True)
    out = folder / "5-100-200.mp4"

    def ffmpeg(args, **_kwargs):
        Path(args[-1]).write_bytes(b"stripped")
        for old in folder.glob("5-*.mp4"):           # a second request, cleaning up
            old.unlink()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(media, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", ffmpeg)
    assert media.strip_video(str(source), str(out))
    assert out.read_bytes() == b"stripped"
    assert [p.name for p in folder.iterdir()] == [out.name]


def test_a48_views_are_kept_under_their_cap(app, monkeypatch):
    views = Path(app.config["LITE"].data_dir) / "views" / "01"
    views.mkdir(parents=True)
    made = []
    for n in range(5):
        path = views / f"{n}-1-1.jpg"
        path.write_bytes(b"x" * 1000)
        stamp = time.time() - 1000 + n
        os.utime(path, (stamp, stamp))
        made.append(path)
    monkeypatch.setattr(api_gallery, "VIEWS_MAX_BYTES", 3000)
    monkeypatch.setattr(api_gallery, "_views_checked_at", 0.0)
    with app.test_request_context():
        api_gallery.trim_views(made[0])
    left = sorted(p.name for p in views.iterdir())
    assert sum(p.stat().st_size for p in views.iterdir()) <= 3000
    assert made[0].name in left and made[-1].name in left and made[1].name not in left


# --- A49: cameras are for the family ---------------------------------------------------------------


def test_a49_guests_get_no_camera_models(app, guest, family):
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET visibility = ?", (db.VIS_PUBLIC,))
    assert guest.get("/api/facets").get_json()["cameras"] == []
    assert not [s for s in guest.get("/api/suggest?q=canon").get_json()["suggestions"]
                if s["type"] == "camera"]
    assert guest.get("/api/segments?q=Canon").get_json()["total"] == 0
    everything = guest.get("/api/segments").get_json()["total"]
    assert guest.get("/api/segments?camera=Canon EOS 80D").get_json()["total"] == everything
    # The family still has them.
    assert family.get("/api/facets").get_json()["cameras"][0]["name"] == "Canon EOS 80D"
    assert family.get("/api/segments?q=Canon").get_json()["total"] == 1


# --- A50: PINs, temporary passwords, the update link ---------------------------------------------


def test_a50_a_new_pin_signs_that_person_out(app, admin):
    phone = app.test_client()
    person = sign_in(app, phone, "family", username="amma")
    assert phone.get("/api/me").get_json()["id"] == person.id
    assert admin.post(f"/api/people/{person.id}", json={"pin": "4826"}).status_code == 200
    assert phone.get("/api/me").get_json()["anonymous"] is True


def test_a50_a_temporary_password_changes_nothing_until_replaced(app):
    phone = app.test_client()
    sign_in(app, phone, "family", username="amma", password="temporary words", must_change=True)
    r = phone.post("/api/albums", json={"name": "Pongal"})
    assert r.status_code == 403 and r.get_json()["must_change"] is True
    assert phone.get("/api/segments").status_code == 200            # looking is fine
    assert phone.post("/api/me/password",
                      json={"password": "my own long words"}).status_code == 200
    assert phone.post("/api/albums", json={"name": "Pongal"}).status_code == 200


@pytest.mark.parametrize("url,kept", [
    ("https://github.com/javajaga-usa/ninaivu-lite/releases/tag/v9.9.9", True),
    ("file:///C:/Windows/System32/calc.exe", False),
    ("https://github.com.evil.example/x", False),
    (None, False),
])
def test_a50_the_panel_opens_only_github_for_an_update(url, kept):
    view = SimpleNamespace(update_text=SimpleNamespace(set=lambda _t: None),
                           download_button=SimpleNamespace(pack=lambda **_k: None,
                                                           pack_forget=lambda: None))
    panel.Panel.show_update(view, {"version": "9.9.9", "url": url, "available": True}, True)
    assert view.update_url == (url if kept else updates.RELEASES_PAGE)


# --- A51: PIN-less Family profiles are pointed out --------------------------------------------------


def test_a51_the_console_is_told_of_family_profiles_without_a_pin(app, admin):
    c = conn_of(app)
    auth.create_user(c, "amma", name="Amma", role="family")
    auth.create_user(c, "appa2", name="Thatha", role="family", pin="4826")
    auth.create_user(c, "paati", name="Paati", role="guest")
    people = admin.get("/api/admin/overview").get_json()["people"]
    assert people["open_family"] == ["Amma"]
    static = Path(api_gallery.__file__).with_name("static")
    en = json.loads((static / "i18n" / "en.json").read_text(encoding="utf-8"))
    ta = json.loads((static / "i18n" / "ta.json").read_text(encoding="utf-8"))
    key = next(k for k in en if k.startswith("Visitors must sign in, but anyone"))
    assert key in (static / "js" / "admin.js").read_text(encoding="utf-8")
    assert "{names}" in ta[key] and ta[key] != key


# --- A52: a signed-in write must say where it came from ------------------------------------------


def test_a52_bodyless_writes_without_an_origin_are_refused(app, admin):
    aid = ids(app)["beach.jpg"]
    assert admin.post("/api/visibility", json={"ids": [aid], "visibility": "hidden"}
                      ).status_code == 200
    admin.environ_base.pop("HTTP_ORIGIN", None)
    for headers in ({"Origin": "null"}, {}):
        r = admin.post("/api/visibility/undo", data=b"", headers=headers,
                       content_type="application/x-www-form-urlencoded")
        assert r.status_code == 403, headers
        r = admin.post("/api/scan", headers=headers)
        assert r.status_code == 403, headers
    assert conn_of(app).execute("SELECT visibility FROM assets WHERE id = ?",
                                (aid,)).fetchone()[0] == db.VIS_HIDDEN


def test_a52_scripts_of_this_site_still_get_through(app, admin):
    admin.environ_base.pop("HTTP_ORIGIN", None)
    # JSON (the app's own calls, and the share page's no-referrer "null").
    r = admin.post("/api/visibility/undo", json={}, headers={"Origin": "null"})
    assert r.status_code == 409
    # The custom header, with no body at all.
    assert admin.post("/api/scan", headers={"X-Ninaivu": "1"}).status_code == 200
    # A same-site page names itself (Referrer-Policy: same-origin).
    assert admin.delete("/api/me/avatar", headers={"Origin": "http://localhost"}).status_code == 200
    # The share page posts its password as JSON from a no-referrer page.
    r = admin.post("/api/share/nothing/unlock", json={"password": "x"}, headers={"Origin": "null"})
    assert r.status_code == 404


def test_a52_the_control_panel_can_still_stop_the_server(app):
    stopped = threading.Event()
    app.config["STOP"] = stopped.set
    app.config["STOP_TOKEN"] = "secret"
    r = app.test_client().post("/api/local/stop", json={"token": "secret"})
    assert r.status_code == 200
    assert stopped.wait(5)
