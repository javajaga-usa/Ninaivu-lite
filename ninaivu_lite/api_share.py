"""Share links: making, listing and ending them, and the public calls behind
the share page.

A share link is the one thing that answers somebody with no account at all,
so everything it reaches is resolved from the token, never from the caller,
and is worked out afresh on every request from what the link's CREATOR may
see today: a photograph hidden since, or a maker who has been made a guest or
switched off, shrinks what the link shows at once. No link ever shows a
Hidden photograph, even one an administrator made: Hidden means admins only. Pictures always go
out as the metadata-free viewing copy, because the person holding the link is
a stranger and a photograph's EXIF says where it was taken.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import sqlite3
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

from . import auth, db, media
from .api_gallery import (
    as_id,
    may_carry_location,
    stripped_video_response,
    original_path,
    original_response,
    own_album,
    thumb_response,
    viewing_response,
)
from .common import body, conn, fail, playable, require_family, visible, visible_asset

bp = Blueprint("api_share", __name__)

SHARE_PASSWORD_MIN = 4
UNLOCK_SECONDS = 12 * 3600
#: Ninaivu's limits on guessing a link's password: per address and link, and
#: per link from anywhere.
throttle_short = auth.Throttle(limit=8, window=300)
throttle_long = auth.Throttle(limit=20, window=1800)
#: And per link per day, as for a PIN: without it a 4-character password
#: could be tried nearly a thousand times a day.
throttle_day = auth.Throttle(limit=60, window=86400)

#: Not the file name: "Chennai_home_2021.jpg" can say where a photograph was
#: taken, which a link promises to leave out.
SHARED_FIELDS = ("id", "ext", "kind", "width", "height", "duration")


def _expired(row: sqlite3.Row) -> bool:
    return bool(row["expires_at"]) and row["expires_at"] < time.time()


def share_shape(row: sqlite3.Row) -> dict[str, Any]:
    """A link as its maker sees it. Never the password hash."""
    if row["scope"] == "album":
        found = conn().execute("SELECT name FROM albums WHERE id = ?",
                               (row["target_id"],)).fetchone()
    else:
        found = conn().execute("SELECT name FROM assets WHERE id = ?",
                               (row["target_id"],)).fetchone()
    return {
        "token": row["token"],
        "scope": row["scope"],
        "target_id": row["target_id"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "expires_at": row["expires_at"],
        "view_count": row["view_count"],
        "expired": _expired(row),
        "has_password": bool(row["password"]),
        "name": found["name"] if found else "",
        "share_url": f"/share/{row['token']}",
    }


# --- making, listing and ending links ------------------------------------------------------


@bp.post("/api/shares")
def create_share():
    who = require_family()
    data = body()
    scope = data.get("scope", "album")
    if scope not in ("album", "asset"):
        fail(400, "Share scope must be album or asset")
    raw_id = data.get("target_id", 0)
    target_id = None if isinstance(raw_id, (bool, float)) else as_id(raw_id)
    if target_id is None:
        fail(400, "Target ID must be a positive integer")

    expires_at = None
    days_raw = data.get("expires_in_days")
    if days_raw is not None:
        try:
            days = float(days_raw)
        except (TypeError, ValueError, OverflowError):
            fail(400, "Share expiry must be a finite number of days")
        if isinstance(days_raw, bool) or not math.isfinite(days) or days < 0 \
                or not math.isfinite(time.time() + days * 86400):
            fail(400, "Share expiry must be a finite, non-negative number of days")
        if days > 0:
            expires_at = time.time() + days * 86400

    password = data.get("password")
    if password is not None and not isinstance(password, str):
        fail(400, "Share password must be text")
    if password and len(password) < SHARE_PASSWORD_MIN:
        fail(400, "A share password needs at least 4 characters.")

    # A link must not reach further than its maker already sees: an album is
    # theirs to share only if they may edit it, a photograph only if visible.
    if scope == "album":
        own_album(target_id, who)
    elif visible_asset(target_id, who)["visibility"] > db.VIS_FAMILY:
        fail(400, "A Hidden photograph cannot be shared. Make it Family or Public first.")

    token = secrets.token_urlsafe(16)
    c = conn()
    with c:
        c.execute(
            """INSERT INTO shares (token, scope, target_id, created_at, created_by,
                                   expires_at, password) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token, scope, target_id, time.time(), who.id, expires_at,
             auth.hash_password(password) if password else None))
    row = c.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    return jsonify({"token": token, "share_url": f"/share/{token}", "share": share_shape(row)})


@bp.get("/api/shares")
def list_shares():
    who = require_family()
    if who.is_admin:
        rows = conn().execute("SELECT * FROM shares ORDER BY created_at DESC, id DESC")
    else:
        rows = conn().execute("SELECT * FROM shares WHERE created_by = ? "
                              "ORDER BY created_at DESC, id DESC", (who.id,))
    return jsonify({"shares": [share_shape(r) for r in rows.fetchall()]})


@bp.delete("/api/shares/<token>")
def delete_share(token: str):
    who = require_family()
    row = conn().execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    if row is None:
        fail(404, "Not found.")
    if row["created_by"] != who.id and not who.is_admin:
        fail(403, "That isn't allowed.")
    with conn():
        conn().execute("DELETE FROM shares WHERE token = ?", (token,))
    return jsonify({"ok": True})


# --- the public side ------------------------------------------------------------------------------


def share_or_404(token: str) -> sqlite3.Row:
    row = conn().execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    if row is None:
        fail(404, "Share link not found or expired")
    if _expired(row):
        fail(410, "This share link has expired")
    return row


def unlock_cookie(token: str) -> str:
    return f"ninaivu_share_{token[:16]}"


def _proof(stored: str, token: str) -> str:
    return hmac.new(stored.encode(), token.encode(), hashlib.sha256).hexdigest()


def _password_attempt(token: str, supplied: str, stored: str) -> bool | None:
    """True if right, False if wrong, None if refused because the limits are spent."""
    near, anywhere = f"{request.remote_addr}|share:{token}", f"*|share:{token}"
    if throttle_short.blocked(near) or throttle_long.blocked(anywhere) \
            or throttle_day.blocked(anywhere):
        return None
    if supplied and auth.verify_password(supplied, stored):
        throttle_short.forget(near)
        return True
    throttle_short.fail(near)
    throttle_long.fail(anywhere)
    throttle_day.fail(anywhere)
    return False


def unlocked(share: sqlite3.Row, token: str) -> bool:
    """Has this browser answered the link's password? The proof is an HMAC of
    the token keyed by the stored hash: it needs no table of its own and dies
    with the password. A script may send ``X-Share-Password`` instead."""
    stored = share["password"]
    if not stored:
        return True
    got = request.cookies.get(unlock_cookie(token), "")
    if got and hmac.compare_digest(got.encode("utf-8"), _proof(stored, token).encode("ascii")):
        return True
    supplied = request.headers.get("X-Share-Password")
    return bool(supplied) and bool(_password_attempt(token, supplied, stored))


def creator_of(share: sqlite3.Row) -> auth.User | None:
    """The link's maker as they are now, or None when nobody is left entitled
    to what it shows (deleted, switched off, or now a guest)."""
    if share["created_by"] is None:
        return None
    creator = auth.get_user(conn(), int(share["created_by"]))
    if creator is None or not creator.active or creator.is_guest:
        return None
    return creator


def share_rows(share: sqlite3.Row, only: int | None = None) -> list[sqlite3.Row]:
    """Exactly what this link may show now, and nothing else."""
    creator = creator_of(share)
    if creator is None:
        return []
    where, params = visible(creator)
    base = ("SELECT a.*, f.path AS root FROM assets a JOIN folders f ON f.id = a.folder_id "
            f"WHERE {where}")
    c = conn()
    if share["scope"] == "album":
        # Narrower than a one-photograph link: an album link shows whatever
        # the album holds, so a hidden photograph in it never goes out.
        sql = (f"{base} AND a.visibility <= ? AND a.id IN "
               "(SELECT asset_id FROM album_items WHERE album_id = ?)")
        args: list[Any] = [*params, db.VIS_FAMILY, share["target_id"]]
        if only is not None:
            sql += " AND a.id = ?"
            args.append(only)
        return c.execute(sql + " ORDER BY a.captured_at DESC, a.id DESC", args).fetchall()
    if only is not None and only != share["target_id"]:
        return []
    return c.execute(f"{base} AND a.visibility <= ? AND a.id = ?",
                     [*params, db.VIS_FAMILY, share["target_id"]]).fetchall()


def shared_item(row: sqlite3.Row, token: str) -> dict[str, Any]:
    """What a stranger is told about one photograph: enough to lay it out and
    play it — no folder, tags, place or visibility."""
    item = {key: row[key] for key in SHARED_FIELDS}
    item["duration"] = item["duration"] or 0
    can_play = playable(row)
    src = f"/api/share/{token}/file/{row['id']}"
    item.update({
        "blurhash": None,
        "color": row["color"],
        "rotation": 0,
        "has_thumb": row["thumb"] != db.THUMB_NONE,
        "playable": can_play,
        "src": src,
        "thumb": f"/api/share/{token}/thumb/{row['id']}",
        "view": src if can_play or row["kind"] != "picture"
        else f"/api/share/{token}/preview/{row['id']}",
    })
    return item


@bp.get("/api/share/<token>")
def view_share(token: str):
    share = share_or_404(token)
    if share["password"] and not unlocked(share, token):
        fail(401, "This link needs its password.", password_required=True, token=token,
             scope=share["scope"])
    with conn():
        conn().execute("UPDATE shares SET view_count = view_count + 1 WHERE id = ?",
                       (share["id"],))
    rows = share_rows(share)
    if not rows:
        fail(404, "Nothing here any more")
    if share["scope"] == "album":
        album = conn().execute("SELECT name FROM albums WHERE id = ?",
                               (share["target_id"],)).fetchone()
        return jsonify({
            "scope": "album",
            "album": {"name": album["name"] if album else "Shared photographs"},
            "total": len(rows),
            "items": [shared_item(r, token) for r in rows],
        })
    return jsonify({"scope": "asset", "item": shared_item(rows[0], token)})


@bp.post("/api/share/<token>/unlock")
def unlock_share(token: str):
    share = share_or_404(token)
    stored = share["password"]
    if not stored:
        return jsonify({"ok": True})
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail(400, "Share credentials must be a JSON object")
    supplied = data.get("password", "")
    if not isinstance(supplied, str):
        fail(400, "Share password must be text")
    answer = _password_attempt(token, supplied, stored)
    if answer is None:
        fail(429, "Too many attempts. Wait a while and try again.")
    if not answer:
        fail(401, "That password isn't right.")
    response = jsonify({"ok": True})
    response.set_cookie(unlock_cookie(token), _proof(stored, token), max_age=UNLOCK_SECONDS,
                        httponly=True, samesite="Lax", secure=request.is_secure, path="/")
    return response


def shared_asset(token: str, asset_id: int) -> sqlite3.Row:
    share = share_or_404(token)
    if not unlocked(share, token):
        fail(401, "This link needs its password.", password_required=True)
    rows = share_rows(share, only=asset_id)
    if not rows:
        fail(404, "Not found.")
    return rows[0]


def _no_referrer(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("/api/share/<token>/thumb/<int:asset_id>")
def shared_thumb(token: str, asset_id: int):
    row = shared_asset(token, asset_id)
    return _no_referrer(thumb_response(row, request.args.get("s"),
                                       cache="private, max-age=3600"))


@bp.get("/api/share/<token>/file/<int:asset_id>")
def shared_file(token: str, asset_id: int):
    row = shared_asset(token, asset_id)
    path = original_path(row)
    if may_carry_location(row) or row["rotation"]:
        # The share page is told rotation 0, so the index's turn is baked in.
        return _no_referrer(viewing_response(row, path, 3600, turned=True))
    if row["kind"] == "video":
        return _no_referrer(stripped_video_response(row, path, 3600))
    return _no_referrer(original_response(row, path))


@bp.get("/api/share/<token>/preview/<int:asset_id>")
def shared_preview(token: str, asset_id: int):
    row = shared_asset(token, asset_id)
    if row["kind"] != "picture":
        fail(404, "Not found.")
    path = original_path(row)
    if media.browser_native(row["name"]) and not may_carry_location(row) and not row["rotation"]:
        return _no_referrer(original_response(row, path))
    return _no_referrer(viewing_response(row, path, 3600, turned=True))
