"""Security audit 2026-10-11 (b), after the large files were split: who may
call each route, and attacks on first setup, share links and sign-in."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from ninaivu_lite import api_auth, auth, db
from ninaivu_lite.app import create_app
from ninaivu_lite.config import Config

from conftest import ids, sign_in


@pytest.fixture()
def fresh_setup_limit():
    api_auth.throttle_setup._fails.clear()
    yield
    api_auth.throttle_setup._fails.clear()


def _new_library(library):
    root, data = library
    return create_app(Config(data_dir=str(data), folders=[str(root)], active=str(root)),
                      addresses=["192.168.1.20"])


# --- SEC1: a stranger cannot keep the owner from making the first administrator ----------


def test_sec1_wrong_setup_codes_from_the_network_do_not_lock_out_this_computer(
        library, fresh_setup_limit):
    app = _new_library(library)
    client = app.test_client()
    stranger = {"REMOTE_ADDR": "192.168.1.66"}
    for _ in range(12):
        r = client.post("/api/auth/setup", json={"setup_code": "WRONG", "username": "x",
                                                 "password": "a long passphrase"},
                        environ_base=stranger, headers={"Origin": "http://localhost"})
        assert r.status_code in (403, 429)
    # The stranger is refused now, whatever code they try...
    assert r.status_code == 429
    # ...but the owner, at the computer itself, needs no code and is never held back.
    r = client.post("/api/auth/setup", json={"username": "appa", "name": "Appa",
                                             "password": "a long passphrase"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 200, r.json
    conn = db.connect(app.config["LITE"].data_dir)
    assert auth.get_user_by_name(conn, "appa").is_admin


# --- every route, as each role: nothing lost its check when the files were split ----------

#: The least a caller must be for each route to answer them. "open" routes
#: answer anybody (the gallery's own visibility rules then apply); "seen"
#: routes answer only about a photograph the caller may see; "token" routes
#: only with a share link's token or the Control Panel's.
ROUTE_ROLES = {
    "family": [
        "GET /api/albums", "POST /api/albums", "GET /api/albums/<int:album_id>",
        "PATCH /api/albums/<int:album_id>", "DELETE /api/albums/<int:album_id>",
        "POST /api/albums/<int:album_id>/items", "POST /api/asset/<int:asset_id>",
        "POST /api/asset/<int:asset_id>/poster", "POST /api/assets/bulk",
        "GET /api/download/<int:asset_id>", "GET /api/download/zip",
        "POST /api/shares", "GET /api/shares", "DELETE /api/shares/<token>",
    ],
    "signed_in": ["POST /api/me", "POST /api/me/avatar", "DELETE /api/me/avatar",
                  "POST /api/me/password"],
    "seen": ["GET /api/asset/<int:asset_id>", "GET /api/file/<int:asset_id>",
             "GET /api/preview/<int:asset_id>", "GET /api/thumb/<int:asset_id>"],
    "token": ["GET /api/share/<token>", "GET /api/share/<token>/file/<int:asset_id>",
              "GET /api/share/<token>/preview/<int:asset_id>",
              "GET /api/share/<token>/thumb/<int:asset_id>",
              "POST /api/share/<token>/unlock", "GET /share/<token>", "POST /api/local/stop"],
    "open": [
        "GET /", "GET /admin", "GET /admin/", "GET /admin/manifest.webmanifest",
        "GET /manifest.webmanifest", "GET /sw.js", "GET /healthz", "GET /readyz",
        "GET /api/health", "POST /api/auth/enter", "POST /api/auth/login",
        "POST /api/auth/logout", "GET /api/auth/profiles", "GET /api/auth/session",
        "POST /api/auth/setup", "GET /api/auth/state", "GET /api/avatar/<int:user_id>",
        "GET /api/me", "GET /api/facets", "GET /api/months", "GET /api/segments",
        "GET /api/status", "GET /api/status/activity", "GET /api/status/scan",
        "GET /api/status/stats", "GET /api/suggest",
    ],
}
#: Everything else is for administrators, and known by its address.
ADMIN_PREFIXES = ("/admin/", "/api/admin/", "/api/archive/", "/api/people", "/api/library/",
                  "/api/visibility", "/api/scan", "/api/assets/rotate",
                  "/api/asset/<int:asset_id>/rotate", "/api/asset/<int:asset_id>/edit")


def _routes(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield method, rule.rule


def _role_of(method: str, rule: str) -> str | None:
    for role, routes in ROUTE_ROLES.items():
        if f"{method} {rule}" in routes:
            return role
    return "admin" if rule.startswith(ADMIN_PREFIXES) else None


def test_sec_every_route_has_a_known_least_role(app):
    unknown = [f"{m} {r}" for m, r in _routes(app) if _role_of(m, r) is None]
    # A route added or moved must say here who may call it.
    assert not unknown


def test_sec_no_route_answers_anyone_below_its_least_role(app):
    callers = {}
    for role in ("anonymous", "guest", "family", "admin"):
        client = app.test_client()
        client.environ_base["HTTP_ORIGIN"] = "http://localhost"
        if role != "anonymous":
            sign_in(app, client, role, username=None if role == "admin" else f"m-{role}")
        callers[role] = client
    conn = db.connect(app.config["LITE"].data_dir)
    admin_id = auth.get_user_by_name(conn, "appa").id
    # A Public photograph, which everyone may see: only the role check stands
    # between a guest and, say, downloading it. A Hidden one for the routes
    # that answer about a photograph by what the caller may see.
    public, hidden, clip = ids(app)["beach.jpg"], ids(app)["sunset.jpg"], ids(app)["clip.mp4"]
    with conn:
        conn.execute("UPDATE assets SET visibility = ? WHERE id IN (?, ?)",
                     (db.VIS_PUBLIC, public, clip))
        conn.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_HIDDEN, hidden))
    album = callers["admin"].post("/api/albums", json={"name": "A", "ids": [public]}).json["id"]
    allowed = {"admin": {"admin"}, "family": {"family", "admin"},
               "signed_in": {"guest", "family", "admin"}, "seen": {"admin"},
               "token": set(), "open": {"anonymous", "guest", "family", "admin"}}
    picture = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(picture, "PNG")
    # What each route needs to do its work, so that the role check is all that
    # refuses: a request the route cannot read would be refused anyway.
    sent = {
        "GET /api/download/zip": {"query_string": {"ids": str(public)}},
        "POST /api/albums": {"json": {"name": "B", "ids": [public]}},
        "PATCH /api/albums/<int:album_id>": {"json": {"name": "C"}},
        "POST /api/albums/<int:album_id>/items": {"json": {"ids": [public]}},
        "POST /api/asset/<int:asset_id>": {"json": {"favorite": True}},
        "POST /api/assets/bulk": {"json": {"ids": [public], "favorite": True}},
        "POST /api/asset/<int:asset_id>/poster": {"url_asset": clip,
                                                  "data": picture.getvalue(),
                                                  "content_type": "image/png"},
        "POST /api/asset/<int:asset_id>/edited-copy": {"data": picture.getvalue(),
                                                       "content_type": "image/png"},
        "POST /api/shares": {"json": {"scope": "asset", "target_id": public}},
        "POST /api/me": {"json": {"name": "Someone"}},
        "POST /api/me/avatar": {"json": {"asset_id": public}},
        "POST /api/visibility": {"json": {"ids": [public], "visibility": "public"}},
        "POST /api/assets/rotate": {"json": {"ids": [public], "turn": 360}},
        "POST /api/asset/<int:asset_id>/rotate": {"json": {"rotation": 0}},
    }
    wrong = []
    for method, rule in _routes(app):
        least = _role_of(method, rule)
        how = dict(sent.get(f"{method} {rule}", {}))
        asset = how.pop("url_asset", hidden if least == "seen" else public)
        if "json" not in how and "data" not in how and method != "GET":
            how["json"] = {}
        url = (rule.replace("<int:asset_id>", str(asset)).replace("<int:album_id>", str(album))
               .replace("<int:user_id>", str(admin_id)).replace("<token>", "no-such-link"))
        for role, client in callers.items():
            if role in allowed[least]:
                continue
            r = client.open(url, method=method, headers={"X-Ninaivu": "1"}, **how)
            if r.status_code < 400:
                wrong.append(f"{method} {rule} answered {role}: {r.status_code}")
    assert not wrong
