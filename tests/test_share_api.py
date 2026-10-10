"""Share links: making, listing and ending them, and the public share page's
calls — passwords, expiry, and links that follow their maker's rights."""

from __future__ import annotations

import io
import sqlite3
import time

from conftest import ids, sign_in
from PIL import Image

from ninaivu_lite import auth, db

from test_gallery_api import jpeg_with_gps, rescan, set_vis

SHARED_KEYS = {"id", "ext", "kind", "width", "height", "duration", "blurhash", "color",
               "rotation", "mirror", "has_thumb", "playable", "src", "thumb", "view"}


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


def share(client, scope, target, **extra):
    r = client.post("/api/shares", json={"scope": scope, "target_id": target,
                                         "expires_in_days": 0, "password": None, **extra})
    assert r.status_code == 200, r.json
    return r.json["token"]


def test_create_share_shape(app, family):
    i = ids(app)
    r = family.post("/api/shares", json={"scope": "asset", "target_id": i["beach.jpg"],
                                         "expires_in_days": 7, "password": None})
    body = r.json
    assert r.status_code == 200 and len(body["token"]) == 22
    assert body["share_url"] == f"/share/{body['token']}"
    s = body["share"]
    assert s["scope"] == "asset" and s["target_id"] == i["beach.jpg"] and s["expired"] is False
    assert s["view_count"] == 0 and s["created_by"] == 2 and "password" not in s
    assert 6.9 * 86400 < s["expires_at"] - time.time() < 7.1 * 86400
    never = family.post("/api/shares", json={"scope": "asset", "target_id": i["beach.jpg"],
                                             "expires_in_days": 0}).json["share"]
    assert never["expires_at"] is None


def test_create_share_validation(app, family, guest):
    i = ids(app)
    target = i["beach.jpg"]
    for bad, message in (
            ({"scope": "folder", "target_id": target}, "Share scope must be album or asset"),
            ({"scope": "asset", "target_id": 0}, "Target ID must be a positive integer"),
            ({"scope": "asset", "target_id": True}, "Target ID must be a positive integer"),
            ({"scope": "asset", "target_id": target, "expires_in_days": -1},
             "Share expiry must be a finite, non-negative number of days"),
            ({"scope": "asset", "target_id": target, "expires_in_days": "soon"},
             "Share expiry must be a finite number of days"),
            ({"scope": "asset", "target_id": target, "password": "abc"},
             "A share password needs at least 4 characters."),
            ({"scope": "asset", "target_id": target, "password": 1234},
             "Share password must be text")):
        r = family.post("/api/shares", json=bad)
        assert r.status_code == 400 and r.json["error"] == message, bad
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    assert family.post("/api/shares", json={"scope": "asset", "target_id": i["sunset.jpg"]}
                       ).status_code == 404
    assert family.post("/api/shares", json={"scope": "album", "target_id": 777}
                       ).status_code == 404
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    assert guest.post("/api/shares", json={"scope": "asset", "target_id": target}
                      ).status_code == 403
    assert app.test_client().post("/api/shares", json={"scope": "asset", "target_id": target}
                                  ).status_code == 401


def test_album_share_needs_ownership(app, admin, family):
    other = app.test_client()
    sign_in(app, other, "family", username="thambi")
    album_id = family.post("/api/albums", json={"name": "Ours", "ids": [ids(app)["beach.jpg"]]}
                           ).json["id"]
    assert other.post("/api/shares", json={"scope": "album", "target_id": album_id}
                      ).status_code == 403
    assert admin.post("/api/shares", json={"scope": "album", "target_id": album_id}
                      ).status_code == 200


def test_public_asset_share(app, family, library):
    root, _ = library
    i = ids(app)
    token = share(family, "asset", i["beach.jpg"])
    app.config["LITE"].open_browsing = False      # links work for strangers regardless
    stranger = app.test_client()
    r = stranger.get(f"/api/share/{token}")
    assert r.status_code == 200 and r.json["scope"] == "asset"
    item = r.json["item"]
    assert set(item) == SHARED_KEYS
    assert item["src"] == item["view"] == f"/api/share/{token}/file/{i['beach.jpg']}"
    assert item["thumb"].startswith(f"/api/share/{token}/thumb/{i['beach.jpg']}?v=")
    t = stranger.get(item["thumb"])
    assert t.status_code == 200 and t.mimetype == "image/webp"
    assert t.headers["Cache-Control"] == "private, max-age=3600"
    f = stranger.get(item["src"])
    assert f.status_code == 200 and f.mimetype == "image/jpeg"
    assert f.data != (root / "2019" / "beach.jpg").read_bytes()     # the viewing copy
    assert b"Canon" not in f.data
    # Only the shared photograph, nothing next to it.
    assert stranger.get(f"/api/share/{token}/thumb/{i['sunset.jpg']}").status_code == 404
    assert stranger.get(f"/api/share/{token}/file/{i['sunset.jpg']}").status_code == 404
    stranger.get(f"/api/share/{token}")
    listed = family.get("/api/shares").json["shares"]
    assert listed[0]["view_count"] == 2
    assert stranger.get("/api/share/not-a-token").json["error"] == \
        "Share link not found or expired"
    assert stranger.get(f"/share/{token}").status_code == 200


def test_shared_photo_never_carries_gps(app, family, library):
    root, _ = library
    jpeg_with_gps(root / "home" / "garden.jpg")
    rescan(app)
    token = share(family, "asset", ids(app)["garden.jpg"])
    stranger = app.test_client()
    item = stranger.get(f"/api/share/{token}").json["item"]
    data = stranger.get(item["src"]).data
    img = Image.open(io.BytesIO(data))
    assert not img.getexif() and b"Canon" not in data
    pre = stranger.get(f"/api/share/{token}/preview/{item['id']}")
    assert pre.status_code == 200 and b"Canon" not in pre.data


def test_shared_video_has_range(app, family):
    i = ids(app)
    token = share(family, "asset", i["clip.mp4"])
    stranger = app.test_client()
    item = stranger.get(f"/api/share/{token}").json["item"]
    assert item["kind"] == "video" and item["view"] == item["src"]
    # The sample clip cannot be remuxed, so its metadata cannot be removed:
    # refused until an administrator chooses to send such videos as they are.
    assert stranger.get(item["src"]).status_code == 415
    app.config["LITE"].video_originals = True
    full = stranger.get(item["src"])
    assert full.headers["X-Ninaivu-Metadata"] == "original"
    assert full.mimetype == "video/mp4"
    part = stranger.get(item["src"], headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.data == full.data[:4]
    assert stranger.get(f"/api/share/{token}/preview/{item['id']}").status_code == 404


def test_album_share_never_shows_hidden(app, admin):
    i = ids(app)
    album_id = admin.post("/api/albums", json={
        "name": "Pongal", "ids": [i["beach.jpg"], i["sunset.jpg"], i["portrait.jpg"]]}).json["id"]
    set_vis(app, ["sunset.jpg"], db.VIS_HIDDEN)
    token = share(admin, "album", album_id)
    stranger = app.test_client()
    r = stranger.get(f"/api/share/{token}").json
    assert r["scope"] == "album" and r["album"] == {"name": "Pongal"} and r["total"] == 2
    assert {it["id"] for it in r["items"]} == {i["beach.jpg"], i["portrait.jpg"]}
    assert all(set(it) == SHARED_KEYS for it in r["items"])
    assert stranger.get(f"/api/share/{token}/thumb/{i['sunset.jpg']}").status_code == 404
    # Hidden means admins only: not even an admin's own link shows one (A68).
    r = admin.post("/api/shares", json={"scope": "asset", "target_id": i["sunset.jpg"]})
    assert r.status_code == 400


def test_password_and_unlock(app, family):
    i = ids(app)
    token = share(family, "asset", i["beach.jpg"], password="mango tree")
    assert "has_password" in family.get("/api/shares").json["shares"][0]
    stranger = app.test_client()
    r = stranger.get(f"/api/share/{token}")
    assert r.status_code == 401
    assert r.json["password_required"] is True and r.json["token"] == token
    assert r.json["scope"] == "asset"
    assert stranger.get(f"/api/share/{token}/thumb/{i['beach.jpg']}").status_code == 401
    bad = stranger.post(f"/api/share/{token}/unlock", json={"password": "apple"})
    assert bad.status_code == 401 and bad.json["error"] == "That password isn't right."
    assert stranger.post(f"/api/share/{token}/unlock", data="x",
                         content_type="application/json").status_code == 400
    ok = stranger.post(f"/api/share/{token}/unlock", json={"password": "mango tree"})
    assert ok.status_code == 200 and ok.json == {"ok": True}
    cookie = ok.headers["Set-Cookie"]
    assert cookie.startswith(f"ninaivu_share_{token[:16]}=") and "HttpOnly" in cookie
    assert "Max-Age=43200" in cookie
    assert stranger.get(f"/api/share/{token}").status_code == 200
    assert stranger.get(f"/api/share/{token}/thumb/{i['beach.jpg']}").status_code == 200
    # A script may send the password with every request instead.
    script = app.test_client()
    assert script.get(f"/api/share/{token}",
                      headers={"X-Share-Password": "mango tree"}).status_code == 200
    # A forged cookie does nothing.
    forger = app.test_client()
    forger.set_cookie(f"ninaivu_share_{token[:16]}", "0" * 64)
    assert forger.get(f"/api/share/{token}").status_code == 401
    # No password: unlocking is a no-op.
    open_token = share(family, "asset", i["beach.jpg"])
    assert stranger.post(f"/api/share/{open_token}/unlock", json={}).json == {"ok": True}


def test_password_guessing_is_limited(app, family):
    token = share(family, "asset", ids(app)["beach.jpg"], password="mango tree")
    stranger = app.test_client()
    for _ in range(8):
        assert stranger.post(f"/api/share/{token}/unlock",
                             json={"password": "nope"}).status_code == 401
    r = stranger.post(f"/api/share/{token}/unlock", json={"password": "mango tree"})
    assert r.status_code == 429


def test_expired_links(app, family):
    i = ids(app)
    token = share(family, "asset", i["beach.jpg"])
    c = conn_of(app)
    with c:
        c.execute("UPDATE shares SET expires_at = ?", (time.time() - 10,))
    stranger = app.test_client()
    r = stranger.get(f"/api/share/{token}")
    assert r.status_code == 410 and r.json["error"] == "This share link has expired"
    assert stranger.get(f"/api/share/{token}/thumb/{i['beach.jpg']}").status_code == 410
    assert stranger.post(f"/api/share/{token}/unlock", json={}).status_code == 410
    assert stranger.get(f"/share/{token}").status_code == 410
    assert family.get("/api/shares").json["shares"][0]["expired"] is True


def test_links_follow_their_maker(app, admin, family):
    i = ids(app)
    album_id = family.post("/api/albums", json={
        "name": "Trip", "ids": [i["beach.jpg"], i["sunset.jpg"]]}).json["id"]
    album_token = share(family, "album", album_id)
    asset_token = share(family, "asset", i["portrait.jpg"])
    stranger = app.test_client()
    assert stranger.get(f"/api/share/{album_token}").json["total"] == 2
    # A photograph hidden since drops out at once.
    admin.post("/api/visibility", json={"ids": [i["beach.jpg"]], "visibility": "hidden"})
    assert stranger.get(f"/api/share/{album_token}").json["total"] == 1
    # The maker made a guest: the links now show only what a guest could... nothing.
    c = conn_of(app)
    maker = auth.get_user_by_name(c, "amma")
    auth.update_profile(c, maker.id, role="guest")
    r = stranger.get(f"/api/share/{album_token}")
    assert r.status_code == 404 and r.json["error"] == "Nothing here any more"
    assert stranger.get(f"/api/share/{asset_token}").status_code == 404
    assert stranger.get(f"/api/share/{asset_token}/file/{i['portrait.jpg']}").status_code == 404
    # Back to family, then switched off: nothing again.
    auth.update_profile(c, maker.id, role="family")
    assert stranger.get(f"/api/share/{asset_token}").status_code == 200
    auth.update_profile(c, maker.id, active=0)
    assert stranger.get(f"/api/share/{asset_token}").status_code == 404


def test_links_follow_an_assigned_folder(app, admin, library):
    root, _ = library
    i = ids(app)
    member = app.test_client()
    who = sign_in(app, member, "family", username="kutti")
    token = share(member, "asset", i["beach.jpg"])
    auth.update_profile(conn_of(app), who.id, library=str(root / "family"))
    assert app.test_client().get(f"/api/share/{token}").status_code == 404


def test_list_and_end_links(app, admin, family):
    i = ids(app)
    other = app.test_client()
    sign_in(app, other, "family", username="thambi")
    mine = share(family, "asset", i["beach.jpg"], password="mango tree")
    theirs = share(other, "asset", i["sunset.jpg"])
    own = family.get("/api/shares").json["shares"]
    assert [s["token"] for s in own] == [mine]
    s = own[0]
    for key in ("token", "scope", "target_id", "created_at", "created_by", "expires_at",
                "view_count", "expired"):
        assert key in s
    assert "password" not in s and s["has_password"] is True and s["name"] == "beach.jpg"
    assert {s["token"] for s in admin.get("/api/shares").json["shares"]} == {mine, theirs}
    assert family.delete(f"/api/shares/{theirs}").status_code == 403
    assert family.delete("/api/shares/nothing").status_code == 404
    assert family.delete(f"/api/shares/{mine}").json == {"ok": True}
    assert app.test_client().get(f"/api/share/{mine}").status_code == 404
    assert admin.delete(f"/api/shares/{theirs}").json == {"ok": True}
    assert admin.get("/api/shares").json["shares"] == []
    guest = app.test_client()
    sign_in(app, guest, "guest", username="paati")
    assert guest.get("/api/shares").status_code == 403
