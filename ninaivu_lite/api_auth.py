"""Sign-in and your own profile: the calls behind Ninaivu's gate and profile sheet."""

from __future__ import annotations

import logging
import os
import re
import time

from flask import Blueprint, jsonify, request, send_file

from . import auth, db, media
from .common import (asset_path, avatar_file, avatar_path, body, cfg, conn, drop_avatar,
                     fail, require_signed_in, sweep_avatars, user, visible_asset)
from .dates import long_path

log = logging.getLogger(__name__)

bp = Blueprint("auth_api", __name__)

#: Ninaivu's limits: per address and account, and per account from anywhere.
#: The username login and the profile picker count against the same account.
#: The day's limit is what keeps a 4-digit PIN out of reach (5,000 guesses
#: on average, 60 a day). The account-wide limits never lock out this
#: computer itself, so nobody on the network can keep the owner out.
throttle_short = auth.Throttle(limit=8, window=300)
throttle_long = auth.Throttle(limit=20, window=1800)
throttle_day = auth.Throttle(limit=60, window=86400)
throttle_setup = auth.Throttle(limit=10, window=300)

COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
LANGUAGES = ("en", "ta")


def _is_local() -> bool:
    return auth.is_local_request(request.remote_addr, request.headers)


def _signed_in(who: auth.User, payload: dict | None = None):
    # The session this browser had (another profile, before a switch) ends
    # here, rather than living on unseen for its thirty days.
    auth.end_session(conn(), request.cookies.get(auth.SESSION_COOKIE))
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
    # The language the setup card was read in: the administrator's own, and
    # the home's default for everyone who has not chosen one yet.
    language = str(data.get("language") or "").strip().lower()
    if language and language not in LANGUAGES:
        fail(400, "The language must be en or ta.")
    try:
        who = auth.create_user(conn(), str(data.get("username") or ""),
                               password=str(data.get("password") or ""),
                               name=str(data.get("name") or ""), role=auth.ROLE_ADMIN,
                               language=language or cfg().language)
    except auth.AccountError as exc:
        fail(400, str(exc))
    if language and language != cfg().language:
        cfg().language = language
        cfg().save()
    return _signed_in(who)


def _too_many(account: str) -> bool:
    """Whether this address, or anybody but this computer, has used up the
    tries for *account*."""
    if throttle_short.blocked(f"{request.remote_addr}|{account}"):
        return True
    return not _is_local() and (throttle_long.blocked(account) or throttle_day.blocked(account))


def _failed(account: str) -> None:
    log.warning("sign-in failed for %r from %s", account, request.remote_addr)
    throttle_short.fail(f"{request.remote_addr}|{account}")
    throttle_long.fail(account)
    throttle_day.fail(account)


@bp.post("/api/auth/login")
def auth_login():
    data = body()
    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    known = auth.get_user_by_name(conn(), username) if username else None
    account = f"account|{known.id}" if known else f"name|{username}"
    if _too_many(account):
        fail(429, "Too many attempts. Wait a few minutes and try again.")
    who = auth.authenticate(conn(), username, password)
    if who is None:
        _failed(account)
        fail(401, "That username and password don't match.")
    if data.get("console") and not who.is_admin:
        fail(403, "This console is for administrators. Use the family app to sign in.")
    throttle_short.forget(f"{request.remote_addr}|{account}")
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
    account = f"account|{user_id}"
    if _too_many(account):
        fail(429, "Too many attempts. Wait a while and try again.")
    who = auth.enter_profile(conn(), user_id, str(data.get("secret") or ""))
    if who is None:
        _failed(account)
        fail(401, "That PIN isn't right." if target.has_pin else "That password isn't right.")
    throttle_short.forget(f"{request.remote_addr}|{account}")
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


# --- your picture ----------------------------------------------------------------------------
# A profile picture is one a person chose from the library, never one the
# server guessed: the middle of that photograph as a 256 px square, kept in
# the data folder, shown on the sign-in screen (so to anyone who reaches it)
# and beside the name everywhere else.

@bp.post("/api/me/avatar")
def me_avatar():
    who = require_signed_in()
    data = body()
    try:
        asset_id = int(data.get("asset_id"))
    except (TypeError, ValueError):
        fail(400, "Choose a photograph from the library.")
    row = visible_asset(asset_id, who)
    if row["kind"] != "picture":
        fail(400, "Choose a photograph, not a video.")
    if row["visibility"] > db.VIS_FAMILY:
        # A profile picture is on the sign-in screen, for anyone who opens it.
        fail(400, "A Hidden photograph cannot be a profile picture: the sign-in screen shows it to everyone.")
    source = asset_path(row)
    if not os.path.isfile(long_path(source)):
        fail(404, "This file is not available right now.")
    try:
        square = media.profile_picture(source, row["rotation"] or 0)
    except Exception:  # noqa: BLE001 — a file Pillow cannot open
        fail(400, "This photograph cannot be opened.")
    moment = time.time()
    target = avatar_path(who.id, auth.avatar_stamp(moment))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = f"{target}.tmp"
    with open(temporary, "wb") as stream:
        stream.write(square)
    os.replace(temporary, target)
    auth.update_profile(conn(), who.id, avatar_at=moment)
    sweep_avatars(who.id, keep=target)
    return jsonify(auth.get_user(conn(), who.id).public())


@bp.delete("/api/me/avatar")
def me_avatar_remove():
    who = require_signed_in()
    drop_avatar(who.id)
    return jsonify(auth.get_user(conn(), who.id).public())


@bp.get("/api/avatar/<int:user_id>")
def avatar(user_id: int):
    """Anyone may look: the sign-in screen shows these before anyone has signed in."""
    person = auth.get_user(conn(), user_id)
    path = avatar_file(person) if person else None
    if not path or not os.path.isfile(path):
        fail(404, "No picture.")
    response = send_file(path, mimetype="image/jpeg", conditional=True,
                         etag=f"avatar-{user_id}-{auth.avatar_stamp(person.avatar_at)}",
                         max_age=86400)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
