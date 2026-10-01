"""A Google Photos export's albums, made again in Lite's library after an import.

Google Photos exports a folder per album, each with a ``metadata.json``
naming it, and a photograph in three albums appears in three folders; the
importer's own duplicate check keeps one copy. Once the import has run, the
index knows where every member went, so the albums can be made without
reading the export again. (Dates, places and descriptions from the sidecars
are read by :mod:`dates` and the scanner.)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any

from . import importer
from .dates import long_path

#: Takeout folders that are not albums, in the account's language: "Photos
#: from 2019", "Fotos von 2019", Untitled, Trash, Archive, Failed videos.
_NOT_ALBUMS = re.compile(
    r"^(?:(?:photos?|fotos?|foto's)\s+\w+\s+\d{4}"
    r"|untitled(?:\(\d+\))?|trash|bin|archive|failed videos)$", re.IGNORECASE)
MAX_FOLDERS = 20_000


def _album_title(folder: str) -> str | None:
    try:
        with open(long_path(os.path.join(folder, "metadata.json")), encoding="utf-8",
                  errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not title.strip() or _NOT_ALBUMS.match(title.strip()):
        return None
    return title.strip()[:120]


def albums_in(sources: list[str]) -> list[dict[str, Any]]:
    """Every album folder under the sources: ``{"title", "folder", "files"}``,
    the files by absolute path (an album folder is flat)."""
    albums: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if not os.path.isdir(long_path(source)):
            continue
        stack = [os.path.abspath(source)]
        while stack and len(seen) < MAX_FOLDERS:
            folder = stack.pop()
            key = os.path.normcase(folder)
            if key in seen:
                continue
            seen.add(key)
            try:
                with os.scandir(long_path(folder)) as entries:
                    items = sorted(entries, key=lambda e: e.name)
            except OSError:
                continue
            files, subdirs = [], []
            for entry in items:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            subdirs.append(os.path.join(folder, entry.name))
                    elif entry.is_file(follow_symlinks=False) and entry.name != "metadata.json" \
                            and not importer.is_sidecar(entry.name) \
                            and importer.kind_by_extension(entry.name):
                        files.append(os.path.join(folder, entry.name))
                except OSError:
                    continue
            stack.extend(reversed(subdirs))
            title = _album_title(folder)
            if title and files:
                albums.append({"title": title, "folder": folder, "files": files})
    return albums


def _asset_id(conn: sqlite3.Connection, roots: dict[str, int], path: str) -> int | None:
    """The library row for an archived file, by library folder, dir and name."""
    for root, folder_id in roots.items():
        if not importer.is_within(path, root):
            continue
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")
        rel_dir, _, name = rel.rpartition("/")
        row = conn.execute("SELECT id FROM assets WHERE folder_id = ? AND dir = ? AND name = ?",
                           (folder_id, rel_dir, name)).fetchone()
        if row:
            return int(row[0])
    return None


def recreate(conn: sqlite3.Connection, sources: list[str], created_by: int) -> dict[str, Any]:
    """Make the export's albums in the library. Members are followed from the
    source through the importer's record to the archived copy, then to the
    index; a member not indexed yet is counted as ``unmatched``."""
    roots = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM folders")}
    made, unmatched = [], 0
    now = time.time()
    for album in albums_in(sources):
        ids: list[int] = []
        for source in album["files"]:
            row = conn.execute("SELECT status, destination, duplicate_of FROM import_files "
                               "WHERE source = ?", (source,)).fetchone()
            archived = None
            if row and row["status"] == "verified":
                archived = row["destination"]
            elif row and row["status"] == "duplicate":
                archived = row["duplicate_of"]
            asset = _asset_id(conn, roots, archived) if archived else None
            if asset is None:
                unmatched += 1
            elif asset not in ids:
                ids.append(asset)
        if not ids:
            continue
        with conn:
            existing = conn.execute("SELECT id FROM albums WHERE name = ? AND created_by = ?",
                                    (album["title"], created_by)).fetchone()
            album_id = int(existing[0]) if existing else int(conn.execute(
                "INSERT INTO albums (name, created_at, created_by) VALUES (?, ?, ?)",
                (album["title"], now, created_by)).lastrowid)
            before = conn.execute("SELECT COUNT(*) FROM album_items WHERE album_id = ?",
                                  (album_id,)).fetchone()[0]
            conn.executemany("INSERT OR IGNORE INTO album_items (album_id, asset_id, added_at) "
                             "VALUES (?, ?, ?)", [(album_id, i, now) for i in ids])
            after = conn.execute("SELECT COUNT(*) FROM album_items WHERE album_id = ?",
                                 (album_id,)).fetchone()[0]
            if not existing or after > before:
                conn.execute("UPDATE albums SET cover_id = COALESCE(cover_id, ?) WHERE id = ?",
                             (ids[0], album_id))
        made.append({"id": album_id, "title": album["title"], "added": after - before})
    return {"albums": made, "unmatched": unmatched}
