"""The same photograph or video in two places in the library: found, never
removed.

A file is only ever read for this when another file in the library is
exactly as large. Then the size and its first and last 64 KiB are hashed
(:func:`quick_hash`, two small reads however large the file); only files
whose quick hashes agree are read whole (:func:`content_hash`). Both are
kept in the index (index version 11) and forgotten when the file changes,
so each file is read at most once, in the scan's quiet pass after the
thumbnails, one file at a time and at the scan's lower priority.

The console lists what it finds (:func:`groups`). Ninaivu Lite never moves
or deletes a photograph: removing a copy is done in the folders themselves,
or in Ninaivu.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import Any

from . import parallel
from .dates import long_path

#: How much of each end of a file the quick hash reads.
QUICK_PART = 64 * 1024
#: Read at a time for the full hash, the next piece read ahead (see parallel.chunks).
CHUNK = 1024 * 1024
#: The quick hash of a file that is there but cannot be read (no permission):
#: not asked again until the file changes.
UNREADABLE = ""

#: Every query here leaves out what the gallery never shows on its own: a
#: missing file, and a Live Photo's short video (it goes with its still).
SHOWN = "missing = 0 AND live_of IS NULL"
SHOWN_A = "a.missing = 0 AND a.live_of IS NULL"


def quick_hash(path: str, size: int) -> str:
    """blake2b of the size, the first and the last :data:`QUICK_PART` bytes."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(size.to_bytes(8, "little"))
    with open(long_path(path), "rb") as f:
        digest.update(f.read(QUICK_PART))
        if size > 2 * QUICK_PART:
            f.seek(size - QUICK_PART)
        digest.update(f.read(QUICK_PART))
    return digest.hexdigest()


def content_hash(path: str, check: Callable[[], Any] | None = None) -> str:
    """blake2b of every byte. *check* may raise to stop between chunks."""
    digest = hashlib.blake2b(digest_size=32)
    with open(long_path(path), "rb") as f:
        for chunk in parallel.chunks(f, CHUNK, check):
            digest.update(chunk)
    return digest.hexdigest()


#: Read by both lists below: what a file is and where.
_COLUMNS = "a.id, a.dir, a.name, a.size, a.mtime, f.path AS root"
#: A page of either list starts after the last file of the one before (by
#: size, largest first, then id): a file that could not be read this time
#: stays in the list and must not be read again at once.
_AFTER = "AND (a.size < ? OR (a.size = ? AND a.id > ?))"


def to_quick_hash(conn: sqlite3.Connection, after: tuple[int, int] | None = None,
                  limit: int = 200) -> list[sqlite3.Row]:
    """Files not yet quick-hashed whose size another file in the library has."""
    return conn.execute(
        f"""SELECT {_COLUMNS} FROM assets a JOIN folders f ON f.id = a.folder_id
            WHERE {SHOWN_A} AND a.quick_hash IS NULL
              AND a.size IN (SELECT size FROM assets WHERE {SHOWN} AND size > 0
                             GROUP BY size HAVING COUNT(*) > 1)
              {_AFTER if after else ""}
            ORDER BY a.size DESC, a.id LIMIT ?""",
        (*((after[0], after[0], after[1]) if after else ()), limit)).fetchall()


def to_content_hash(conn: sqlite3.Connection, after: tuple[int, int] | None = None,
                    limit: int = 50) -> list[sqlite3.Row]:
    """Files not yet hashed whole whose quick hash another file shares."""
    return conn.execute(
        f"""SELECT {_COLUMNS} FROM assets a JOIN folders f ON f.id = a.folder_id
            WHERE {SHOWN_A} AND a.content_hash IS NULL
              AND a.quick_hash IS NOT NULL AND a.quick_hash != ''
              AND (a.size, a.quick_hash) IN (
                  SELECT size, quick_hash FROM assets
                  WHERE {SHOWN} AND quick_hash IS NOT NULL AND quick_hash != ''
                  GROUP BY size, quick_hash HAVING COUNT(*) > 1)
              {_AFTER if after else ""}
            ORDER BY a.size DESC, a.id LIMIT ?""",
        (*((after[0], after[0], after[1]) if after else ()), limit)).fetchall()


#: The groups of copies: the same size and every byte the same.
GROUPS_SQL = f"""SELECT size, content_hash, COUNT(*) AS n FROM assets
                 WHERE content_hash IS NOT NULL AND content_hash != '' AND {SHOWN}
                 GROUP BY size, content_hash HAVING n > 1"""


def totals(read: Callable[[str, tuple], list[tuple]]) -> dict[str, int]:
    """How many files have a copy elsewhere (counting each group once), how
    many copies there are beyond the first, and the bytes those take.
    *read* runs a query (common.counted, remembered until the index changes)."""
    groups, extra, wasted = read(
        f"SELECT COUNT(*), COALESCE(SUM(n - 1), 0), COALESCE(SUM(size * (n - 1)), 0) "
        f"FROM ({GROUPS_SQL})", ())[0]
    pending = read(
        f"""SELECT COUNT(*) FROM assets WHERE {SHOWN} AND (
                (quick_hash IS NULL AND size IN (
                    SELECT size FROM assets WHERE {SHOWN} AND size > 0
                    GROUP BY size HAVING COUNT(*) > 1))
                OR (content_hash IS NULL AND quick_hash != '' AND (size, quick_hash) IN (
                    SELECT size, quick_hash FROM assets
                    WHERE {SHOWN} AND quick_hash IS NOT NULL AND quick_hash != ''
                    GROUP BY size, quick_hash HAVING COUNT(*) > 1)))""", ())[0][0]
    return {"groups": groups, "extra": extra, "bytes": wasted, "pending": pending}


def groups(conn: sqlite3.Connection, offset: int, limit: int) -> list[dict[str, Any]]:
    """One page of groups, those taking the most room first, each with every
    copy: where it is, how large, and when it was taken."""
    page = conn.execute(
        f"SELECT size, content_hash, n FROM ({GROUPS_SQL}) "
        f"ORDER BY size * (n - 1) DESC, content_hash LIMIT ? OFFSET ?",
        (limit, offset)).fetchall()
    if not page:
        return []
    hashes = [r["content_hash"] for r in page]
    copies: dict[str, list[sqlite3.Row]] = {h: [] for h in hashes}
    for row in conn.execute(
            f"""SELECT a.id, a.dir, a.name, a.kind, a.size, a.date_key, a.captured_at,
                       a.thumb, a.thumb_v, a.visibility, a.content_hash, f.path AS root
                FROM assets a JOIN folders f ON f.id = a.folder_id
                WHERE a.content_hash IN ({','.join('?' * len(hashes))})
                  AND {SHOWN_A}
                ORDER BY f.path, a.dir, a.name, a.id""", hashes):
        copies[row["content_hash"]].append(row)
    return [{"size": r["size"], "count": len(copies[r["content_hash"]]),
             "items": copies[r["content_hash"]]}
            for r in page if len(copies[r["content_hash"]]) > 1]
