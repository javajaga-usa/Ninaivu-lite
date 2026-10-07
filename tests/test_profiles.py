"""The sign-in screen's tiles: the administrator's own, locked, and the
picture a person chooses for theirs."""

from __future__ import annotations

import io
import os

from PIL import Image

from ninaivu_lite import auth, create_app, db
from ninaivu_lite.scanner import Scanner

from conftest import ids, sign_in


def square_of(library, me):
    """Where the picture the address names lives on disk."""
    _, data = library
    return data / "avatars" / f"{me['id']}-{me['avatar'].split('v=')[1]}.jpg"


def profiles(app):
    return {p["name"]: p for p in app.test_client().get("/api/auth/profiles").get_json()["profiles"]}


def test_the_administrator_has_a_locked_tile_that_takes_the_password(app, family):
    tiles = profiles(app)
    appa = tiles["Appa"]
    assert (appa["role"], appa["locked"], appa["kind"]) == ("admin", True, "password")
    assert tiles["Family Person"]["kind"] == "open"
    door = app.test_client()
    assert door.post("/api/auth/enter", json={"id": appa["id"], "secret": "1234"}).status_code == 401
    assert door.post("/api/auth/enter", json={"id": appa["id"]}).status_code == 401
    r = door.post("/api/auth/enter", json={"id": appa["id"], "secret": "admin passphrase"})
    assert r.status_code == 200
    assert r.get_json()["user"]["role"] == "admin"
    assert door.get("/api/me").get_json()["can"]["manage_people"] is True
    # A disabled administrator leaves the picker, as anyone disabled does.
    conn = db.connect(app.config["LITE"].data_dir)
    auth.update_profile(conn, appa["id"], active=0)
    assert "Appa" not in profiles(app)


def test_a_person_chooses_a_photograph_as_their_picture(app, family, library):
    me = family.get("/api/me").get_json()
    assert me["avatar"] is None
    assert family.post("/api/me/avatar", json={}).status_code == 400
    assert family.post("/api/me/avatar", json={"asset_id": ids(app)["clip.mp4"]}).status_code == 400
    assert family.post("/api/me/avatar", json={"asset_id": 999_999}).status_code == 404
    r = family.post("/api/me/avatar", json={"asset_id": ids(app)["beach.jpg"]})
    assert r.status_code == 200
    me = r.get_json()
    assert me["avatar"].startswith(f"/api/avatar/{me['id']}?v=")
    square = square_of(library, me)
    assert square.is_file()
    with Image.open(square) as img:
        assert img.size == (256, 256)
        assert not img.getexif()
    # On the sign-in screen, for whoever reaches it, before any sign-in.
    assert profiles(app)["Family Person"]["avatar"] == me["avatar"]
    anyone = app.test_client()
    picture = anyone.get(me["avatar"])
    assert picture.status_code == 200
    assert picture.mimetype == "image/jpeg"
    assert "public" in picture.headers["Cache-Control"]
    with Image.open(io.BytesIO(picture.data)) as img:
        assert img.size == (256, 256)
    picture.close()
    # Choosing again is a new file at a new address, so no cache keeps the
    # old and nothing is written over a file a browser may still be reading.
    r2 = family.post("/api/me/avatar", json={"asset_id": ids(app)["sunset.jpg"]})
    assert r2.status_code == 200
    second = square_of(library, r2.get_json())
    assert second != square and second.is_file()
    assert not square.exists()                         # swept, once nothing holds it
    # Back to initials.
    assert family.delete("/api/me/avatar").get_json()["avatar"] is None
    assert not second.exists()
    assert anyone.get(me["avatar"]).status_code == 404
    assert anyone.get("/api/avatar/424242").status_code == 404


def test_a_picture_must_be_one_the_person_may_see(app, admin, family):
    target = ids(app)["beach.jpg"]
    assert admin.post("/api/visibility", json={"ids": [target], "visibility": "hidden"}).status_code == 200
    assert family.post("/api/me/avatar", json={"asset_id": target}).status_code == 404
    # Not even an admin's: the sign-in screen shows a profile picture to anyone.
    assert admin.post("/api/me/avatar", json={"asset_id": target}).status_code == 400


def test_nobody_anonymous_sets_a_picture(app):
    assert app.test_client().post("/api/me/avatar", json={"asset_id": 1}).status_code == 401
    assert app.test_client().delete("/api/me/avatar").status_code == 401


def test_an_administrator_takes_a_picture_down_and_a_removed_person_leaves_no_file(app, admin, family, library):
    me = family.post("/api/me/avatar", json={"asset_id": ids(app)["beach.jpg"]}).get_json()
    square = square_of(library, me)
    assert square.is_file()
    assert family.delete(f"/api/people/{me['id']}/avatar").status_code == 403
    r = admin.delete(f"/api/people/{me['id']}/avatar")
    assert r.status_code == 200
    assert r.get_json()["avatar"] is None
    assert not square.exists()
    assert admin.delete("/api/people/424242/avatar").status_code == 404
    # Deleting the profile takes the picture with it.
    again = family.post("/api/me/avatar", json={"asset_id": ids(app)["beach.jpg"]}).get_json()
    square = square_of(library, again)
    assert square.is_file()
    assert admin.delete(f"/api/people/{me['id']}").status_code == 200
    assert not square.exists()


def test_an_older_index_gains_the_column(app):
    conn = db.connect(app.config["LITE"].data_dir)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert "avatar_at" in columns
    # The square honours a manual turn: a tall crop of a turned photograph is
    # still a square, taken from the picture the person sees.
    c = app.test_client()
    sign_in(app, c, "admin")
    target = ids(app)["beach.jpg"]
    assert c.post(f"/api/asset/{target}/rotate", json={"rotation": 90}).status_code == 200
    me = c.post("/api/me/avatar", json={"asset_id": target}).get_json()
    stamp = me["avatar"].split("v=")[1]
    with Image.open(os.path.join(app.config["LITE"].data_dir, "avatars",
                                 f"{me['id']}-{stamp}.jpg")) as img:
        assert img.size == (256, 256)


def test_setup_keeps_the_language_the_card_was_read_in(library):
    """Somebody who picked Tamil on the setup card gets a Tamil console: the
    language goes on the new profile and becomes the home's default, instead
    of the profile taking the home's English and undoing the choice."""
    from ninaivu_lite.config import Config
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root))
    application = create_app(cfg, scanner=Scanner(cfg.data_dir, cfg.folders))
    c = application.test_client()
    r = c.post("/api/auth/setup", json={"username": "appa", "password": "admin passphrase",
                                        "name": "Appa", "language": "ta"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["user"]["language"] == "ta"
    assert c.get("/api/me").get_json()["language"] == "ta"
    assert cfg.language == "ta"
    assert Config.load(str(data)).language == "ta"


def test_setup_without_a_language_takes_the_homes(library):
    from ninaivu_lite.config import Config
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root))
    application = create_app(cfg, scanner=Scanner(cfg.data_dir, cfg.folders))
    c = application.test_client()
    bad = c.post("/api/auth/setup", json={"username": "appa", "password": "admin passphrase",
                                          "name": "Appa", "language": "fr"})
    assert bad.status_code == 400
    r = c.post("/api/auth/setup", json={"username": "appa", "password": "admin passphrase",
                                        "name": "Appa"})
    assert r.status_code == 200
    assert r.get_json()["user"]["language"] == "en"
    assert cfg.language == "en"
