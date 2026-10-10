"""The index: one SQLite file in the data folder.

Table and column names follow Ninaivu's where the two overlap (users,
sessions, user_assets, albums, album_items, shares, visibility_batches,
visibility_undo), so the screens taken from Ninaivu read the same shapes and a
household that moves up to Ninaivu carries its data across cleanly.

The schema has a version from day one; :data:`MIGRATIONS` brings an older file
forward in order, so an upgrade never asks anyone to rebuild their library.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DB_FILE = "ninaivu-lite.db"

# Visibility levels (Ninaivu's numbers).
VIS_PUBLIC, VIS_FAMILY, VIS_HIDDEN = 0, 1, 2
VIS_NAMES = {VIS_PUBLIC: "public", VIS_FAMILY: "family", VIS_HIDDEN: "hidden"}
VIS_VALUES = {name: value for value, name in VIS_NAMES.items()}

#: Grid thumbnail states.
THUMB_PENDING, THUMB_OK, THUMB_NONE = 0, 1, 2

MIGRATIONS: list[str] = [
    # 1 — the first schema
    """
    CREATE TABLE folders (
        id          INTEGER PRIMARY KEY,
        path        TEXT NOT NULL UNIQUE,
        added_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE assets (
        id            INTEGER PRIMARY KEY,
        folder_id     INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
        dir           TEXT NOT NULL,        -- folder inside the library folder, '/'-separated, '' at the top
        name          TEXT NOT NULL,
        ext           TEXT NOT NULL,        -- lower case, no dot
        kind          TEXT NOT NULL,        -- 'picture' | 'video'
        size          INTEGER NOT NULL,
        mtime         REAL NOT NULL,
        captured_at   REAL NOT NULL,
        date_key      TEXT NOT NULL,        -- 'YYYY-MM-DD' of captured_at, local time
        date_source   TEXT NOT NULL,        -- exif | container | filename | mtime
        width         INTEGER,              -- as displayed, after EXIF rotation
        height        INTEGER,
        duration      REAL NOT NULL DEFAULT 0,
        camera        TEXT,
        lens          TEXT,
        iso           INTEGER,
        f_number      REAL,
        exposure      TEXT,
        focal_length  REAL,
        gps_lat       REAL,
        gps_lon       REAL,
        color         TEXT,                 -- '#rrggbb' from the thumbnail, painted while it loads
        error         TEXT,
        visibility    INTEGER NOT NULL DEFAULT 1,
        vis_source    TEXT NOT NULL DEFAULT 'default',   -- default | rule | item
        thumb         INTEGER NOT NULL DEFAULT 0,        -- the small (256) thumbnail
        large         INTEGER NOT NULL DEFAULT 0,        -- the 640 one exists
        thumb_v       INTEGER NOT NULL DEFAULT 0,        -- changes whenever the thumbnails do
        missing       INTEGER NOT NULL DEFAULT 0,
        added_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
        UNIQUE (folder_id, dir, name)
    );
    CREATE INDEX assets_date ON assets (missing, captured_at DESC, id DESC);
    CREATE INDEX assets_dir ON assets (folder_id, dir);
    CREATE INDEX assets_counts ON assets (missing, visibility, kind);
    CREATE INDEX assets_thumb ON assets (thumb, large, captured_at DESC);

    CREATE TABLE users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name  TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'family',
        password      TEXT,                 -- scrypt hash, or NULL
        pin           TEXT,                 -- scrypt hash of a 4-8 digit PIN, or NULL
        color         TEXT,
        active        INTEGER NOT NULL DEFAULT 1,
        created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        last_login    REAL,
        must_change   INTEGER NOT NULL DEFAULT 0,
        library       TEXT,                 -- absolute folder this person may see, or NULL for all
        home_label    TEXT,
        language      TEXT
    );
    CREATE TABLE sessions (
        token_hash    TEXT PRIMARY KEY,     -- sha256 of the cookie; the cookie itself is never stored
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at    REAL NOT NULL,
        seen_at       REAL NOT NULL,
        expires_at    REAL NOT NULL,
        agent         TEXT
    );
    CREATE INDEX sessions_user ON sessions (user_id);

    CREATE TABLE user_assets (
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        favorite      INTEGER NOT NULL DEFAULT 0,
        added_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
        PRIMARY KEY (user_id, asset_id)
    );
    CREATE INDEX user_assets_fav ON user_assets (user_id, favorite);

    CREATE TABLE albums (
        id            INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
        cover_id      INTEGER REFERENCES assets(id) ON DELETE SET NULL,
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE album_items (
        album_id      INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        added_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
        PRIMARY KEY (album_id, asset_id)
    );
    CREATE INDEX album_items_asset ON album_items (asset_id);

    CREATE TABLE shares (
        id            INTEGER PRIMARY KEY,
        token         TEXT NOT NULL UNIQUE,
        scope         TEXT NOT NULL,        -- 'album' | 'asset'
        target_id     INTEGER NOT NULL,
        created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
        created_by    INTEGER REFERENCES users(id) ON DELETE CASCADE,
        expires_at    REAL,
        password      TEXT,
        view_count    INTEGER NOT NULL DEFAULT 0
    );

    -- Who may see a folder and everything under it, files added later included.
    CREATE TABLE folder_rules (
        folder_id     INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
        dir           TEXT NOT NULL,
        visibility    INTEGER NOT NULL,
        created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
        PRIMARY KEY (folder_id, dir)
    );
    -- Every bulk visibility change and what it overwrote, so it can be undone.
    CREATE TABLE visibility_batches (
        id            INTEGER PRIMARY KEY,
        created_at    REAL NOT NULL,
        created_by    INTEGER,
        scope         TEXT NOT NULL,        -- 'folder' | 'items'
        folder_id     INTEGER,
        dir           TEXT,
        visibility    INTEGER NOT NULL,
        affected      INTEGER NOT NULL DEFAULT 0,
        exposed       INTEGER NOT NULL DEFAULT 0,
        had_rule      INTEGER NOT NULL DEFAULT 0,
        prior_rule    INTEGER,
        undone_at     REAL
    );
    CREATE TABLE visibility_undo (
        batch_id      INTEGER NOT NULL REFERENCES visibility_batches(id) ON DELETE CASCADE,
        asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        visibility    INTEGER NOT NULL,
        vis_source    TEXT NOT NULL,
        PRIMARY KEY (batch_id, asset_id)
    );
    """,
    # 2 — a folder rule replaces the rules below it; remember those too, so
    # undoing the change puts them back.
    """
    CREATE TABLE visibility_undo_rules (
        batch_id      INTEGER NOT NULL REFERENCES visibility_batches(id) ON DELETE CASCADE,
        dir           TEXT NOT NULL,
        visibility    INTEGER NOT NULL,
        created_at    REAL,
        PRIMARY KEY (batch_id, dir)
    );
    """,
    # 3 — the grid's first page straight from one index: every column
    # /api/segments reads is in it, in date order, so a page of 25,000 rows is
    # read in order without visiting the table once (2-3x faster on 100,000
    # rows). It starts with the same columns as assets_date, which it replaces.
    """
    CREATE INDEX assets_grid ON assets (missing, captured_at DESC, id DESC, visibility, kind,
                                        width, height, duration, thumb, thumb_v, color, date_key);
    DROP INDEX assets_date;
    """,
    # 4 — the importer: one row per file it has looked at, one per run. Progress
    # lives here, so Start after a power cut carries on where it stopped.
    """
    CREATE TABLE import_files (
        id            INTEGER PRIMARY KEY,
        source        TEXT NOT NULL UNIQUE,  -- absolute path in the source folder
        name          TEXT NOT NULL,
        size          INTEGER NOT NULL DEFAULT 0,
        mtime         REAL,
        hash          TEXT,                  -- sha256 of the source, when read
        dest_hash     TEXT,                  -- sha256 of the copy, re-read from disk
        taken         TEXT,                  -- 'YYYY-MM-DD HH:MM:SS' the file was filed under
        date_source   TEXT,
        status        TEXT NOT NULL,         -- pending | verified | duplicate | skipped | error
                                             -- | planned | plan-duplicate | plan-skip
        destination   TEXT,                  -- where the copy went (or would go)
        duplicate_of  TEXT,
        error         TEXT,
        job_id        INTEGER,
        updated_at    REAL NOT NULL
    );
    CREATE INDEX import_files_hash ON import_files (hash, status);
    CREATE INDEX import_files_size ON import_files (size, status);
    CREATE INDEX import_files_status ON import_files (status, updated_at DESC);
    CREATE TABLE import_jobs (
        id            INTEGER PRIMARY KEY,
        sources       TEXT NOT NULL,         -- JSON list of absolute paths
        destination   TEXT NOT NULL,
        mode          TEXT NOT NULL,         -- copy | dry-run | verify
        state         TEXT NOT NULL,         -- running | completed | stopped | failed
        phase         TEXT NOT NULL DEFAULT 'counting',
        total_files   INTEGER NOT NULL DEFAULT 0,
        total_bytes   INTEGER NOT NULL DEFAULT 0,
        bytes_copied  INTEGER NOT NULL DEFAULT 0,
        started_at    REAL NOT NULL,
        ended_at      REAL,
        message       TEXT
    );
    """,
    # 5 — which way up a photograph goes, decided during the scan and stored
    # here, never in the file: the camera's tag, faces (when OpenCV is there),
    # or a person. `upright` says the question has been looked at.
    """
    ALTER TABLE assets ADD COLUMN rotation INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE assets ADD COLUMN rot_source TEXT NOT NULL DEFAULT 'none';
    ALTER TABLE assets ADD COLUMN upright INTEGER NOT NULL DEFAULT 1;
    UPDATE assets SET upright = 0 WHERE kind = 'picture';
    CREATE INDEX assets_upright ON assets (upright, kind, missing);
    """,
    # 6 — a profile picture a person chose from the library: the moment it was
    # set, so the browser's cached copy is dropped when it changes. The small
    # square itself is avatars/<id>.jpg in the data folder.
    """
    ALTER TABLE users ADD COLUMN avatar_at REAL;
    """,
    # 7 — a share link dies with what it points at. Ids of deleted rows can be
    # handed out again (a library folder removed and another added), and a
    # link left behind would then show a photograph or album nobody shared.
    # Triggers fire for cascaded deletes too, so a removed folder's links go
    # with its photographs. Links already pointing at nothing are dropped.
    """
    DELETE FROM shares WHERE scope = 'asset'
        AND target_id NOT IN (SELECT id FROM assets);
    DELETE FROM shares WHERE scope = 'album'
        AND target_id NOT IN (SELECT id FROM albums);
    CREATE TRIGGER shares_asset_gone AFTER DELETE ON assets BEGIN
        DELETE FROM shares WHERE scope = 'asset' AND target_id = OLD.id;
    END;
    CREATE TRIGGER shares_album_gone AFTER DELETE ON albums BEGIN
        DELETE FROM shares WHERE scope = 'album' AND target_id = OLD.id;
    END;
    """,
    # 8 — a folder's visibility history goes with the folder. Its id can be
    # handed to the next folder added, and undoing an old change would then
    # put the removed folder's rules (2019 = Public, say) on that one.
    # History left from folders already removed, or made before the folder
    # now holding that id was added, is dropped.
    """
    DELETE FROM visibility_batches WHERE folder_id IS NOT NULL AND (
        folder_id NOT IN (SELECT id FROM folders)
        OR created_at < (SELECT added_at FROM folders WHERE id = folder_id));
    CREATE TRIGGER IF NOT EXISTS visibility_folder_gone AFTER DELETE ON folders BEGIN
        DELETE FROM visibility_batches WHERE folder_id = OLD.id;
    END;
    """,
    # 9 — a library folder taken out of the library is set aside, not
    # deleted: its photographs' visibility, favourites, album places, folder
    # rules and share links wait, unseen (every photograph counts as
    # missing), and come back as they were when the folder is added again.
    """
    ALTER TABLE folders ADD COLUMN detached_at REAL;
    """,
    # 10 — a photograph or video shown mirrored, set by hand next to its turn
    # (Flip in the viewer). Kept here, never in the file: the picture is
    # mirrored left to right first, then turned by `rotation`. A vertical
    # flip is a mirror and a half turn, so this one column says both.
    """
    ALTER TABLE assets ADD COLUMN mirror INTEGER NOT NULL DEFAULT 0;
    """,
]


class NewerIndex(sqlite3.DatabaseError):
    """The index was made by a newer Ninaivu Lite than this one."""


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """A connection with the settings every caller wants, schema brought up to date."""
    path = Path(data_dir) / DB_FILE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    _private(path)              # before WAL: its files take the index's permissions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        # Locked by another process for longer than the busy timeout says
        # nothing about the disk: the file keeps the mode it has, rather than
        # leaving WAL for good.
        transient = "locked" in str(exc) or "busy" in str(exc)
        log.info("journal mode %s: %s", "left as it is" if transient else "not WAL", exc)
        mode = "wal" if transient else ""
    if str(mode).lower() != "wal":
        # Network drives and some old filesystems cannot do WAL. The classic
        # journal is slower with many readers but always works.
        conn.execute("PRAGMA journal_mode=DELETE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if 0 < version < len(MIGRATIONS):
            # The first start after an update: a recovery zip of the index as
            # the earlier version left it, before anything in it is changed.
            from . import backups
            backups.before_change(path.parent, f"update-from-index-{version}")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _private(path: Path) -> None:
    """The index holds share tokens and PIN hashes: readable by its owner
    only, on systems where other accounts could otherwise read it."""
    if os.name == "nt":
        return
    for one in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if one.stat().st_mode & 0o077:
                os.chmod(one, 0o600)
        except OSError:
            pass


_MIGRATE_LOCK = threading.Lock()


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the schema up to date. Safe with another process doing the same
    (the panel and the server, or two servers started at once): each step
    takes the write lock first and reads the version again under it."""
    with _MIGRATE_LOCK:
        while True:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise NewerIndex(
                    f"The index is version {version}, made by a newer Ninaivu Lite; this one "
                    f"knows versions up to {len(MIGRATIONS)}. Update Ninaivu Lite to open it.")
            if version == len(MIGRATIONS):
                return len(MIGRATIONS)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] == version:
                    number = version + 1
                    log.info("updating the index to version %d", number)
                    # One transaction per step: a power cut leaves the old version, whole.
                    for statement in _statements(MIGRATIONS[version]):
                        try:
                            conn.execute(statement)
                        except sqlite3.OperationalError as exc:
                            # A column another process (or an interrupted
                            # earlier start) already added is there: fine.
                            if not ("duplicate column" in str(exc)
                                    and "ADD COLUMN" in statement.upper()):
                                raise
                    conn.execute(f"PRAGMA user_version={number}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def _statements(script: str):
    """A migration's statements one at a time (``executescript`` would commit
    the transaction it runs in). A trigger's body is kept whole."""
    buffer = ""
    for piece in script.split(";"):
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            if buffer.strip(" \t\r\n;"):
                yield buffer
            buffer = ""


def sync_folders(conn: sqlite3.Connection, paths: list[str]) -> dict[str, int]:
    """Make the folders table match the settings; returns path → id of the
    folders in the library.

    A folder removed from the settings is set aside with everything decided
    about its photographs (see migration 9); added again, it is brought
    back, and the next scan finds its photographs where they were. The
    photographs themselves are never touched.
    """
    with conn:
        rows = conn.execute("SELECT id, path, detached_at FROM folders").fetchall()
        have = {r["path"]: r["id"] for r in rows}
        detached = {r["path"] for r in rows if r["detached_at"] is not None}
        for path in paths:
            if path not in have:
                cur = conn.execute("INSERT INTO folders (path) VALUES (?)", (path,))
                have[path] = int(cur.lastrowid)
            elif path in detached:
                conn.execute("UPDATE folders SET detached_at = NULL WHERE id = ?", (have[path],))
        for path, fid in list(have.items()):
            if path not in paths:
                if path not in detached:
                    conn.execute("UPDATE folders SET detached_at = strftime('%s','now') "
                                 "WHERE id = ?", (fid,))
                    conn.execute("UPDATE assets SET missing = 1 WHERE folder_id = ?", (fid,))
                del have[path]
        # A scan that was walking a folder when it was taken out could put
        # some of its photographs back (A148): whatever a set-aside folder
        # shows is set aside again, on every sync.
        conn.execute("UPDATE assets SET missing = 1 WHERE missing = 0 AND folder_id IN "
                     "(SELECT id FROM folders WHERE detached_at IS NOT NULL)")
    return have


def move_folder(conn: sqlite3.Connection, old: str, new: str) -> None:
    """A library folder's photographs are now at *new* (another drive, a new
    computer): the same rows, so everything decided about them is kept. A
    set-aside folder that was at *new* gives way."""
    with conn:
        conn.execute("DELETE FROM folders WHERE path = ? AND detached_at IS NOT NULL", (new,))
        conn.execute("UPDATE folders SET path = ? WHERE path = ?", (new, old))


class Remembered:
    """Answers that change only when the index does: the library's counts and
    its years, folders and cameras. Every page load used to count the whole
    library again (50-200 ms a count on 100,000 photographs, several times
    that on a Raspberry Pi) for numbers that had not changed since the last
    load.

    Whether the index changed is SQLite's own answer: ``PRAGMA data_version``,
    asked on a connection kept for nothing else, moves whenever any other
    connection commits a change, in this process or another (the scanner,
    the Control Panel). An answer is kept only when the index did not change
    while it was worked out, and used only while it still has not.
    """

    def __init__(self, data_dir: str | Path, limit: int = 128) -> None:
        self._path = Path(data_dir) / DB_FILE
        self._limit = limit
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._version: int | None = None
        self._answers: OrderedDict[Any, Any] = OrderedDict()

    def _current(self) -> int | None:
        """The index's change counter now, or None when it cannot be asked
        (nothing is then remembered). Called with the lock held."""
        try:
            if self._conn is None:
                # Never creating the index, and never writing to it: it only watches.
                self._conn = sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=rw", uri=True,
                                             timeout=5, check_same_thread=False)
            return int(self._conn.execute("PRAGMA data_version").fetchone()[0])
        except (sqlite3.Error, ValueError):
            self.close()
            return None

    def get(self, key: Any, compute: Callable[[], Any],
            keep: Callable[[Any], bool] | None = None) -> Any:
        """The answer for *key*, worked out by *compute* unless remembered.
        *keep* can say an answer is not worth remembering (too large)."""
        with self._lock:
            before = self._current()
            if before is not None and before == self._version and key in self._answers:
                self._answers.move_to_end(key)
                return self._answers[key]
        value = compute()
        if keep is not None and not keep(value):
            return value
        with self._lock:
            if before is not None and self._current() == before:
                if self._version != before:
                    self._answers = OrderedDict()
                    self._version = before
                self._answers[key] = value
                self._answers.move_to_end(key)
                # Full: the answer asked for longest ago gives way, not all of
                # them, so several people browsing at once still find theirs (A153).
                while len(self._answers) > self._limit:
                    self._answers.popitem(last=False)
        return value

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn, self._version, self._answers = None, None, OrderedDict()


def rules_of(conn: sqlite3.Connection, folder_id: int) -> dict[str, int]:
    """A library folder's rules: folder inside it -> visibility."""
    return {r[0]: int(r[1]) for r in conn.execute(
        "SELECT dir, visibility FROM folder_rules WHERE folder_id = ?", (folder_id,))}


def rule_in(rules: dict[str, int], rel_dir: str) -> int | None:
    """The nearest of *rules* on *rel_dir* or above it, or None."""
    parts = [p for p in rel_dir.split("/") if p] if rel_dir else []
    for i in range(len(parts), -1, -1):
        candidate = "/".join(parts[:i])
        if candidate in rules:
            return rules[candidate]
    return None


def rule_for(conn: sqlite3.Connection, folder_id: int, rel_dir: str) -> int | None:
    """The visibility a folder rule gives a file in *rel_dir*: the nearest rule
    on that folder or above it, or None when no rule applies."""
    return rule_in(rules_of(conn, folder_id), rel_dir)
