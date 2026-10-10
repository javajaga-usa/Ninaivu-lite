"""The family gallery's calls: status and counts, the grid, facets and
suggestions, one photograph, its thumbnail and file, downloads, favourites,
per-item visibility, and albums.

Shapes and sentences follow Ninaivu's (see the contract): the screens taken
from Ninaivu read these fields by name, and Ninaivu's Tamil translations key
on the English sentences. Every query goes through :func:`common.visible`, so
nobody is ever shown, counted or served a photograph above their ceiling; an
item someone may not see is answered with 404, never 403.

Lite never changes a photograph: the only writes here are to the index
(favourites, visibility, albums).
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import re
import sqlite3
import threading
import time
import unicodedata
import zipfile
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from PIL import Image

from . import auth, db, media, parallel
from .common import (
    ApiError,
    asset_path,
    asset_public,
    body,
    cfg,
    conn,
    counted,
    fail,
    importer as engine,
    library_exists,
    remembered_page,
    require_admin,
    require_family,
    scan_snapshot,
    scanner,
    snap_thumb,
    subtree,
    user,
    visible,
    visible_asset,
)
from .dates import long_path

log = logging.getLogger(__name__)

bp = Blueprint("api_gallery", __name__)

KIND_CODES = {"picture": 0, "video": 1}
MAX_ID = 2 ** 63 - 1
MAX_LIMIT = 200_000
DEFAULT_LIMIT = 100_000
MAX_ZIP_FILES = 2000
MAX_IDS = 5000
IN_CHUNK = 500

#: Types a browser may be handed to show; anything else is a download.
INLINE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "image/tiff", "image/heic", "image/heif", "image/avif",
    "video/mp4", "video/quicktime", "video/x-m4v", "video/webm",
})
#: Picture formats that cannot carry where they were taken.
NO_LOCATION_EXTS = frozenset({"gif", "bmp"})

#: Videos copied without their metadata at once. ffmpeg runs inside the
#: request, for guests and links too, and the server has sixteen threads in all.
FFMPEG_SLOTS = threading.BoundedSemaphore(2)
#: How long a second request for the same video waits for the first one's copy.
STRIP_WAIT = 30.0
#: The most the copies in views/ may take; past it the oldest go first.
VIEWS_MAX_BYTES = 2 * 1024 ** 3
VIEWS_CHECK_EVERY = 60.0
_strip_locks: dict[int, threading.Lock] = {}
#: Thumbnails made while a phone waits for them, and viewing copies: a few at
#: a time (see :class:`parallel.Slots`), so they never hold every one of the
#: server's threads. A tile past the waiting room is told to ask again (the
#: grid does, once, a moment later); a photograph opened in the viewer waits.
TILES = parallel.Slots(parallel.REQUEST_WORK, parallel.REQUEST_WAITING)
VIEWS = parallel.Slots(parallel.REQUEST_WORK, parallel.SERVER_THREADS // 2, wait_for=60.0)
#: Seconds a tile turned away is asked for again after (said in Retry-After).
TILE_RETRY = 2
_views_lock = threading.Lock()
_views_checked_at = 0.0

for _ext, _mime in ((".heic", "image/heic"), (".heif", "image/heif"), (".avif", "image/avif"),
                    (".webp", "image/webp"), (".m4v", "video/x-m4v"), (".mov", "video/quicktime"),
                    (".webm", "video/webm"), (".jfif", "image/jpeg")):
    mimetypes.add_type(_mime, _ext)

SORTS = {
    "date_desc": "a.captured_at DESC, a.id DESC",
    "date_asc": "a.captured_at ASC, a.id ASC",
    "name_asc": "a.name COLLATE NOCASE ASC, a.id ASC",
    "name_desc": "a.name COLLATE NOCASE DESC, a.id DESC",
    "size_desc": "a.size DESC, a.id DESC",
    "random": "random()",
}

YEAR_RE = re.compile(r"^\d{4}(-\d{2})?$")
#: A date_key's month, as /api/months lists them.
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


# --- small parsers ------------------------------------------------------------------


def as_id(value: Any) -> int | None:
    """*value* as a positive item id, or None (strings of digits are taken)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 < value <= MAX_ID else None
    text = str(value).strip()
    if not text or len(text) > 19 or not text.isascii() or not text.isdigit():
        return None
    number = int(text)
    return number if 0 < number <= MAX_ID else None


def id_list(data: dict[str, Any], limit: int = MAX_IDS) -> list[int]:
    """The ``ids`` of a request body, read loosely (non-ids skipped), or a 400."""
    values = data.get("ids")
    if values is None:
        return []
    if not isinstance(values, list):
        fail(400, "Send the item ids as a list.")
    out: dict[int, None] = {}
    for value in values:
        number = as_id(value)
        if number is not None:
            out[number] = None
    return list(out)[:limit]


def int_arg(name: str, default: int = 0) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def bool_arg(name: str, default: bool = True) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def visible_ids(ids: Sequence[int], who: auth.User | None = None) -> list[int]:
    """The ids, in the order given, that this person may see."""
    where, params = visible(who)
    found: set[int] = set()
    for start in range(0, len(ids), IN_CHUNK):
        piece = list(ids[start:start + IN_CHUNK])
        marks = ",".join("?" * len(piece))
        found.update(r[0] for r in conn().execute(
            f"SELECT a.id FROM assets a WHERE a.id IN ({marks}) AND {where}",
            [*piece, *params]))
    return [i for i in ids if i in found]


def favourite_ids(who: auth.User) -> set[int]:
    if who.anonymous:
        return set()
    return {r[0] for r in conn().execute(
        "SELECT asset_id FROM user_assets WHERE user_id = ? AND favorite = 1", (who.id,))}


def swatch(color: str | None) -> str:
    if color and len(color) == 7 and color.startswith("#"):
        return color[1] + color[3] + color[5]
    return ""


# --- status ---------------------------------------------------------------------------


def root_label(who: auth.User) -> str:
    folders = cfg().folders
    if who.is_admin:
        if len(folders) > 1:
            return f"{len(folders)} library folders"
        return cfg().active_folder
    if not folders:
        return ""
    if who.assigned_library:
        return Path(who.assigned_library).name or who.assigned_library
    if len(folders) > 1:
        return "Your library"
    return Path(folders[0]).name or "Library"


#: library_stats' columns, in the order its query reads them.
STATS_COLUMNS = ("count", "bytes", "favorites", "pictures", "videos", "hidden", "public",
                 "first_date", "last_date")


def library_stats(who: auth.User) -> dict[str, Any]:
    where, params = visible(who)
    row = counted(
        f"""SELECT COUNT(*) AS count, COALESCE(SUM(a.size), 0) AS bytes,
                   COALESCE(SUM(ua.favorite = 1), 0) AS favorites,
                   COALESCE(SUM(a.kind = 'picture'), 0) AS pictures,
                   COALESCE(SUM(a.kind = 'video'), 0) AS videos,
                   COALESCE(SUM(a.visibility = 2), 0) AS hidden,
                   COALESCE(SUM(a.visibility = 0), 0) AS public,
                   MIN(NULLIF(a.date_key, '')) AS first_date,
                   MAX(NULLIF(a.date_key, '')) AS last_date
            FROM assets a
            LEFT JOIN user_assets ua ON ua.asset_id = a.id AND ua.user_id = ?
            WHERE {where}""", [who.id, *params])[0]
    out = dict(zip(STATS_COLUMNS, row, strict=True))
    out["first_date"] = out["first_date"] or ""
    out["last_date"] = out["last_date"] or ""
    out.update(live=0, audio=0, duplicate_groups=0, nsfw=0, embedded=0)
    return out


IDLE_SCAN = {"status": "idle", "running": False, "percent": 100}


@bp.get("/api/status")
def status():
    who = user()
    c = cfg()
    away = [path for path in c.folders if not library_exists(path)]
    payload: dict[str, Any] = {
        "has_library": bool(c.folders),
        "root": c.active_folder if who.is_admin else None,
        "root_label": root_label(who),
        "roots": list(c.folders) if who.is_admin else [],
        "libraries_away": len(away),
        "libraries_away_paths": away if who.is_admin else [],
        "restricted": False,
        "user": who.public(),
        "scan": scan_snapshot() if who.is_admin else dict(IDLE_SCAN),
        "ai": {"engine": "off"},
        # The console is this same port, at /admin.
        "admin_port": None,
        "home_port": c.port,
        "scheme": request.scheme,
        "hostnames": {},
        "thumb_sizes": sorted(media.THUMB_SIZES.values()),
        "capabilities": {"ffmpeg": bool(media.FFMPEG), "heif": media.HEIF, "watch": c.watch},
    }
    if c.folders and bool_arg("stats", True):
        payload["stats"] = library_stats(who)
    return jsonify(payload)


@bp.get("/api/status/stats")
def status_stats():
    stats = library_stats(user()) if cfg().folders else None
    response = jsonify({"stats": stats})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/status/scan")
def status_scan():
    if not user().is_admin:
        return jsonify(IDLE_SCAN)
    return jsonify(scan_snapshot())


def _indexing_job(scan: dict[str, Any]) -> dict[str, Any]:
    total, done = scan.get("total") or 0, scan.get("processed") or 0
    if scan["status"] == "walking":
        detail = f"{done:,}"
    elif total:
        detail = f"{done:,} / {total:,}"
    else:
        detail = ""
    return {
        "id": "indexing",
        "title": "Indexing",
        "detail": detail,
        "percent": scan.get("percent"),
        "eta": scan.get("eta"),
        "paused": False,
        "uses": ["disk", "cpu"] if scan["status"] in ("indexing", "finishing") else ["disk"],
        "page": "library",
    }


def _import_job() -> dict[str, Any] | None:
    """The importer's run, for the strip on every console page."""
    job = engine().progress()
    if not job["is_scanning"]:
        return None
    total, done = job["total_files"] or 0, job["processed"] or 0
    counting = job["phase"] == "counting"
    return {
        "id": "import",
        "title": {"dry-run": "Dry run", "verify": "Auditing the archive"}.get(
            job["job_mode"], "Consolidating"),
        "detail": f"{total:,}" if counting else f"{done:,} / {total:,}",
        "percent": None if counting or not total else min(100, round(done * 100 / total)),
        "eta": job["eta_seconds"],
        "paused": bool(job["is_paused"]),
        "uses": ["disk"],
        "page": "archive",
    }


@bp.get("/api/status/activity")
def status_activity():
    if not user().is_admin:
        return jsonify({"jobs": [], "running": False, "scan": dict(IDLE_SCAN)})
    scan = scan_snapshot()
    jobs = [job for job in (_import_job(), _indexing_job(scan) if scan["running"] else None)
            if job]
    response = jsonify({
        "running": bool(jobs),
        "jobs": jobs,
        "uses": sorted({use for job in jobs for use in job["uses"]}),
        "scan": scan,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# --- the grid -------------------------------------------------------------------------------


def grid_filters(who: auth.User) -> tuple[str, list[Any]]:
    """WHERE and parameters for /api/segments' query string."""
    where, params = visible(who)
    clauses, args = [where], list(params)
    query = request.args

    # Every word must match somewhere: the file name, its folder or (for the
    # family: how a photo was taken is not for guests) the camera; a bare
    # year ("2019") or month ("2019-05") also matches the date.
    for word in (query.get("q") or "").split():
        pattern = like(word)
        part = "(a.name LIKE ? ESCAPE '\\' OR a.dir LIKE ? ESCAPE '\\'"
        part_args: list[Any] = [pattern, pattern]
        if who.family_or_more:
            part += " OR a.camera LIKE ? ESCAPE '\\'"
            part_args.append(pattern)
        if YEAR_RE.match(word):
            part += " OR a.date_key LIKE ?"
            part_args.append(word + "-%")
        clauses.append(part + ")")
        args += part_args

    kinds = [k for k in query.getlist("kind") if k in KIND_CODES]
    if kinds:
        clauses.append(f"a.kind IN ({','.join('?' * len(kinds))})")
        args += kinds

    if bool_arg("favorites", False):
        clauses.append("a.id IN (SELECT asset_id FROM user_assets "
                       "WHERE user_id = ? AND favorite = 1)")
        args.append(who.id)

    # Only within what this person may see anyway; above it, ignored.
    level = db.VIS_VALUES.get((query.get("visibility") or "").lower())
    if level is not None and level <= who.max_visibility:
        clauses.append("a.visibility = ?")
        args.append(level)

    folder = (query.get("folder") or "").replace("\\", "/").strip("/")
    if folder:
        inside, more = subtree("a.", folder)
        clauses.append(inside)
        args += more

    if query.get("from"):
        clauses.append("a.date_key >= ?")
        args.append(query["from"])
    if query.get("to"):
        clauses.append("a.date_key <= ?")
        args.append(query["to"])

    album = as_id(query.get("album"))
    if album is not None and who.family_or_more:
        clauses.append("a.id IN (SELECT asset_id FROM album_items WHERE album_id = ?)")
        args.append(album)

    if query.get("camera") and who.family_or_more:
        clauses.append("a.camera = ?")
        args.append(query["camera"])

    return " AND ".join(clauses), args


@bp.get("/api/segments")
def segments():
    who = user()
    where, args = grid_filters(who)
    sort = request.args.get("sort") or "date_desc"
    if sort not in SORTS:
        sort = "date_desc"
    limit = max(1, min(int_arg("limit", DEFAULT_LIMIT), MAX_LIMIT))
    offset = max(0, int_arg("offset"))
    pageable = sort != "random"
    if not pageable:
        # A random order cannot be continued: one piece, as large as it was.
        offset, limit = 0, max(limit, DEFAULT_LIMIT)

    if not pageable:
        return jsonify(grid_page(who, where, args, sort, limit, offset))
    # The same page for the same person is the same answer until the index
    # changes: a gallery opened again (or on another of their devices) is
    # sent the last one instead of reading 25,000 rows again.
    body = remembered_page(
        (who.id, where, tuple(args), sort, limit, offset),
        lambda: current_app.json.dumps(grid_page(who, where, args, sort, limit, offset),
                                       separators=(",", ":")))
    return current_app.response_class(body, mimetype="application/json")


def grid_page(who: auth.User, where: str, args: list[Any], sort: str, limit: int,
              offset: int) -> dict[str, Any]:
    pageable = sort != "random"
    c = conn()
    # Plain tuples, and no join: the columns read here are all in the
    # assets_grid index, so the date-ordered page never touches the table.
    cur = c.cursor()
    cur.row_factory = None
    total = cur.execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", args).fetchone()[0]
    rows = cur.execute(
        f"""SELECT a.id, a.width, a.height, a.kind, a.visibility, a.duration, a.thumb,
                   a.thumb_v, a.color, a.date_key
            FROM assets a WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?""",
        [*args, limit, offset]).fetchall()
    favs = favourite_ids(who) if rows else set()

    out: list[dict[str, Any]] = []
    current_key: str | None = None
    items: list[list[Any]] = []
    for aid, width, height, kind, vis, duration, thumb, thumb_v, color, day in rows:
        aspect = round(width / height * 100) if width and height else 100
        aspect = 30 if aspect < 30 else 400 if aspect > 400 else aspect
        has_thumb = thumb != db.THUMB_NONE
        flags = ((1 if aid in favs else 0) | (2 if has_thumb else 0)
                 | (64 if vis == db.VIS_PUBLIC else 0) | (128 if vis == db.VIS_HIDDEN else 0))
        key = day or "unknown"
        if key != current_key:
            items = []
            out.append({"key": key, "items": items})
            current_key = key
        items.append([aid, aspect, KIND_CODES.get(kind, 0), flags, int(duration or 0),
                      thumb_v or 0, swatch(color) if has_thumb else ""])

    reached = offset + len(rows)
    return {
        "total": total,
        "offset": offset,
        "returned": len(rows),
        "next_offset": reached if pageable and rows and reached < total else None,
        "truncated": total > reached,
        "semantic": False,
        "segments": out,
        "thumb_sizes": sorted(media.THUMB_SIZES.values()),
    }


# --- facets and suggestions --------------------------------------------------------------------


def _folder_counts(where: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    return [{"name": r[0], "count": r[1]} for r in counted(
        f"""SELECT a.dir, COUNT(*) AS n FROM assets a WHERE {where} AND a.dir != ''
            GROUP BY a.dir ORDER BY n DESC, a.dir LIMIT ?""", [*params, limit])]


def _camera_counts(where: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    if not user().family_or_more:
        return []     # as for one photo's details: cameras are for the family
    return [{"name": r[0], "count": r[1]} for r in counted(
        f"""SELECT a.camera, COUNT(*) AS n FROM assets a
            WHERE {where} AND a.camera IS NOT NULL AND a.camera != ''
            GROUP BY a.camera ORDER BY n DESC, a.camera LIMIT ?""", [*params, limit])]


@bp.get("/api/facets")
def facets():
    where, params = visible()
    years = [{"year": r[0], "count": r[1]} for r in counted(
        f"""SELECT substr(a.date_key, 1, 4) AS y, COUNT(*) FROM assets a
            WHERE {where} AND a.date_key != '' GROUP BY y ORDER BY y DESC""", params)]
    return jsonify({
        "years": years,
        "folders": _folder_counts(where, params, 40),
        "tags": [],
        "cameras": _camera_counts(where, params, 200),
    })


@bp.get("/api/months")
def months():
    """The months the grid as filtered right now has photographs in, newest
    first, with the undated counted apart: what the "Jump to" button offers.
    The same query string as /api/segments, and remembered until the index
    changes, so opening the list again costs nothing."""
    where, args = grid_filters(user())
    rows = counted(
        f"""SELECT substr(a.date_key, 1, 7) AS m, COUNT(*) FROM assets a
            WHERE {where} GROUP BY m ORDER BY m DESC""", args)
    found = [{"month": m, "count": n} for m, n in rows if m and MONTH_RE.match(m)]
    undated = sum(n for m, n in rows if not (m and MONTH_RE.match(m)))
    return jsonify({"months": found, "undated": undated})


@bp.get("/api/suggest")
def suggest():
    text = (request.args.get("q") or "").strip().lower()
    where, params = visible()
    out = []
    for kind, values in (("folder", _folder_counts(where, params, 200)),
                         ("camera", _camera_counts(where, params, 200))):
        for item in values:
            if not text or text in item["name"].lower():
                out.append({"type": kind, "value": item["name"], "count": item["count"]})
    out.sort(key=lambda s: (-s["count"], s["value"]))
    return jsonify({"suggestions": out[:20], "concepts": []})


# --- one item ------------------------------------------------------------------------------------


@bp.get("/api/asset/<int:asset_id>")
def asset_get(asset_id: int):
    return jsonify(asset_public(visible_asset(asset_id)))


def set_favourite(who: auth.User, ids: Sequence[int], favourite: bool) -> None:
    c = conn()
    with c:
        c.executemany(
            """INSERT INTO user_assets (user_id, asset_id, favorite) VALUES (?, ?, ?)
               ON CONFLICT (user_id, asset_id) DO UPDATE SET favorite = excluded.favorite""",
            [(who.id, i, int(favourite)) for i in ids])


@bp.post("/api/asset/<int:asset_id>")
def asset_update(asset_id: int):
    who = require_family()
    data = body()
    row = visible_asset(asset_id)
    if "favorite" in data:
        set_favourite(who, [asset_id], bool(data["favorite"]))
    return jsonify(asset_public(row, who))


@bp.post("/api/assets/bulk")
def assets_bulk():
    who = require_family()
    data = body()
    ids = id_list(data)
    if "favorite" not in data:
        return jsonify({"updated": 0, "skipped": len(ids)})
    seen = visible_ids(ids, who)
    if seen:
        set_favourite(who, seen, bool(data["favorite"]))
    return jsonify({"updated": len(seen), "skipped": len(ids) - len(seen)})


POSTER_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
POSTER_MAX_BYTES = 4 * 1024 * 1024


@bp.post("/api/asset/<int:asset_id>/poster")
def asset_poster(asset_id: int):
    """A video's preview picture, made by a family member's browser.

    Without ffmpeg the server cannot open a video, but the browser that plays
    it can: the gallery draws one frame and sends it here, and from then on
    everyone sees it on the tile. Only for a video that has no picture yet;
    one that has keeps it. The video itself is never touched.
    """
    require_family()
    row = visible_asset(asset_id)
    if row["kind"] != "video":
        fail(400, "Only a video takes a preview picture.")
    if row["thumb"] == db.THUMB_OK:
        return jsonify(asset_public(row))
    fmt = POSTER_TYPES.get(request.mimetype)
    if not fmt:
        fail(400, "Send the picture as JPEG, PNG or WebP.")
    if request.content_length and request.content_length > POSTER_MAX_BYTES:
        fail(413, "That is too large.")
    payload = request.stream.read(POSTER_MAX_BYTES + 1)
    if len(payload) > POSTER_MAX_BYTES:
        fail(413, "That is too large.")
    try:
        with Image.open(io.BytesIO(payload)) as sent:
            if sent.format != fmt or sent.width * sent.height > 4_000_000 \
                    or sent.width < 16 or sent.height < 16:
                raise ValueError()
            frame = sent.convert("RGB")
    except (ValueError, OSError, Image.DecompressionBombError):
        fail(400, "That is not a picture this can use.")
    colour = media.save_thumbnails(frame, scanner().thumbs_dir, asset_id)
    version = int(time.time() * 1000) % 2_000_000_000
    c = conn()
    with c:
        c.execute("UPDATE assets SET thumb = ?, large = 1, color = ?, thumb_v = ? WHERE id = ?",
                  (db.THUMB_OK, colour, version, asset_id))
    scanner().generation += 1
    return jsonify(asset_public(visible_asset(asset_id))), 201


@bp.post("/api/asset/<int:asset_id>/rotate")
def asset_rotate(asset_id: int):
    """Turn a photograph by hand: the index's answer, never the file's. For
    the one the scan could not judge, or judged wrong."""
    require_admin()
    row = visible_asset(asset_id)
    if row["kind"] != "picture":
        fail(400, "Only photographs can be turned.")
    data = body()
    try:
        rotation = int(data.get("rotation", (row["rotation"] + 90) % 360))
    except (TypeError, ValueError):
        fail(400, "Rotation must be 0, 90, 180 or 270.")
    if rotation % 360 not in media.ROTATIONS:
        fail(400, "Rotation must be 0, 90, 180 or 270.")
    scanner().set_rotation(conn(), asset_id, rotation % 360, "manual")
    return jsonify(asset_public(visible_asset(asset_id)))


@bp.post("/api/visibility")
def set_visibility():
    """Mark chosen items public, family-only or hidden, remembering what they
    were so the console's undo strip can put them back."""
    who = require_admin()
    data = body()
    level_name = str(data.get("visibility", "")).lower()
    if level_name not in db.VIS_VALUES:
        fail(400, "Visibility must be public, family or hidden.")
    level = db.VIS_VALUES[level_name]
    ids = visible_ids(id_list(data), who)
    if not ids:
        return jsonify({"updated": 0, "visibility": level_name})
    c = conn()
    with c:
        exposed = 0
        for start in range(0, len(ids), IN_CHUNK):
            piece = ids[start:start + IN_CHUNK]
            exposed += c.execute(
                f"SELECT COUNT(*) FROM assets WHERE id IN ({','.join('?' * len(piece))}) "
                f"AND visibility > ?", [*piece, level]).fetchone()[0]
        batch = c.execute(
            """INSERT INTO visibility_batches (created_at, created_by, scope, visibility,
                                               affected, exposed)
               VALUES (?, ?, 'items', ?, ?, ?)""",
            (time.time(), who.id, level, len(ids), exposed)).lastrowid
        for start in range(0, len(ids), IN_CHUNK):
            piece = ids[start:start + IN_CHUNK]
            marks = ",".join("?" * len(piece))
            c.execute(
                f"""INSERT INTO visibility_undo (batch_id, asset_id, visibility, vis_source)
                    SELECT ?, id, visibility, vis_source FROM assets WHERE id IN ({marks})""",
                [batch, *piece])
            c.execute(f"UPDATE assets SET visibility = ?, vis_source = 'item' "
                      f"WHERE id IN ({marks})", [level, *piece])
    scanner().generation += 1
    return jsonify({"updated": len(ids), "visibility": level_name, "batch_id": batch})


# --- thumbnails and files ------------------------------------------------------------------------


def thumb_response(row: sqlite3.Row, requested: str | None, *, cache: str) -> Response:
    """The thumbnail for *row* at the configured size nearest *requested*,
    made now if it is still missing; 404 when there is none."""
    if row["thumb"] == db.THUMB_NONE:
        fail(404, "This item has no thumbnail.")
    size = snap_thumb(requested)
    thumbs = scanner().thumbs_dir
    # A file on disk is served only when the index says it is this row's:
    # ids are handed out again, and a removed photograph's thumbnail (a
    # Hidden one, say) stays behind until the new one is made over it.
    try:
        made = scanner().thumbnail_now(conn(), row["id"], size, slots=TILES)
        if not made and size != "s":
            size = "s"
            made = scanner().thumbnail_now(conn(), row["id"], size, slots=TILES)
    except parallel.Busy:
        response = ApiError(503, "Busy making other thumbnails. Try again in a moment.") \
            .get_response()
        response.headers["Retry-After"] = str(TILE_RETRY)
        return response
    path = media.thumb_path(thumbs, row["id"], size)
    if not made or not path.is_file():
        fail(404, "This item has no thumbnail.")
    stamp = path.stat().st_mtime_ns
    response = send_file(path, mimetype="image/webp", conditional=True,
                         etag=f"{stamp}-{path.stem}", max_age=3600)
    response.headers["Cache-Control"] = cache
    return response


@bp.get("/api/thumb/<int:asset_id>")
def thumb(asset_id: int):
    return thumb_response(visible_asset(asset_id), request.args.get("s"),
                          cache="private, max-age=31536000, immutable")


def may_carry_location(row: sqlite3.Row) -> bool:
    return row["kind"] == "picture" and (row["ext"] or "").lower() not in NO_LOCATION_EXTS


def original_path(row: sqlite3.Row) -> str:
    path = asset_path(row)
    if not os.path.isfile(long_path(path)):
        fail(404, "This file is not available right now.")
    return path


def views_dir() -> Path:
    return Path(cfg().data_dir) / "views"


def trim_views(keep: Path) -> None:
    """Keep views/ under :data:`VIEWS_MAX_BYTES`, oldest copies out first
    (never *keep*, the one just made). Looked at once a minute at most."""
    global _views_checked_at
    with _views_lock:
        now = time.monotonic()
        if _views_checked_at and now - _views_checked_at < VIEWS_CHECK_EVERY:
            return
        _views_checked_at = now
    found, total = [], 0
    for item in views_dir().glob("*/*"):
        try:
            st = item.stat()
        except OSError:
            continue
        found.append((st.st_mtime, st.st_size, item))
        total += st.st_size
    if total <= VIEWS_MAX_BYTES:
        return
    for _mtime, size, item in sorted(found):
        if total <= VIEWS_MAX_BYTES * 0.8:
            break
        if item == keep:
            continue
        try:
            item.unlink()
        except OSError:      # being sent right now (Windows): next time
            continue
        total -= size


def viewing_response(row: sqlite3.Row, path: str, max_age: int, turned: bool = False) -> Response:
    """The picture re-encoded for viewing: upright, at most 2560 px, and with
    no metadata at all. Kept on disk in the data folder, keyed by the file's
    size and time, so a second look costs nothing. *turned* bakes in the
    index's own quarter turn (faces, or a hand): for a copy that leaves the
    viewer, which otherwise turns the picture itself as it shows it."""
    try:
        st = os.stat(long_path(path))
    except OSError:
        fail(404, "This file is not available right now.")
    rotation = (row["rotation"] or 0) if turned else 0
    cache = views_dir() / f"{row['id'] % 256:02x}" / \
        f"{row['id']}-{st.st_size}-{int(st.st_mtime)}{f'-t{rotation}' if rotation else ''}.jpg"
    if not cache.is_file():
        try:
            # A full photograph decoded and shrunk: a core's worth, so taken
            # in turn with the thumbnails being made for others.
            with VIEWS.slot():
                data = media.viewing_copy(path, rotation=rotation)
        except parallel.Busy:
            fail(503, "Busy preparing other photographs. Try again in a moment.")
        except Exception as exc:  # noqa: BLE001 — damaged or unsupported: say so
            log.debug("no viewing copy for %s: %s", path, exc)
            fail(415, "This photograph could not be converted for the browser.")
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            for old in cache.parent.glob(f"{row['id']}-*.jpg"):
                old.unlink(missing_ok=True)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}-{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, cache)
            trim_views(cache)
        except OSError:
            response = Response(data, mimetype="image/jpeg")
            response.headers["Cache-Control"] = f"private, max-age={max_age}"
            return response
    response = send_file(cache, mimetype="image/jpeg", conditional=True,
                         etag=cache.stem, max_age=max_age)
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def original_response(row: sqlite3.Row, path: str, max_age: int = 3600) -> Response:
    """The file as it is, with Range for seeking; inline only for types a
    browser shows, otherwise a download."""
    mime, _ = mimetypes.guess_type(row["name"])
    inline = mime in INLINE_TYPES
    response = send_file(long_path(path), mimetype=mime if inline else "application/octet-stream",
                         conditional=True, max_age=max_age, etag=True)
    if not inline:
        response.headers["Content-Disposition"] = attachment_header(row["name"])
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def stripped_video_response(row: sqlite3.Row, path: str, max_age: int) -> Response:
    """A video with its metadata removed (a phone writes where it was shot
    into the file), kept in the data folder like the viewing copies. Without
    ffmpeg, or for a file it cannot remux, the video is refused: the person
    asking is a guest or a stranger with a link, and the original may say
    where it was shot. Only an administrator's choice (``video_originals``)
    sends such a video as it is, marked so in a header."""
    try:
        st = os.stat(long_path(path))
    except OSError:
        fail(404, "This file is not available right now.")
    ext = os.path.splitext(row["name"])[1].lower() or ".mp4"
    cache = views_dir() / f"{row['id'] % 256:02x}" / f"{row['id']}-{st.st_size}-{int(st.st_mtime)}{ext}"
    if not cache.is_file():
        # One copy of a video at a time (a player asks several times over),
        # and only a few videos at once, so guests cannot use up the threads.
        lock = _strip_locks.setdefault(row["id"], threading.Lock())
        if not lock.acquire(timeout=STRIP_WAIT):
            fail(503, "Videos are being prepared for others. Try again in a moment.")
        try:
            if not cache.is_file():
                if not FFMPEG_SLOTS.acquire(blocking=False):
                    fail(503, "Videos are being prepared for others. Try again in a moment.")
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    for old in cache.parent.glob(f"{row['id']}-*{ext}"):
                        try:
                            old.unlink(missing_ok=True)
                        except OSError:      # still being sent (Windows)
                            pass
                    stripped = media.strip_video(path, str(cache))
                finally:
                    FFMPEG_SLOTS.release()
                if not stripped:
                    if not cfg().video_originals:
                        fail(415, "This video cannot be shared without its location data.")
                    response = original_response(row, path, max_age)
                    response.headers["X-Ninaivu-Metadata"] = "original"
                    return response
                trim_views(cache)
        finally:
            lock.release()
    mime, _ = mimetypes.guess_type(row["name"])
    response = send_file(cache, mimetype=mime or "video/mp4", conditional=True,
                         etag=cache.stem, max_age=max_age)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def guarded_file(row: sqlite3.Row, who: auth.User) -> Response:
    path = original_path(row)
    if who.is_guest and may_carry_location(row):
        # A guest gets the gallery's view of a photograph, never its EXIF:
        # the original's bytes say where it was taken.
        return viewing_response(row, path, 3600, turned=True)
    if who.is_guest and row["kind"] == "video":
        return stripped_video_response(row, path, 3600)
    return original_response(row, path)


@bp.get("/api/file/<int:asset_id>")
def file(asset_id: int):
    return guarded_file(visible_asset(asset_id), user())


@bp.get("/api/preview/<int:asset_id>")
def preview(asset_id: int):
    row = visible_asset(asset_id)
    if row["kind"] != "picture":
        fail(404, "Not found.")
    if media.browser_native(row["name"]):
        return guarded_file(row, user())
    # A guest is told rotation 0 (asset_public), so the turn is baked in here.
    return viewing_response(row, original_path(row), 86400, turned=user().is_guest)


def attachment_header(name: str, fallback: str = "photo") -> str:
    """``Content-Disposition: attachment`` naming *name*, whatever script it
    is in: ``filename*`` (RFC 5987) carries it, ``filename`` an ASCII stand-in."""
    name = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in name)
    try:
        name.encode("ascii")
        simple = name
    except UnicodeEncodeError:
        simple = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        stem, dot, ext = simple.rpartition(".")
        if not (stem if dot else simple).strip(" ._-"):
            simple = f"{fallback}.{ext}" if dot and ext else fallback
    simple = simple.replace("\\", "_").replace('"', "'")
    value = f'attachment; filename="{simple}"'
    if simple != name:
        value += "; filename*=UTF-8''" + quote(name, safe="")
    return value


@bp.get("/api/download/<int:asset_id>")
def download(asset_id: int):
    require_family()
    row = visible_asset(asset_id)
    path = original_path(row)
    mime, _ = mimetypes.guess_type(row["name"])
    response = send_file(long_path(path), mimetype=mime or "application/octet-stream",
                         conditional=True, max_age=0, etag=True)
    response.headers["Content-Disposition"] = attachment_header(row["name"])
    response.headers["Cache-Control"] = "private, no-cache"
    return response


class _Pipe(io.RawIOBase):
    """A write-only, unseekable stream whose bytes are taken as they come."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[bytes] = []
        self._pos = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, data) -> int:  # type: ignore[override]
        chunk = bytes(data)
        self._chunks.append(chunk)
        self._pos += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self._pos

    def take(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def unique_name(taken: set[str], name: str) -> str:
    """*name*, or "name (2).ext" and so on when the archive already has it."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    candidate, n = name, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
    taken.add(candidate.lower())
    return candidate


def zip_stream(entries: list[tuple[str, str]], missing: list[str],
               taken: set[str]) -> Iterator[bytes]:
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, path in entries:
            try:
                handle = open(long_path(path), "rb")  # noqa: SIM115
            except OSError:
                missing.append(name)
                continue
            with handle:
                st = os.fstat(handle.fileno())
                when = datetime.fromtimestamp(max(st.st_mtime, 315576000)).timetuple()[:6]
                info = zipfile.ZipInfo(name, date_time=when)
                info.compress_type = zipfile.ZIP_STORED
                try:
                    with archive.open(info, "w", force_zip64=True) as out:
                        while chunk := handle.read(1 << 20):
                            out.write(chunk)
                            yield pipe.take()
                except OSError as exc:
                    log.warning("zip: could not read %s: %s", path, exc)
                    missing.append(name)
            yield pipe.take()
        if missing:
            note = ("These files could not be read when the download was made, "
                    "so they are not in it:\n\n" + "\n".join(missing) + "\n")
            archive.writestr(unique_name(taken, "NOT INCLUDED.txt"), note.encode("utf-8"))
    yield pipe.take()


@bp.get("/api/download/zip")
def download_zip():
    require_family()
    wanted: dict[int, None] = {}
    for part in (request.args.get("ids") or "").split(","):
        number = as_id(part)
        if number is not None:
            wanted[number] = None
    ids = list(wanted)[:MAX_ZIP_FILES]
    if not ids:
        fail(400, "Choose some photographs first.")
    seen = visible_ids(ids)
    if not seen:
        fail(404, "None of those are yours to download.")
    rows = {}
    for start in range(0, len(seen), IN_CHUNK):
        piece = seen[start:start + IN_CHUNK]
        for row in conn().execute(
                f"SELECT a.id, a.dir, a.name, f.path AS root FROM assets a "
                f"JOIN folders f ON f.id = a.folder_id WHERE a.id IN "
                f"({','.join('?' * len(piece))})", piece):
            rows[row["id"]] = row
    # Every path is settled before the first byte goes out: after that the
    # status is sent, and a problem can only be written into the archive.
    taken: set[str] = set()
    entries: list[tuple[str, str]] = []
    missing: list[str] = []
    for asset_id in seen:
        row = rows[asset_id]
        path = asset_path(row)
        name = unique_name(taken, row["name"])
        if os.path.isfile(long_path(path)):
            entries.append((name, path))
        else:
            missing.append(name)
    if not entries:
        fail(404, "None of those files can be read right now.")
    if len(seen) == 1:
        filename = f"{entries[0][0].rpartition('.')[0] or entries[0][0]}.zip"
    else:
        filename = f"ninaivu-{date.today().isoformat()}.zip"
    response = Response(zip_stream(entries, missing, taken), mimetype="application/zip",
                        direct_passthrough=True)
    response.headers["Content-Disposition"] = attachment_header(filename, "ninaivu")
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-store"
    return response


# --- albums ------------------------------------------------------------------------------------------


def album_ids_arg(data: dict[str, Any]) -> list[int]:
    values = data.get("ids", [])
    if not isinstance(values, list):
        fail(400, "Asset IDs must be a list")
    if len(values) > MAX_IDS:
        fail(400, "Send at most 5000 asset IDs at a time")
    out: dict[int, None] = {}
    for value in values:
        if isinstance(value, (bool, float)) or not isinstance(value, (int, str)):
            fail(400, "Asset IDs must be integers")
        number = as_id(value)
        if number is None:
            fail(400, "Asset ID is out of range")
        out[number] = None
    return list(out)


def album_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail(400, "Album settings must be a JSON object")
    return data


def own_album(album_id: int, who: auth.User) -> sqlite3.Row:
    """404 for an album that is not there; 403 for one that is not theirs.
    Albums without a recorded owner stay editable by any family member."""
    row = conn().execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    if row is None:
        fail(404, "Not found.")
    if row["created_by"] is not None and row["created_by"] != who.id and not who.is_admin:
        fail(403, "That isn't allowed.")
    return row


def album_item_ids(album_id: int, who: auth.User) -> list[int]:
    """The album's items this person may see, most recently added first."""
    where, params = visible(who)
    return [r[0] for r in conn().execute(
        f"""SELECT a.id FROM album_items ai JOIN assets a ON a.id = ai.asset_id
            WHERE ai.album_id = ? AND {where} ORDER BY ai.added_at DESC, ai.rowid DESC""",
        [album_id, *params])]


def album_shape(row: sqlite3.Row, who: auth.User) -> dict[str, Any]:
    items = album_item_ids(row["id"], who)
    cover = row["cover_id"]
    if cover is None or not visible_ids([cover], who):
        cover = items[0] if items else None
    date_key = None
    if items:
        where, params = visible(who)
        date_key = conn().execute(
            f"""SELECT MAX(NULLIF(a.date_key, '')) FROM album_items ai
                JOIN assets a ON a.id = ai.asset_id WHERE ai.album_id = ? AND {where}""",
            [row["id"], *params]).fetchone()[0]
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "cover_id": cover, "created_by": row["created_by"], "date_key": date_key,
            "item_ids": items, "n": len(items)}


@bp.get("/api/albums")
def albums():
    who = require_family()
    where, params = visible(who)
    rows = conn().execute(
        f"""SELECT al.id, al.name, al.created_at, al.created_by,
               (SELECT COUNT(*) FROM album_items ai JOIN assets a ON a.id = ai.asset_id
                 WHERE ai.album_id = al.id AND {where}) AS n,
               COALESCE(
                 (SELECT a.id FROM assets a WHERE a.id = al.cover_id AND {where}),
                 (SELECT a.id FROM album_items ai JOIN assets a ON a.id = ai.asset_id
                   WHERE ai.album_id = al.id AND {where}
                   ORDER BY ai.added_at DESC, ai.rowid DESC LIMIT 1)) AS cover_id,
               (SELECT MAX(NULLIF(a.date_key, '')) FROM album_items ai
                  JOIN assets a ON a.id = ai.asset_id
                 WHERE ai.album_id = al.id AND {where}) AS date_key
            FROM albums al ORDER BY al.name COLLATE NOCASE, al.id""",
        params * 4).fetchall()
    out = []
    for r in rows:
        # An album showing this person nothing is left out, unless it is theirs.
        if r["n"] == 0 and r["created_by"] != who.id and not who.is_admin:
            continue
        out.append({"id": r["id"], "name": r["name"], "n": r["n"], "cover_id": r["cover_id"],
                    "created_at": r["created_at"], "created_by": r["created_by"],
                    "date_key": r["date_key"]})
    return jsonify({"albums": out})


@bp.post("/api/albums")
def album_create():
    who = require_family()
    data = album_body()
    name = data.get("name", "")
    if not isinstance(name, str):
        fail(400, "Album name must be text")
    name = name.strip()[:120]
    if not name:
        fail(400, "Name required")
    # Only what the caller can see goes in: an album cannot collect ids
    # its maker was never allowed to know exist.
    ids = visible_ids(album_ids_arg(data), who)
    c = conn()
    now = time.time()
    with c:
        album_id = c.execute("INSERT INTO albums (name, created_at, created_by) VALUES (?, ?, ?)",
                             (name, now, who.id)).lastrowid
        c.executemany("INSERT OR IGNORE INTO album_items (album_id, asset_id, added_at) "
                      "VALUES (?, ?, ?)", [(album_id, i, now) for i in ids])
    return jsonify({"id": album_id, "name": name})


@bp.get("/api/albums/<int:album_id>")
def album_get(album_id: int):
    who = require_family()
    row = conn().execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    if row is None:
        fail(404, "Not found.")
    album = album_shape(row, who)
    if not album["n"] and row["created_by"] != who.id and not who.is_admin:
        fail(404, "Not found.")
    return jsonify({"album": album})


@bp.patch("/api/albums/<int:album_id>")
def album_update(album_id: int):
    who = require_family()
    own_album(album_id, who)
    data = album_body()
    name, cover = data.get("name"), data.get("cover_id")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        fail(400, "Album name must be non-empty text")
    cover_id: int | None = None
    if cover is not None:
        if isinstance(cover, (bool, float)) or not isinstance(cover, (int, str)):
            fail(400, "Asset IDs must be integers")
        cover_id = 0 if str(cover).strip() == "0" else as_id(cover)
        if cover_id is None:
            fail(400, "Asset ID is out of range")
        if cover_id and not visible_ids([cover_id], who):
            fail(400, "Cover asset not accessible")
    c = conn()
    with c:
        if name is not None:
            c.execute("UPDATE albums SET name = ? WHERE id = ?", (name.strip()[:120], album_id))
        if cover is not None:
            c.execute("UPDATE albums SET cover_id = ? WHERE id = ?", (cover_id or None, album_id))
    row = c.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    return jsonify({"ok": True, "album": album_shape(row, who)})


@bp.delete("/api/albums/<int:album_id>")
def album_delete(album_id: int):
    who = require_family()
    own_album(album_id, who)
    c = conn()
    with c:
        c.execute("DELETE FROM album_items WHERE album_id = ?", (album_id,))
        c.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    return jsonify({"ok": True})


@bp.post("/api/albums/<int:album_id>/items")
def album_items(album_id: int):
    who = require_family()
    own_album(album_id, who)
    data = album_body()
    if "remove" in data and not isinstance(data["remove"], bool):
        fail(400, "Remove must be true or false")
    ids = visible_ids(album_ids_arg(data), who)
    c = conn()
    with c:
        if data.get("remove"):
            c.executemany("DELETE FROM album_items WHERE album_id = ? AND asset_id = ?",
                          [(album_id, i) for i in ids])
        else:
            now = time.time()
            c.executemany("INSERT OR IGNORE INTO album_items (album_id, asset_id, added_at) "
                          "VALUES (?, ?, ?)", [(album_id, i, now) for i in ids])
    return jsonify({"ok": True, "count": len(album_item_ids(album_id, who))})
