"""The admin console's calls (ninaivu_lite/api_admin.py)."""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import pytest
from conftest import ids, make_jpeg, sign_in

from ninaivu_lite import auth, db
from ninaivu_lite.config import Config

ROUTES = [
    ("get", "/api/admin/overview"),
    ("get", "/api/admin/preview?role=guest"),
    ("get", "/api/library/browse"),
    ("post", "/api/library/root"),
    ("delete", "/api/admin/libraries?path=/x"),
    ("post", "/api/admin/libraries/active"),
    ("post", "/api/scan"),
    ("get", "/api/admin/assignable"),
    ("post", "/api/admin/settings"),
    ("get", "/api/admin/first-day"),
    ("post", "/api/admin/first-day"),
    ("get", "/api/people"),
    ("post", "/api/people"),
    ("post", "/api/people/1"),
    ("post", "/api/people/1/signout"),
    ("delete", "/api/people/1"),
    ("get", "/api/admin/folders"),
    ("post", "/api/visibility/folder"),
    ("get", "/api/visibility/history"),
    ("post", "/api/visibility/undo"),
    ("get", "/admin/backup"),
    ("get", "/admin/export"),
]


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


def call(client, method, url, payload=None):
    kwargs = {} if method in ("get", "delete") else {"json": payload or {}}
    return getattr(client, method)(url, **kwargs)


# --- the guard -----------------------------------------------------------------------


@pytest.mark.parametrize("method,url", ROUTES)
def test_anonymous_gets_401_json(app, method, url):
    r = call(app.test_client(), method, url)
    assert r.status_code == 401
    assert r.get_json()["error"] == "Administrator sign-in required."


@pytest.mark.parametrize("method,url", ROUTES)
def test_family_gets_403(family, method, url):
    r = call(family, method, url)
    assert r.status_code == 403
    assert r.get_json()["error"] == "Family members cannot do that."


def test_anonymous_downloads_on_a_private_library_are_401_json(app):
    app.config["LITE"].open_browsing = False
    for url in ("/admin/backup", "/admin/export"):
        r = app.test_client().get(url)
        assert r.status_code == 401 and r.is_json


# --- overview and preview -----------------------------------------------------------


def test_overview_shape(app, admin, library):
    root, _ = library
    data = admin.get("/api/admin/overview").get_json()
    assert data["app"]["home_url"] == "/"
    assert data["app"]["house_name"] == "" and data["app"]["house_name_effective"] == "Ninaivu"
    assert data["app"]["default_language"] == "en"
    assert set(data["app"]) >= {"version", "open_browsing", "watch"}
    assert data["library"]["root"] == str(root)
    assert data["library"]["roots"] == [str(root)]
    folder = data["library"]["folders"][0]
    assert folder == {"path": str(root), "name": "Photos", "exists": True, "count": 6,
                      "bytes": folder["bytes"], "assigned": 0, "active": True}
    assert folder["bytes"] > 0
    assert data["capabilities"]["opencv"] is False
    assert set(data["capabilities"]) == {"ffmpeg", "heif", "opencv"}
    assert data["people"]["by_role"] == {"guest": 0, "family": 0, "admin": 1}
    assert data["people"]["total"] == 1 and data["people"]["signed_in"] == 1
    stats = data["stats"]
    assert stats["count"] == 6 and stats["videos"] == 1 and stats["pictures"] == 5
    assert stats["audio"] == 0 and stats["public"] == 0 and stats["hidden"] == 0


def test_preview_by_role_and_person(app, admin, library):
    root, _ = library
    data = admin.get("/api/admin/preview?role=guest").get_json()
    assert data["as"] == "Guest" and data["total"] == 0 and data["items"] == []
    assert data["scope"] is None
    data = admin.get("/api/admin/preview?role=family").get_json()
    assert data["total"] == 6 and len(data["items"]) == 6
    item = data["items"][0]
    assert set(item) == {"id", "name", "kind", "folder", "has_thumb", "thumb_v", "visibility"}
    assert item["visibility"] == "family"

    kid = auth.create_user(conn_of(app), "kutti", name="Kutti", role="family",
                           library=str(root / "2019"))
    data = admin.get(f"/api/admin/preview?person={kid.id}").get_json()
    assert data["as"] == "Kutti" and data["total"] == 2
    assert {i["name"] for i in data["items"]} == {"beach.jpg", "sunset.jpg"}
    assert admin.get("/api/admin/preview?person=999").status_code == 404
    r = admin.get("/api/admin/preview?role=boss")
    assert r.status_code == 400 and r.get_json()["error"] == "Unknown role."


# --- library folders -----------------------------------------------------------------


def test_browse(admin, library, tmp_path):
    root, _ = library
    data = admin.get("/api/library/browse").get_json()
    assert data["path"] == str(root)
    assert data["parent"] == str(root.parent)
    assert [d["name"] for d in data["dirs"]] == ["2019", "family"]     # hidden skipped
    assert data["dirs"][0]["path"] == str(root / "2019")
    assert data["selectable"] is True
    assert {"name": "Photos (current)", "path": str(root)} in data["shortcuts"]
    data = admin.get("/api/library/browse", query_string={"path": str(root / "family")})
    assert [d["name"] for d in data.get_json()["dirs"]] == ["pongal"]
    r = admin.get("/api/library/browse", query_string={"path": str(tmp_path / "nope")})
    assert r.status_code == 404 and r.get_json()["error"] == "Not a directory"
    if sys.platform != "win32":      # /proc, /sys and /dev exist only there
        assert admin.get("/api/library/browse?path=/proc").status_code == 403
    # Inside an existing library folder: can't be added on its own.
    data = admin.get("/api/library/browse", query_string={"path": str(root / "2019")})
    assert data.get_json()["selectable"] is False


def test_add_library_folder_and_make_active(app, admin, library, tmp_path):
    root, data_dir = library
    more = tmp_path / "More"
    make_jpeg(more / "a.jpg", "2020:02:02 10:00:00")
    r = admin.post("/api/library/root", json={"path": str(more)})
    assert r.status_code == 200
    out = r.get_json()
    assert out == {"ok": True, "root": str(more), "added": True,
                   "folders": [str(root), str(more)]}
    saved = Config.load(data_dir)
    assert saved.folders == [str(root), str(more)] and saved.active_folder == str(more)
    assert app.config["SCANNER"].folders == [str(root), str(more)]
    app.config["SCANNER"].scan_once(conn_of(app))
    overview = admin.get("/api/admin/overview").get_json()
    counts = {f["path"]: (f["count"], f["active"]) for f in overview["library"]["folders"]}
    assert counts == {str(root): (6, False), str(more): (1, True)}

    # Adding it again only makes it the default.
    admin.post("/api/admin/libraries/active", json={"path": str(root)})
    assert Config.load(data_dir).active_folder == str(root)
    r = admin.post("/api/library/root", json={"path": str(more)})
    assert r.get_json()["added"] is False and r.get_json()["root"] == str(more)

    r = admin.post("/api/admin/libraries/active", json={"path": "/not/there"})
    assert r.status_code == 404
    assert r.get_json()["error"] == "That folder is not in the library."


def test_add_library_refusals(admin, library, tmp_path):
    root, data_dir = library
    r = admin.post("/api/library/root", json={"path": ""})
    assert r.status_code == 400
    r = admin.post("/api/library/root", json={"path": str(tmp_path / "missing")})
    assert r.status_code == 400
    assert r.get_json()["error"] == "That folder does not exist on this computer."
    system = os.environ.get("SystemRoot", "C:\\Windows") if sys.platform == "win32" else "/etc"
    r = admin.post("/api/library/root", json={"path": system})
    assert r.status_code == 403
    assert r.get_json()["error"].startswith("That is a system folder")
    data_dir.mkdir(exist_ok=True)
    r = admin.post("/api/library/root", json={"path": str(data_dir)})
    assert r.status_code == 403
    r = admin.post("/api/library/root", json={"path": str(root / "2019")})
    assert r.status_code == 400
    assert r.get_json()["error"] == "That folder is already in the library."
    r = admin.post("/api/library/root", json={"path": str(tmp_path)})
    assert r.status_code == 400 and "contains one" in r.get_json()["error"]


def test_remove_library_with_assigned_people(app, admin, library, tmp_path):
    root, data_dir = library
    more = tmp_path / "More"
    make_jpeg(more / "a.jpg")
    admin.post("/api/library/root", json={"path": str(more)})
    app.config["SCANNER"].scan_once(conn_of(app))
    auth.create_user(conn_of(app), "kutti", name="Kutti", role="family",
                     library=str(more))
    assert admin.get("/api/admin/overview").get_json()["library"]["folders"][1]["assigned"] == 1

    assert admin.delete("/api/admin/libraries").status_code == 400
    r = admin.delete("/api/admin/libraries", query_string={"path": "/nowhere"})
    assert r.status_code == 404
    r = admin.delete("/api/admin/libraries", query_string={"path": str(more)})
    assert r.status_code == 409
    assert r.get_json()["assigned"] == ["Kutti"]
    assert r.get_json()["error"].startswith("Kutti is assigned to that folder.")
    r = admin.delete("/api/admin/libraries", query_string={"path": str(more), "force": "1"})
    assert r.status_code == 200
    assert [f["path"] for f in r.get_json()["folders"]] == [str(root)]
    saved = Config.load(data_dir)
    assert saved.folders == [str(root)] and saved.active_folder == str(root)
    # The index rows went at once; the assignment stays (and now sees nothing).
    c = conn_of(app)
    assert c.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 6
    assert auth.get_user_by_name(c, "kutti").library == str(more)
    assert (more / "a.jpg").exists()


def test_scan_and_assignable(app, admin, library):
    root, _ = library
    r = admin.post("/api/scan", json={"full": True})
    assert r.get_json() == {"ok": True, "full": True, "folders": [str(root)]}
    data = admin.get("/api/admin/assignable").get_json()["folders"]
    assert data[0] == {"path": str(root), "label": "Photos", "depth": 0, "count": 6,
                       "is_root": True}
    assert [(f["path"], f["depth"], f["count"]) for f in data[1:]] == [
        (str(root / "2019"), 1, 2), (str(root / "family"), 1, 2),
        (str(root / "family" / "pongal"), 2, 1)]


# --- settings and first day -----------------------------------------------------------


def test_settings_echo_and_persist(app, admin, library):
    _, data_dir = library
    r = admin.post("/api/admin/settings", json={
        "house_name": "  Our   Home ", "open_browsing": False, "watch": False,
        "language": "ta", "ai_enabled": True})
    data = r.get_json()
    assert data["ok"] is True
    assert set(data["changed"]) == {"house_name", "open_browsing", "watch", "language"}
    assert data["settings"] == {"house_name": "Our Home", "house_name_effective": "Our Home",
                                "open_browsing": False, "watch": False, "language": "ta",
                                "default_language": "ta"}
    assert app.config["SCANNER"].auto is False
    saved = Config.load(data_dir)
    assert (saved.house_name, saved.open_browsing, saved.watch, saved.language) == \
        ("Our Home", False, False, "ta")
    data = admin.post("/api/admin/settings", json={"house_name": "",
                                                   "default_language": "en"}).get_json()
    assert data["settings"]["house_name_effective"] == "Ninaivu"
    assert data["settings"]["default_language"] == "en"
    assert admin.post("/api/admin/settings", json={"language": "fr"}).status_code == 400
    r = admin.post("/api/admin/settings", json={"watch": "yes", "house_name": "X"})
    assert r.status_code == 400
    assert Config.load(data_dir).house_name == ""          # nothing half-applied


def test_first_day(app, admin, library):
    root, data_dir = library
    data = admin.get("/api/admin/first-day").get_json()
    assert data == {"done": False, "library": {"chosen": True, "root": str(root)}, "people": 0}
    assert admin.post("/api/admin/first-day", json={}).get_json() == {"done": True}
    assert admin.get("/api/admin/first-day").get_json()["done"] is True
    assert Config.load(data_dir).first_day_done is True


# --- people ----------------------------------------------------------------------------


def person(admin, username):
    people = admin.get("/api/people").get_json()["people"]
    return next(p for p in people if p["username"] == username)


def test_people_list_and_create(app, admin, library):
    root, _ = library
    data = admin.get("/api/people").get_json()
    assert data["roles"] == [{"value": "guest", "label": "Guest"},
                             {"value": "family", "label": "Family member"},
                             {"value": "admin", "label": "Admin"}]
    appa = data["people"][0]
    assert appa["username"] == "appa" and appa["has_password"] and appa["sessions"] == 1
    assert appa["visible_count"] == 6 and appa["entry"] == "password"

    r = admin.post("/api/people", json={"name": "Meena", "username": "meena",
                                        "role": "family", "library": str(root / "family"),
                                        "password": "", "pin": "2468"})
    assert r.status_code == 200
    meena = r.get_json()["person"]
    assert meena["library"] == str(root / "family") and meena["entry"] == "pin"
    assert meena["has_pin"] and not meena["has_password"] and meena["visible_count"] == 2
    assert meena["must_change"] is False

    r = admin.post("/api/people", json={"username": "ravi", "role": "guest",
                                        "password": "a good secret"})
    assert r.get_json()["person"]["must_change"] is True

    for payload, message in [
        ({"username": "x1", "role": "boss"}, "Unknown role."),
        ({"username": "meena", "role": "family"}, "That username is already taken."),
        ({"username": "kid", "role": "family", "pin": "1234"}, "That PIN is too easy to guess."),
        ({"username": "kid", "role": "family", "pin": "12"}, "A PIN is 4 to 8 digits."),
        ({"username": "kid", "role": "family", "password": "short"},
         "Use at least 8 characters."),
        ({"username": "kid", "role": "family", "password": "password"},
         "That password is too common — pick something else."),
        ({"username": "kid", "role": "family", "library": "/somewhere/else"},
         "“/somewhere/else” is not inside any library folder. "
         "Add it on the Library tab first."),
    ]:
        r = admin.post("/api/people", json=payload)
        assert r.status_code == 400, payload
        assert r.get_json()["error"] == message


def test_edit_person(app, admin, library):
    root, _ = library
    meena = admin.post("/api/people", json={"name": "Meena", "username": "meena",
                                            "role": "family"}).get_json()["person"]
    url = f"/api/people/{meena['id']}"
    assert admin.post("/api/people/999", json={"role": "guest"}).status_code == 404

    r = admin.post(url, json={"role": "guest"})
    assert r.get_json()["person"]["role"] == "guest"
    assert r.get_json()["person"]["visible_count"] == 0
    assert admin.post(url, json={"role": "boss"}).status_code == 400

    r = admin.post(url, json={"role": "admin"})
    assert r.status_code == 400
    assert r.get_json()["error"].startswith("Meena has no password, and an administrator")

    r = admin.post(url, json={"library": str(root / "2019")})
    assert r.get_json()["person"]["library"] == str(root / "2019")
    r = admin.post(url, json={"library": "/elsewhere"})
    assert r.status_code == 400 and "not inside any library folder" in r.get_json()["error"]
    assert admin.post(url, json={"library": ""}).get_json()["person"]["library"] is None

    assert admin.post(url, json={"pin": "1357"}).get_json()["person"]["entry"] == "pin"
    r = admin.post(url, json={"pin": "0000", "role": "family"})
    assert r.status_code == 400
    assert person(admin, "meena")["role"] == "guest"     # nothing half-applied
    assert admin.post(url, json={"pin": ""}).get_json()["person"]["entry"] == "open"

    assert admin.post(url, json={"name": "Meena Amma"}).get_json()["person"]["name"] == \
        "Meena Amma"

    # Reset password: temporary, and every device signs out.
    client = app.test_client()
    token = auth.start_session(conn_of(app), meena["id"])
    client.set_cookie(auth.SESSION_COOKIE, token)
    assert person(admin, "meena")["sessions"] == 1
    assert admin.post(url, json={"password": "short"}).status_code == 400
    p = admin.post(url, json={"password": "river-maple-42"}).get_json()["person"]
    assert p["must_change"] is True and p["has_password"] and p["sessions"] == 0

    # Promote with a password; their sessions end.
    token = auth.start_session(conn_of(app), meena["id"])
    p = admin.post(url, json={"role": "admin", "password": "river-maple-43"}).get_json()
    assert p["person"]["role"] == "admin" and p["person"]["sessions"] == 0

    # Disable and enable.
    p = admin.post(url, json={"active": False}).get_json()["person"]
    assert p["active"] is False
    assert admin.post(url, json={"active": True}).get_json()["person"]["active"] is True

    # Sign out everywhere.
    auth.start_session(conn_of(app), meena["id"])
    auth.start_session(conn_of(app), meena["id"])
    r = admin.post(f"{url}/signout")
    assert r.get_json() == {"ok": True, "sessions_ended": 2}


def test_last_admin_protections(app, admin):
    appa = person(admin, "appa")
    url = f"/api/people/{appa['id']}"
    r = admin.post(url, json={"role": "family"})
    assert r.status_code == 409
    assert r.get_json()["error"] == \
        "This is the only administrator — promote someone else first."
    r = admin.post(url, json={"active": False})
    assert r.status_code == 409
    assert r.get_json()["error"] == "You can't disable the only administrator."
    r = admin.delete(url)
    assert r.status_code == 409
    assert r.get_json()["error"].startswith("You can't delete the profile you're signed in")

    # A second admin: still can't disable yourself; the other can be removed.
    other = admin.post("/api/people", json={"username": "amma", "role": "admin",
                                            "password": "another secret"}).get_json()
    r = admin.post(url, json={"active": False})
    assert r.status_code == 409
    assert r.get_json()["error"] == "You can't disable your own profile."
    # Another admin can be removed.
    c2 = app.test_client()
    sign_in(app, c2, "admin")
    r = c2.delete(f"/api/people/{other['person']['id']}")
    assert r.status_code == 200
    assert r.get_json()["removed"]["username"] == "amma"


def test_delete_person(app, admin, library):
    meena = admin.post("/api/people", json={"username": "meena",
                                            "role": "family"}).get_json()["person"]
    c = conn_of(app)
    asset = ids(app)["beach.jpg"]
    with c:
        c.execute("INSERT INTO user_assets (user_id, asset_id, favorite) VALUES (?,?,1)",
                  (meena["id"], asset))
        c.execute("INSERT INTO albums (name, created_by) VALUES ('Trip', ?)", (meena["id"],))
    assert person(admin, "meena")["favorites"] == 1
    r = admin.delete(f"/api/people/{meena['id']}")
    assert r.status_code == 200
    removed = r.get_json()["removed"]
    assert removed["username"] == "meena" and removed["role"] == "family"
    assert removed["favorites"] == 1
    assert admin.delete(f"/api/people/{meena['id']}").status_code == 404
    appa = auth.get_user_by_name(c, "appa")
    assert c.execute("SELECT created_by FROM albums").fetchone()[0] == appa.id


# --- visibility ------------------------------------------------------------------------


def vis(app):
    return {r["name"]: (r["visibility"], r["vis_source"]) for r in conn_of(app).execute(
        "SELECT name, visibility, vis_source FROM assets")}


def test_folder_tree(admin):
    data = admin.get("/api/admin/folders").get_json()
    assert [f["path"] for f in data["folders"]] == ["2019", "family", "family/pongal"]
    fam = data["folders"][1]
    assert fam == {"path": "family", "name": "family", "depth": 0, "count": 2, "public": 0,
                   "family": 2, "hidden": 0, "rule": None, "effective": None}
    assert data["folders"][2]["depth"] == 1
    assert data["root_rule"] is None


def test_folder_rule_confirm_apply_and_new_files(app, admin, library):
    root, _ = library
    r = admin.post("/api/visibility/folder", json={"folder": "", "visibility": "hidden"})
    assert r.status_code == 403
    r = admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "secret"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Visibility must be public, family or hidden."

    # Narrowing needs no confirmation.
    r = admin.post("/api/visibility/folder", json={"folder": "family/pongal",
                                                   "visibility": "hidden"})
    assert r.status_code == 200
    out = r.get_json()
    assert out["updated"] == 1 and out["visibility"] == "hidden"
    assert out["impact"]["restricted"] == 1 and out["impact"]["exposed"] == 0
    assert vis(app)["IMG_20230115_091500.jpg"] == (2, "rule")

    # Widening the parent asks first, and says what it would show.
    r = admin.post("/api/visibility/folder", json={"folder": "family", "visibility": "public"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["needs_confirmation"] is True
    assert body["impact"] == {"folder": "family", "visibility": 0, "total": 2, "exposed": 2,
                              "restricted": 0, "hidden_by_the_filesystem": 0,
                              "decided_individually": 0, "child_rules": 1}
    assert vis(app)["clip.mp4"] == (1, "default")              # nothing changed yet
    r = admin.post("/api/visibility/folder", json={"folder": "family", "visibility": "public",
                                                   "confirm": True})
    assert r.status_code == 200 and r.get_json()["updated"] == 2
    v = vis(app)
    assert v["clip.mp4"] == (0, "rule") and v["IMG_20230115_091500.jpg"] == (0, "rule")
    tree = admin.get("/api/admin/folders").get_json()
    rules = {f["path"]: (f["rule"], f["effective"]) for f in tree["folders"]}
    assert rules == {"2019": (None, None), "family": ("public", "public"),
                     "family/pongal": (None, "public")}            # child rule gave way
    assert app.test_client().get("/api/admin/overview").status_code == 401

    # A new file in that folder gets the rule when it is scanned.
    make_jpeg(root / "family" / "pongal" / "new.jpg", "2024:01:15 08:00:00")
    app.config["SCANNER"].scan_once(conn_of(app))
    assert vis(app)["new.jpg"] == (0, "rule")
    assert admin.get("/api/admin/preview?role=guest").get_json()["total"] == 3


def test_history_and_undo_restore_exact_values(app, admin):
    c = conn_of(app)
    asset = ids(app)["beach.jpg"]
    with c:     # an earlier per-item decision inside the folder
        c.execute("UPDATE assets SET visibility = 2, vis_source = 'item' WHERE id = ?", (asset,))
    admin.post("/api/visibility/folder", json={"folder": "2019/x", "visibility": "family"})
    with c:
        c.execute("INSERT INTO folder_rules (folder_id, dir, visibility) "
                  "SELECT folder_id, '2019/inner', 2 FROM assets LIMIT 1")
    before = vis(app)
    rules_before = {r["dir"]: r["visibility"] for r in c.execute("SELECT * FROM folder_rules")}
    r = admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "public",
                                                   "confirm": True})
    assert r.status_code == 200
    assert vis(app)["beach.jpg"] == (0, "rule")

    history = admin.get("/api/visibility/history").get_json()
    latest = history["changes"][0]
    assert latest["scope"] == "folder" and latest["folder"] == "2019"
    assert latest["visibility"] == 0 and latest["exposed"] == 2
    assert latest["restorable"] == 2 and latest["undone_at"] is None
    assert latest["had_rule"] == 0
    assert history["names"] == {"0": "public", "1": "family", "2": "hidden"}

    r = admin.post("/api/visibility/undo", json={"batch_id": None})
    assert r.status_code == 200
    assert r.get_json()["restored"] == 2 and r.get_json()["folder"] == "2019"
    assert vis(app) == before
    assert {r["dir"]: r["visibility"] for r in c.execute("SELECT * FROM folder_rules")} == \
        rules_before
    assert admin.get("/api/visibility/history").get_json()["changes"][0]["undone_at"]

    batch = latest["id"]
    r = admin.post("/api/visibility/undo", json={"batch_id": batch})
    assert r.status_code == 409
    assert r.get_json() == {"ok": False, "error": "That change has already been undone.",
                            "status": 409}
    r = admin.post("/api/visibility/undo", json={"batch_id": 999})
    assert r.get_json()["error"] == "That change is no longer in the history."
    r = admin.post("/api/visibility/undo", json={"batch_id": "x"})
    assert r.status_code == 400
    # The first (narrow) change is still undoable, then nothing is left.
    assert admin.post("/api/visibility/undo", json={"batch_id": None}).status_code == 200
    r = admin.post("/api/visibility/undo", json={"batch_id": None})
    assert r.status_code == 409 and r.get_json()["error"] == "There is nothing to undo."


def test_undo_replaces_prior_rule(app, admin):
    admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "hidden"})
    admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "family",
                                               "confirm": True})
    admin.post("/api/visibility/undo", json={"batch_id": None})
    tree = admin.get("/api/admin/folders").get_json()
    assert tree["folders"][0]["rule"] == "hidden"
    assert vis(app)["beach.jpg"] == (2, "rule")


def test_items_batches_are_undoable(app, admin):
    """A batch written by the gallery's per-item route (scope 'items')."""
    c = conn_of(app)
    asset = ids(app)["portrait.jpg"]
    with c:
        cur = c.execute("INSERT INTO visibility_batches (created_at, scope, visibility, "
                        "affected, exposed) VALUES (1, 'items', 0, 1, 1)")
        c.execute("INSERT INTO visibility_undo VALUES (?, ?, 1, 'default')",
                  (cur.lastrowid, asset))
        c.execute("UPDATE assets SET visibility = 0, vis_source = 'item' WHERE id = ?", (asset,))
    change = admin.get("/api/visibility/history").get_json()["changes"][0]
    assert change["scope"] == "items" and change["affected"] == 1 and change["restorable"] == 1
    r = admin.post("/api/visibility/undo", json={"batch_id": None})
    assert r.get_json()["restored"] == 1
    assert vis(app)["portrait.jpg"] == (1, "default")


def test_history_is_capped(app, admin):
    for i in range(23):
        admin.post("/api/visibility/folder", json={
            "folder": "2019", "visibility": "hidden" if i % 2 == 0 else "family",
            "confirm": True})
    c = conn_of(app)
    assert c.execute("SELECT COUNT(*) FROM visibility_batches").fetchone()[0] == 20
    assert len(admin.get("/api/visibility/history").get_json()["changes"]) == 10


# --- downloads ------------------------------------------------------------------------


def test_backup_zip(app, admin):
    r = admin.get("/admin/backup")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert r.headers["Content-Disposition"].startswith(
        'attachment; filename="ninaivu-lite-backup-')
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert "ninaivu-lite.db" in names and "README.txt" in names


def test_export_json(app, admin, library):
    root, _ = library
    meena = admin.post("/api/people", json={"name": "Meena", "username": "meena",
                                            "role": "family", "pin": "2468",
                                            "library": str(root / "family")}).get_json()
    admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "hidden"})
    c = conn_of(app)
    asset = ids(app)["clip.mp4"]
    with c:
        c.execute("INSERT INTO user_assets (user_id, asset_id, favorite) VALUES (?,?,1)",
                  (meena["person"]["id"], asset))
        album = c.execute("INSERT INTO albums (name, created_by, cover_id) VALUES ('Pongal',?,?)",
                          (meena["person"]["id"], asset)).lastrowid
        c.execute("INSERT INTO album_items (album_id, asset_id) VALUES (?,?)", (album, asset))
        c.execute("INSERT INTO shares (token, scope, target_id, created_by, view_count) "
                  "VALUES ('tok', 'album', ?, ?, 3)", (album, meena["person"]["id"]))
    r = admin.get("/admin/export")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert 'filename="ninaivu-lite-export-' in r.headers["Content-Disposition"]
    data = json.loads(r.data)
    assert data["format"] == "ninaivu-lite-export" and data["format_version"] == 1
    assert data["folders"] == [str(root)]
    people = {p["username"]: p for p in data["people"]}
    assert people["appa"]["password_scheme"] in ("scrypt", "pbkdf2")
    assert people["meena"]["password"] is None and people["meena"]["pin"]
    assert people["meena"]["library"] == str(root / "family")
    assert data["folder_rules"] == [{"folder": str(root), "path": "2019", "level": 2,
                                     "created_at": data["folder_rules"][0]["created_at"]}]
    levels = {v["path"]: (v["level"], v["source"]) for v in data["visibility"]}
    assert levels == {"2019/beach.jpg": (2, "rule"), "2019/sunset.jpg": (2, "rule")}
    assert data["favourites"][0]["path"] == "family/clip.mp4"
    assert data["favourites"][0]["user"] == "meena"
    assert data["albums"][0]["owner"] == "meena"
    assert data["albums"][0]["cover"] == {"folder": str(root), "path": "family/clip.mp4"}
    assert data["albums"][0]["items"][0]["path"] == "family/clip.mp4"
    assert data["shares"] == [{"token": "tok", "kind": "album", "owner": "meena",
                               "password": None, "expires_at": None,
                               "created_at": data["shares"][0]["created_at"], "views": 3,
                               "album_id": album}]
