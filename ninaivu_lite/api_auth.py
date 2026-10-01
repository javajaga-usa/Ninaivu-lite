"""Sign-in and your own profile: the calls behind Ninaivu's gate and profile sheet."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from . import auth
from .common import body, cfg, conn, fail, require_signed_in, user

bp = Blueprint("auth_api", __name__)

#: Ninaivu's limits: per address and name, and per name from anywhere.
throttle_short = auth.Throttle(limit=8, window=300)
throttle_long = auth.Throttle(limit=20, window=1800)
throttle_setup = auth.Throttle(limit=10, window=300)

COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


def _is_local() -> bool:
    return auth.is_local_request(request.remote_addr, request.headers)


def _signed_in(who: auth.User, payload: dict | None = None):
    token = auth.start_session(conn(), who.id, request.headers.get("User-Agent", ""))
    response = jsonify({"ok": True, "user": auth.get_user(conn(), who.id).public(),
                        **(payload or {})})
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_TTL, httponly=True,
                        samesite="Lax", secure=request.is_secure, path="/")
    return response


def state_payload(face: str = "home") -> dict:
    who = user()
    c = cfg()
    needs_setup = auth.needs_setup(conn())
    out = {
        "setup_required": needs_setup,
        "setup_code_required": needs_setup and not _is_local(),
        "open_browsing": c.open_browsing,
        "user": who.public(),
        "signed_in": not who.anonymous,
        "app_name": "Ninaivu",
        "house_name": c.house_name_effective,
        "home_name": who.home_label or c.house_name_effective,
        "face": face,
        "default_language": c.language,
        "lock": {"signed_in": not who.anonymous, "locked": False, "lock_after": 0},
    }
    if face == "home":
        out["profiles"] = [p.picker_entry() for p in auth.pickable_profiles(conn())]
    return out


@bp.get("/api/auth/state")
def auth_state():
    face = "admin" if request.args.get("face") == "admin" else "home"
    return jsonify(state_payload(face))


@bp.get("/api/auth/profiles")
def auth_profiles():
    return jsonify(profiles=[p.picker_entry() for p in auth.pickable_profiles(conn())],
                   open_browsing=cfg().open_browsing)


@bp.get("/api/auth/session")
def auth_session():
    # The screen lock is not part of Lite: it stays off.
    return jsonify(signed_in=not user().anonymous, locked=False, lock_after=0)


@bp.post("/api/auth/setup")
def auth_setup():
    if not auth.needs_setup(conn()):
        fail(409, "This library already has an administrator.")
    key = f"setup:{request.remote_addr}"
    if throttle_setup.blocked(key, "setup:all"):
        fail(429, "Too many attempts. Wait a few minutes and try again.")
    data = body()
    if not _is_local():
        code = str(data.get("setup_code") or "").strip().upper()
        import hmac
        if not hmac.compare_digest(code, auth.setup_code()):
            throttle_setup.fail(key, "setup:all")
            fail(403, "Enter the setup code shown where Ninaivu Lite was started.",
                 setup_code_required=True)
    try:
        who = auth.create_user(conn(), str(data.get("username") or ""),
                               password=str(data.get("password") or ""),
                               name=str(data.get("name") or ""), role=auth.ROLE_ADMIN,
                               language=cfg().language)
    except auth.AccountError as exc:
        fail(400, str(exc))
    return _signed_in(who)


@bp.post("/api/auth/login")
def auth_login():
    data = body()
    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    keys = (f"{request.remote_addr}|{username}",)
    if throttle_short.blocked(*keys) or throttle_long.blocked(username):
        fail(429, "Too many attempts. Wait a few minutes and try again.")
    who = auth.authenticate(conn(), username, password)
    if who is None:
        throttle_short.fail(*keys)
        throttle_long.fail(username)
        fail(401, "That username and password don't match.")
    if data.get("console") and not who.is_admin:
        fail(403, "This console is for administrators. Use the family app to sign in.")
    throttle_short.forget(*keys)
    return _signed_in(who)


@bp.post("/api/auth/enter")
def auth_enter():
    data = body()
    try:
        user_id = int(data.get("id"))
    except (TypeError, ValueError):
        fail(400, "Pick a profile.")
    target = auth.get_user(conn(), user_id)
    if target is None or not target.active:
        fail(400, "Pick a profile.")
    if target.is_admin:
        fail(403, "Administrators sign in on the admin console.")
    keys = (f"{request.remote_addr}|enter|{user_id}",)
    if throttle_short.blocked(*keys) or throttle_long.blocked(f"enter|{user_id}"):
        fail(429, "Too many attempts. Wait a while and try again.")
    who = auth.enter_profile(conn(), user_id, str(data.get("secret") or ""))
    if who is None:
        throttle_short.fail(*keys)
        throttle_long.fail(f"enter|{user_id}")
        fail(401, "That PIN isn't right." if target.has_pin else "That password isn't right.")
    throttle_short.forget(*keys)
    return _signed_in(who)


@bp.post("/api/auth/logout")
def auth_logout():
    auth.end_session(conn(), request.cookies.get(auth.SESSION_COOKIE))
    response = jsonify(ok=True)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# --- your own profile ------------------------------------------------------------------------

@bp.get("/api/me")
def me():
    return jsonify(user().public())


@bp.post("/api/me")
def me_update():
    who = user()
    if who.anonymous:
        fail(401, "Sign in first.")
    data = body()
    changes: dict = {}
    if "name" in data:
        name = str(data.get("name") or "").strip()[:60]
        if not name:
            fail(400, "A name can't be empty.")
        changes["display_name"] = name
    if "color" in data:
        colour = str(data.get("color") or "").strip()
        if colour and not COLOR_RE.match(colour):
            fail(400, "That isn't a colour.")
        changes["color"] = colour or None
    if "home_label" in data:
        if who.is_guest:
            fail(403, "Guests cannot do that.")
        changes["home_label"] = " ".join(str(data.get("home_label") or "").split())[:40] or None
    if "language" in data:
        lang = str(data.get("language") or "").strip().lower()[:8]
        if lang and not LANG_RE.match(lang):
            fail(400, "That isn't a language.")
        changes["language"] = lang or None
    auth.update_profile(conn(), who.id, **changes)
    return jsonify(auth.get_user(conn(), who.id).public())


@bp.post("/api/me/password")
def me_password():
    who = require_signed_in()
    data = body()
    key = f"password|{who.id}"
    if throttle_short.blocked(key):
        fail(429, "Too many attempts. Wait a few minutes and try again.")
    if not who.must_change and not auth.check_own_password(
            conn(), who.id, str(data.get("current") or "")):
        throttle_short.fail(key)
        fail(403, "Your current password isn't right.")
    try:
        auth.set_password(conn(), who.id, str(data.get("password") or ""),
                          keep_token=request.cookies.get(auth.SESSION_COOKIE))
    except auth.AccountError as exc:
        fail(400, str(exc))
    return jsonify(ok=True)
