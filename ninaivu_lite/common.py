"""What every route shares: the database, who is asking, what they may see,
and the shapes Ninaivu's screens read.

Errors on ``/api/*`` are always JSON ``{"error": "...", "status": n}``: the
screens show ``error`` as it is, and a 401 sends a signed-in person back to
the sign-in screen, so a route Lite does not have answers 404, never 401.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, NoReturn

from flask import Response, current_app, g, request
from werkzeug.exceptions import HTTPException
from werkzeug.routing import IntegerConverter

from . import auth, db, media
from .config import Config
from .dates import long_path
from .scanner import Scanner, full_path, leads_away

# --- the request's surroundings ---------------------------------------------------


def cfg() -> Config:
    return current_app.config["LITE"]


def scanner() -> Scanner:
    return current_app.config["SCANNER"]


def importer():
    return current_app.config["IMPORTER"]


def conn() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db.connect(cfg().data_dir)
    return g.db


#: An answer asked for with more than this in its parameters (a search or a
#: folder a quarter of a megabyte long, from anyone on the network) is worked
#: out but not kept: kept, 128 of them held hundreds of megabytes.
KEY_KEEP_CHARS = 4096


def small_key(params: Any) -> bool:
    """Whether *params* are small enough to be part of a remembered key."""
    return sum(len(str(p)) for p in params) <= KEY_KEEP_CHARS


def counted(sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[tuple]:
    """The rows of a read-only count over the index, worked out again only
    when the index has changed since (see :class:`db.Remembered`)."""
    def compute() -> list[tuple]:
        return [tuple(r) for r in conn().execute(sql, params)]
    if not small_key(params):
        return compute()
    return current_app.config["REMEMBERED"].get((sql, tuple(params)), compute)


#: A grid page larger than this is not kept (a 25,000-photo page is about
#: 0.7 MB): at most PAGES_KEPT of them stay in memory.
PAGE_KEEP_BYTES = 4 * 1024 * 1024
PAGES_KEPT = 6


def remembered_page(key: tuple, compute: Callable[[], str]) -> str:
    """A grid page's answer, made again only when the index has changed."""
    if not small_key(key):
        return compute()
    return current_app.config["REMEMBERED_PAGES"].get(
        key, compute, keep=lambda body: len(body) <= PAGE_KEEP_BYTES)


def user() -> auth.User:
    """The person asking: signed in, or the anonymous guest."""
    if "user" not in g:
        cookie = request.cookies.get(auth.SESSION_COOKIE)
        found = auth.session_user(conn(), cookie)
        # A device whose session was ended from the console (a PIN changed,
        # Sign out everywhere, a profile switched off) is told so, rather
        # than quietly answered as somebody "just looking" while it keeps
        # the last person's albums and photographs on screen.
        g.session_ended = bool(cookie) and found is None
        g.user = found or auth.ANONYMOUS
    return g.user


# --- errors -------------------------------------------------------------------------


class ApiError(HTTPException):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(description=message)
        self.code = status
        self.extra = extra

    def get_response(self, environ=None, scope=None) -> Response:  # noqa: ARG002
        body = {"error": self.description, "status": self.code, **self.extra}
        return Response(json.dumps(body, ensure_ascii=False), self.code,
                        mimetype="application/json")


def fail(status: int, message: str, **extra: Any) -> NoReturn:
    raise ApiError(status, message, **extra)


#: The largest id (or count) SQLite can hold; one past it was a 500 (A152).
MAX_ROW_ID = 2 ** 63 - 1


def row_id(value: Any) -> int | None:
    """*value* as a positive id the index can hold, or None."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 < number <= MAX_ROW_ID else None


class RowIdConverter(IntegerConverter):
    """``<int:...>`` in every route: a number too large for the index does
    not match (404), instead of reaching SQLite and failing there (A152)."""

    def __init__(self, map, *args: Any, **kwargs: Any) -> None:  # noqa: A002
        kwargs.setdefault("max", MAX_ROW_ID)
        super().__init__(map, *args, **kwargs)


def install_converters(state) -> None:
    """For ``Blueprint.record_once`` on the first blueprint with id routes."""
    state.app.url_map.converters["int"] = RowIdConverter


#: No JSON a screen sends is anywhere near this; a body claiming more is
#: refused before it is read into memory.
JSON_MAX_BYTES = 1024 * 1024


def json_sent() -> Any:
    """The body read as JSON, or None when it is not JSON. Never more than
    :data:`JSON_MAX_BYTES` (a 100 MB body of small objects took over 2 GB to
    read), and never nested so deep that reading it fails."""
    if (request.content_length or 0) > JSON_MAX_BYTES:
        fail(413, "That is too large.")
    try:
        return request.get_json(silent=True)
    except RecursionError:
        return None


def body() -> dict[str, Any]:
    """The JSON object sent, or {} for an empty body (logout, delete)."""
    if (request.content_length or 0) > JSON_MAX_BYTES:
        fail(413, "That is too large.")
    if not request.get_data(cache=True):
        return {}
    data = json_sent()
    if not isinstance(data, dict):
        fail(400, "Send a JSON object.")
    return data


def require_signed_in() -> auth.User:
    who = user()
    if who.anonymous:
        fail(401, "Sign in to do that.")
    return who


def require_family() -> auth.User:
    who = require_signed_in()
    if not who.family_or_more:
        fail(403, "Guests cannot do that.")
    return who


def require_admin() -> auth.User:
    who = user()
    if who.anonymous:
        fail(401, "Administrator sign-in required.")
    if not who.is_admin:
        fail(403, "Family members cannot do that.")
    return who


# --- profile pictures ------------------------------------------------------------------

AVATARS_DIR = "avatars"


def avatar_path(user_id: int, stamp: int) -> str:
    """The small square a person chose: avatars/<id>-<stamp>.jpg in the data
    folder. A new picture is a new file, never written over the old one,
    which a browser may still be reading (Windows refuses that)."""
    return os.path.join(cfg().data_dir, AVATARS_DIR, f"{user_id}-{stamp}.jpg")


def avatar_file(person: auth.User) -> str | None:
    return avatar_path(person.id, auth.avatar_stamp(person.avatar_at)) if person.avatar_at else None


def sweep_avatars(user_id: int, keep: str | None = None) -> None:
    """Remove a person's picture files, but *keep*. One still being read
    cannot go on Windows; it is tried again at the next change."""
    folder = os.path.join(cfg().data_dir, AVATARS_DIR)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(f"{user_id}-") and name.endswith(".jpg") and path != keep:
            try:
                os.unlink(path)
            except OSError:
                pass


def drop_avatar(user_id: int) -> None:
    """Forget a person's picture: the files and the record of when it was set."""
    sweep_avatars(user_id)
    auth.update_profile(conn(), user_id, avatar_at=None)


# --- folders -------------------------------------------------------------------------


def folder_ids() -> dict[str, int]:
    return {r["path"]: r["id"] for r in conn().execute(
        "SELECT id, path FROM folders WHERE detached_at IS NULL")}


def split_library(path: str | None) -> tuple[int, str] | None:
    """An absolute folder as (library folder id, path inside it), or None."""
    if not path:
        return None
    norm = os.path.normcase(os.path.normpath(path))
    for root, fid in folder_ids().items():
        base = os.path.normcase(os.path.normpath(root))
        if norm == base:
            return fid, ""
        if norm.startswith(base.rstrip(os.sep) + os.sep):
            rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root))
            return fid, rel.replace(os.sep, "/")
    return None


def subtree(alias: str, rel: str) -> tuple[str, list[Any]]:
    """SQL for "in folder *rel* or anywhere below it"."""
    if not rel:
        return "1", []
    return (f"({alias}dir = ? OR ({alias}dir > ? AND {alias}dir < ?))",
            [rel, rel + "/", rel + "/\U0010ffff"])


# --- what a person may see ------------------------------------------------------------


def visible(who: auth.User | None = None, alias: str = "a") -> tuple[str, list[Any]]:
    """SQL and parameters for "rows this person may see": not missing, within
    their visibility ceiling, and inside their assigned folder if they have one."""
    who = who or user()
    p = f"{alias}." if alias else ""
    sql = f"{p}missing = 0 AND {p}visibility <= ?"
    params: list[Any] = [who.max_visibility]
    if who.assigned_library:
        place = split_library(who.assigned_library)
        if place is None:
            return "0", []          # assigned to a folder no longer in the library
        fid, rel = place
        inside, more = subtree(p, rel)
        sql += f" AND {p}folder_id = ? AND {inside}"
        params += [fid, *more]
    return sql, params


def visible_asset(asset_id: int, who: auth.User | None = None) -> sqlite3.Row:
    """The row, if this person may see it; 404 otherwise (never 403)."""
    where, params = visible(who)
    row = conn().execute(
        f"SELECT a.*, f.path AS root FROM assets a JOIN folders f ON f.id = a.folder_id "
        f"WHERE a.id = ? AND {where}", [asset_id, *params]).fetchone()
    if row is None:
        fail(404, "Not found.")
    return row


def asset_path(row: sqlite3.Row) -> str:
    """Where the file is; a 404 when it leads, through a link, into the data
    folder or to something that is not a photograph or video (see
    :func:`scanner.leads_away`): a file can be made a link after it was indexed."""
    path = full_path(row["root"], row["dir"], row["name"])
    if leads_away(path, scanner().data_real):
        fail(404, "This file is not available right now.")
    return path


def is_favourite(asset_id: int, who: auth.User | None = None) -> bool:
    who = who or user()
    if who.anonymous:
        return False
    row = conn().execute("SELECT favorite FROM user_assets WHERE user_id = ? AND asset_id = ?",
                         (who.id, asset_id)).fetchone()
    return bool(row and row[0])


# --- shapes --------------------------------------------------------------------------------

THUMB_PX = sorted(media.THUMB_SIZES.values())          # [256, 640]
PX_TO_SIZE = {px: key for key, px in media.THUMB_SIZES.items()}


def snap_thumb(requested: str | None) -> str:
    """The configured size nearest to what was asked for ("s" or "l")."""
    try:
        px = int(requested or 0)
    except ValueError:
        px = 0
    if px <= 0:
        return PX_TO_SIZE[THUMB_PX[0]]
    return PX_TO_SIZE[min(THUMB_PX, key=lambda size: abs(size - px))]


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def playable(row: sqlite3.Row) -> bool:
    return media.browser_native(row["name"])


def turn_query(row: sqlite3.Row) -> str:
    """How a copy with the index's turn baked in is told apart from the copy
    before a turn or flip ('' for neither): browsers cache such copies."""
    if not row["rotation"] and not row["mirror"]:
        return ""
    return f"?r={row['rotation'] or 0}" + ("&m=1" if row["mirror"] else "")


def asset_public(row: sqlite3.Row, who: auth.User | None = None) -> dict[str, Any]:
    """One photo or video in the shape Ninaivu's viewer reads (``_public``)."""
    who = who or user()
    asset_id = row["id"]
    can_play = playable(row)
    served_turned = who.is_guest and row["kind"] == "picture" \
        and (row["ext"] or "").lower() not in ("gif", "bmp")
    # A guest's copy has the turn baked in and is cached for up to a day: a
    # photograph turned by hand since gets a new address, so it is fetched again.
    turn = turn_query(row) if served_turned else ""
    out: dict[str, Any] = {
        "id": asset_id,
        "name": row["name"],
        "filename": row["name"],
        "folder": row["dir"],
        "ext": row["ext"],
        "kind": row["kind"],
        "size": row["size"],
        "size_h": human_size(row["size"]),
        "date": row["date_key"] or "",
        "date_source": row["date_source"],
        "captured_at": row["captured_at"],
        "width": row["width"],
        "height": row["height"],
        "duration": row["duration"] or 0,
        "favorite": is_favourite(asset_id, who),
        "has_thumb": row["thumb"] != db.THUMB_NONE,
        "thumb_v": row["thumb_v"] or "",
        "blurhash": None,
        "color": row["color"],
        "playable": can_play,
        "needs_proxy": False,
        "src": f"/api/file/{asset_id}{turn}",
        "view": f"/api/file/{asset_id}{turn}" if can_play else f"/api/preview/{asset_id}{turn}",
        # A guest is served a re-encoded copy with the index's turn already in
        # it (api_gallery.guarded_file), so the viewer must not turn it again.
        "rotation": 0 if served_turned else (row["rotation"] or 0),
        "mirror": False if served_turned else bool(row["mirror"]),
        "rotation_source": row["rot_source"] or "none",
        "visibility": db.VIS_NAMES.get(row["visibility"], "family"),
        "visibility_source": row["vis_source"],
        "caption": None, "city": None, "country": None,
        "duplicate": False,
        "tags": [],
        "is_live": False,
        "live_src": None,
        "rating": 0,
        "nsfw": 0,
        "quality": [],
    }
    if who.family_or_more:
        # Where and how a photo was taken is for the family, not for guests.
        out.update({
            "download": f"/api/download/{asset_id}",
            "camera": row["camera"], "lens": row["lens"], "iso": row["iso"],
            "f_number": row["f_number"], "exposure": row["exposure"],
            "focal_length": row["focal_length"],
            "gps": [row["gps_lat"], row["gps_lon"]] if row["gps_lat"] is not None else None,
        })
    return out


def scan_snapshot() -> dict[str, Any]:
    """The scanner's progress in the shape Ninaivu's screens read."""
    s = scanner().snapshot()
    state = s.get("state", "idle")
    total = s.get("thumbs_total") or 0
    left = s.get("thumbs_left") or 0
    if state == "walking":
        status, percent, processed, total_n = "walking", None, s.get("found", 0), 0
    elif state in ("thumbnails", "finishing"):
        status = "indexing" if state == "thumbnails" else "finishing"
        processed = max(0, total - left)
        percent = round(processed * 100 / total) if total else 100
        total_n = total
    else:
        status = "done" if s.get("last_finished") else "idle"
        percent, processed, total_n = 100, 0, 0
    return {
        "running": state != "idle",
        "status": status,
        "percent": percent,
        "processed": processed,
        "total": total_n,
        "message": "",
        "folder": "",
        "eta": None,
        "added": s.get("added", 0),
        "removed": s.get("removed", 0),
        "tagged": 0,
        "tag_total": 0,
        "unreachable": s.get("unreachable", []),
        # Folders inside the library that could not be read on the last
        # walk; their photographs were kept as they were.
        "unreadable": s.get("unreadable", 0),
    }


#: How long an answer about a library folder being there is used for.
REACH_KEEP = 5.0
#: The longest a request waits for a library folder to answer.
REACH_WAIT = 0.5
_reach_lock = threading.Lock()
_reach: dict[str, tuple[bool, float]] = {}
_reaching: dict[str, threading.Event] = {}


def library_exists(path: str) -> bool:
    """Whether a library folder can be reached, as the gallery's status,
    the console and the folder picker show it.

    Asked on every page load, on every device. A folder on a NAS that is off
    or asleep can take half a minute to say it is not there (Windows gives up
    on a share after 20 to 40 seconds; a hard NFS mount on Linux never
    does), and every request asking waited that long. So the folder is asked
    on a thread of its own, the answer is kept for a few seconds, and a
    request waits at most :data:`REACH_WAIT`: past that it goes with the last
    answer, or "there" when there has been none (a waking disk is far more
    often there than not; the scan says so when it is not)."""
    now = time.monotonic()
    with _reach_lock:
        known = _reach.get(path)
        if known is not None and now - known[1] < REACH_KEEP:
            return known[0]
        asking = _reaching.get(path)
        if asking is None:
            asking = _reaching[path] = threading.Event()
            threading.Thread(target=_look_at, args=(path, asking), name="library-reach",
                             daemon=True).start()
    asking.wait(REACH_WAIT)
    with _reach_lock:
        found = _reach.get(path)
    if found is not None and found is not known:
        return found[0]
    return known[0] if known is not None else True


def _look_at(path: str, done: threading.Event) -> None:
    try:
        there = os.path.isdir(long_path(path))
    except (OSError, ValueError):
        there = False
    with _reach_lock:
        _reach[path] = (there, time.monotonic())
        if len(_reach) > 256:
            for stale in sorted(_reach, key=lambda p: _reach[p][1])[:128]:
                del _reach[stale]
        _reaching.pop(path, None)
    done.set()
