"""The family gallery's albums: listing, making, renaming, a cover, adding
and removing photographs.

Only what the maker may see goes into an album, and each album is shown with
only what the person asking may see. Lite never changes a photograph: albums
are rows in the index.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from typing import Any

from flask import Blueprint, jsonify

from . import auth
from .api_gallery import IN_CHUNK, MAX_IDS, as_id, visible_ids
from .common import conn, fail, json_sent, require_family, visible

bp = Blueprint("api_albums", __name__)

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
    data = json_sent()
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
            "cover_id": cover, "cover_v": cover_versions([cover]).get(cover, 0),
            "created_by": row["created_by"], "date_key": date_key,
            "item_ids": items, "n": len(items)}


def cover_versions(cover_ids: Sequence[int | None]) -> dict[int, int]:
    """Each cover's thumbnail version: thumbnails are cached for a year, so a
    cover turned since is asked for at a new address (``thumbUrl``)."""
    wanted = sorted({i for i in cover_ids if i})
    out: dict[int, int] = {}
    for start in range(0, len(wanted), IN_CHUNK):
        piece = wanted[start:start + IN_CHUNK]
        out.update((r[0], r[1] or 0) for r in conn().execute(
            f"SELECT id, thumb_v FROM assets WHERE id IN ({','.join('?' * len(piece))})", piece))
    return out


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
    versions = cover_versions([r["cover_id"] for r in rows])
    for r in rows:
        # An album showing this person nothing is left out, unless it is theirs.
        if r["n"] == 0 and r["created_by"] != who.id and not who.is_admin:
            continue
        out.append({"id": r["id"], "name": r["name"], "n": r["n"], "cover_id": r["cover_id"],
                    "cover_v": versions.get(r["cover_id"], 0),
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
