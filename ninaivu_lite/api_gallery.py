"""The family gallery's calls: status and counts, the grid, facets and
suggestions, one photograph, favourites, turning, and per-item visibility.
Its files and downloads are in :mod:`gallery_files`, its albums in
:mod:`api_albums`.

Shapes and sentences follow Ninaivu's (see the contract): the screens taken
from Ninaivu read these fields by name, and Ninaivu's Tamil translations key
on the English sentences. Every query goes through :func:`common.visible`, so
nobody is ever shown, counted or served a photograph above their ceiling; an
item someone may not see is answered with 404, never 403.

Lite never changes a photograph: the only writes here are to the index
(favourites, visibility, albums).
"""

from __future__ import annotations

import calendar
import io
import json
import math
import re
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from PIL import Image

from . import auth, db, media
from .api_auth import public_of
from .common import (
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
    subtree,
    user,
    visible,
    visible_asset,
)

bp = Blueprint("api_gallery", __name__)

KIND_CODES = {"picture": 0, "video": 1}
MAX_ID = 2 ** 63 - 1
MAX_LIMIT = 200_000
DEFAULT_LIMIT = 100_000
MAX_IDS = 5000
IN_CHUNK = 500

SORTS = {
    "date_desc": "a.captured_at DESC, a.id DESC",
    "date_asc": "a.captured_at ASC, a.id ASC",
    "name_asc": "a.name COLLATE NOCASE ASC, a.id ASC",
    "name_desc": "a.name COLLATE NOCASE DESC, a.id DESC",
    "size_desc": "a.size DESC, a.id DESC",
    "random": "random()",
}

#: A search reads at most this many words, each at most this long: a few
#: hundred words, or one word of 50,000 characters, failed in SQLite (a 500
#: and a traceback in the log for each such request, from anyone).
SEARCH_WORDS = 32
SEARCH_WORD_CHARS = 200

YEAR_RE = re.compile(r"^\d{4}(-\d{2})?$")
#: "On this day": a day as the browser sees today, YYYY-MM-DD.
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: "Taken nearby": within about this many kilometres of the photograph asked about.
NEAR_KM = 1.0
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


def id_list(data: dict[str, Any], limit: int | None = MAX_IDS) -> list[int]:
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


def sent_count(data: dict[str, Any]) -> int:
    """How many ids were sent, before :func:`id_list` left any out: what was
    not done is told as skipped, never quietly dropped (A151)."""
    return len(id_list(data, limit=None))


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
                 "live", "first_date", "last_date")


def library_stats(who: auth.User) -> dict[str, Any]:
    where, params = visible(who)
    row = counted(
        f"""SELECT COUNT(*) AS count, COALESCE(SUM(a.size), 0) AS bytes,
                   COALESCE(SUM(ua.favorite = 1), 0) AS favorites,
                   COALESCE(SUM(a.kind = 'picture'), 0) AS pictures,
                   COALESCE(SUM(a.kind = 'video'), 0) AS videos,
                   COALESCE(SUM(a.visibility = 2), 0) AS hidden,
                   COALESCE(SUM(a.visibility = 0), 0) AS public,
                   COALESCE(SUM(a.live_id IS NOT NULL), 0) AS live,
                   MIN(NULLIF(a.date_key, '')) AS first_date,
                   MAX(NULLIF(a.date_key, '')) AS last_date
            FROM assets a
            LEFT JOIN user_assets ua ON ua.asset_id = a.id AND ua.user_id = ?
            WHERE {where}""", [who.id, *params])[0]
    out = dict(zip(STATS_COLUMNS, row, strict=True))
    out["first_date"] = out["first_date"] or ""
    out["last_date"] = out["last_date"] or ""
    out.update(audio=0, duplicate_groups=0, nsfw=0, embedded=0)
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
        "user": public_of(who),
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
    for word in (query.get("q") or "").split()[:SEARCH_WORDS]:
        word = word[:SEARCH_WORD_CHARS]
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

    # The same day of the year in the years before (the "On this day" row).
    day = query.get("on_this_day") or ""
    if DAY_RE.match(day):
        days = [day[5:]]
        if days == ["02-28"] and not calendar.isleap(int(day[:4])):
            days.append("02-29")      # a leap day's photographs, in the years between
        clauses.append(f"substr(a.date_key, 6, 5) IN ({','.join('?' * len(days))}) "
                       f"AND a.date_key < ?")
        args += [*days, day[:4]]

    # Taken within a kilometre of a photograph this person may see. Where a
    # photograph was taken is for the family, as in its details.
    near = as_id(query.get("near"))
    if near is not None and who.family_or_more:
        box = near_box(near, who)
        if box is None:
            clauses.append("0")
        elif box[2] < -180 or box[3] > 180:
            # Across the 180th meridian (Fiji, Kamchatka): east of the west
            # edge, or west of the east edge, on the other side of it.
            clauses.append("a.gps_lat BETWEEN ? AND ? AND (a.gps_lon >= ? OR a.gps_lon <= ?)")
            args += [box[0], box[1], box[2] + 360 if box[2] < -180 else box[2],
                     box[3] - 360 if box[3] > 180 else box[3]]
        else:
            clauses.append("a.gps_lat BETWEEN ? AND ? AND a.gps_lon BETWEEN ? AND ?")
            args += box

    return " AND ".join(clauses), args


def near_box(asset_id: int, who: auth.User) -> list[float] | None:
    """South, north, west and east edges of the square around where *asset_id*
    was taken, or None when it has no place or this person cannot see it."""
    where, params = visible(who)
    row = conn().execute(
        f"SELECT a.gps_lat, a.gps_lon FROM assets a WHERE a.id = ? AND {where}",
        [asset_id, *params]).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    lat, lon = float(row[0]), float(row[1])
    dlat = NEAR_KM / 111.32
    dlon = NEAR_KM / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    return [lat - dlat, lat + dlat, lon - dlon, lon + dlon]


@bp.get("/api/segments")
def segments():
    who = user()
    where, args = grid_filters(who)
    sort = request.args.get("sort") or "date_desc"
    if sort not in SORTS:
        sort = "date_desc"
    limit = max(1, min(int_arg("limit", DEFAULT_LIMIT), MAX_LIMIT))
    offset = min(max(0, int_arg("offset")), MAX_ID)   # SQLite holds no more (A152)
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


#: The columns of a gallery page, in the order grid_page reads them.
GRID_COLUMNS = ("id", "width", "height", "kind", "visibility", "duration", "thumb", "thumb_v",
                "color", "date_key", "live_id")


def page_in_one_step(cur: sqlite3.Cursor, page_sql: str, params: Sequence[Any],
                     columns: Sequence[str]) -> list[Any]:
    """The rows of *page_sql* as lists, read as one JSON value rather than
    one step a row.

    sqlite3 lets go of the GIL on every row it steps and waits to take it
    back behind whichever thread holds it. With a scan busy in Python in the
    same process, fetching the gallery's 25,000 rows one at a time took
    seconds instead of a fraction of one. Built as one value inside SQLite it
    is a single step, as quick while a scan runs as when it does not. The
    order is the subquery's: SQLite feeds an aggregate the rows of an ordered
    subquery in that order. A column holding bytes has no JSON form: then the
    rows are read one by one, as before. (From Ninaivu 1.10.8.)
    """
    # A REAL in all 17 digits: json_array writes 15, which would move a
    # number by its last bits.
    wanted = ", ".join(f"CASE typeof(p.{name}) WHEN 'real' THEN json(printf('%!.17g', p.{name})) "
                       f"ELSE p.{name} END" for name in columns)
    try:
        text = cur.execute(f"SELECT json_group_array(json_array({wanted})) FROM ({page_sql}) p",
                           params).fetchone()[0]
    except sqlite3.OperationalError:
        return cur.execute(page_sql, params).fetchall()
    return json.loads(text) if text else []


def grid_page(who: auth.User, where: str, args: list[Any], sort: str, limit: int,
              offset: int) -> dict[str, Any]:
    pageable = sort != "random"
    c = conn()
    # Plain tuples, and no join: the columns read here are all in the
    # assets_grid index, so the date-ordered page never touches the table.
    cur = c.cursor()
    cur.row_factory = None
    total = cur.execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", args).fetchone()[0]
    rows = page_in_one_step(
        cur,
        f"""SELECT a.id, a.width, a.height, a.kind, a.visibility, a.duration, a.thumb,
                   a.thumb_v, a.color, a.date_key, a.live_id
            FROM assets a WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?""",
        [*args, limit, offset], GRID_COLUMNS)
    favs = favourite_ids(who) if rows else set()

    out: list[dict[str, Any]] = []
    current_key: str | None = None
    items: list[list[Any]] = []
    for aid, width, height, kind, vis, duration, thumb, thumb_v, color, day, live in rows:
        aspect = round(width / height * 100) if width and height else 100
        aspect = 30 if aspect < 30 else 400 if aspect > 400 else aspect
        has_thumb = thumb != db.THUMB_NONE
        flags = ((1 if aid in favs else 0) | (2 if has_thumb else 0)
                 | (4 if live else 0)   # a Live Photo
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
        return jsonify({"updated": 0, "skipped": sent_count(data)})
    seen = visible_ids(ids, who)
    if seen:
        set_favourite(who, seen, bool(data["favorite"]))
    return jsonify({"updated": len(seen), "skipped": sent_count(data) - len(seen)})


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
    # A video turned or flipped by hand: its picture is too.
    frame = media.turn(frame, row["rotation"] or 0, bool(row["mirror"]))
    colour = media.save_thumbnails(frame, scanner().thumbs_dir, asset_id)
    version = int(time.time() * 1000) % 2_000_000_000
    c = conn()
    with c:
        # Not when it was turned meanwhile: that picture is the old way up.
        c.execute("UPDATE assets SET thumb = ?, large = 1, color = ?, thumb_v = ? WHERE id = ? "
                  "AND rotation = ? AND mirror = ?",
                  (db.THUMB_OK, colour, version, asset_id, row["rotation"] or 0,
                   int(bool(row["mirror"]))))
    scanner().generation += 1
    return jsonify(asset_public(visible_asset(asset_id))), 201


TURNABLE = ("picture", "video")


def quarter_turn(value: Any) -> int:
    """0, 90, 180 or 270 from a request, or a 400."""
    try:
        rotation = int(value)
    except (TypeError, ValueError, OverflowError):
        fail(400, "Rotation must be 0, 90, 180 or 270.")
    if rotation % 360 not in media.ROTATIONS:
        fail(400, "Rotation must be 0, 90, 180 or 270.")
    return rotation % 360


@bp.post("/api/asset/<int:asset_id>/rotate")
def asset_rotate(asset_id: int):
    """Turn or flip a photograph or video by hand: the index's answer, never
    the file's. For the one the scan could not judge, or judged wrong, or one
    taken in a mirror. ``mirror`` left out keeps it; 0 and no mirror is the
    file as it is (Reset to original)."""
    require_admin()
    row = visible_asset(asset_id)
    if row["kind"] not in TURNABLE:
        fail(400, "Only photographs and videos can be turned.")
    data = body()
    rotation = quarter_turn(data.get("rotation", (row["rotation"] + 90) % 360))
    mirror = data.get("mirror")
    if mirror is not None and not isinstance(mirror, bool):
        fail(400, "Mirror must be true or false.")
    scanner().set_rotation(conn(), asset_id, rotation, "manual", mirror=mirror)
    return jsonify(asset_public(visible_asset(asset_id)))


@bp.post("/api/assets/rotate")
def assets_rotate():
    """Turn many at once by the same quarter turn (``turn``: 90 clockwise, 270
    the other way, 180), each from the way it is shown now: a whole card of
    photographs taken with the camera on its side. Never the files. The
    thumbnails are made again in the background."""
    who = require_admin()
    data = body()
    turn = quarter_turn(data.get("turn", 90))
    seen = visible_ids(id_list(data), who)
    rows = []
    for start in range(0, len(seen), 500):
        chunk = seen[start:start + 500]
        rows += conn().execute(
            "SELECT id, rotation FROM assets WHERE kind IN ('picture', 'video') AND id IN ("
            + ",".join("?" * len(chunk)) + ")", chunk).fetchall()
    s = scanner()
    turned = s.set_rotations(conn(), [(r["id"], (r["rotation"] or 0) + turn) for r in rows],
                             "manual")
    if turned:
        s.remake_later(turned)
    return jsonify({"updated": len(rows), "skipped": sent_count(data) - len(rows)})


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
        return jsonify({"updated": 0, "visibility": level_name, "skipped": sent_count(data)})
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
        # A Live Photo's short video goes with its still (and comes back with
        # it on undo): it is never chosen on its own, so it never would.
        changed = list(ids)
        for start in range(0, len(ids), IN_CHUNK):
            piece = ids[start:start + IN_CHUNK]
            changed += [r[0] for r in c.execute(
                f"SELECT id FROM assets WHERE live_of IN ({','.join('?' * len(piece))})", piece)]
        for start in range(0, len(changed), IN_CHUNK):
            piece = changed[start:start + IN_CHUNK]
            marks = ",".join("?" * len(piece))
            c.execute(
                f"""INSERT INTO visibility_undo (batch_id, asset_id, visibility, vis_source)
                    SELECT ?, id, visibility, vis_source FROM assets WHERE id IN ({marks})""",
                [batch, *piece])
            c.execute(f"UPDATE assets SET visibility = ?, vis_source = 'item' "
                      f"WHERE id IN ({marks})", [level, *piece])
    scanner().generation += 1
    return jsonify({"updated": len(ids), "visibility": level_name, "batch_id": batch,
                    "skipped": sent_count(data) - len(ids)})
