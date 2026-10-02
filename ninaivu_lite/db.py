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
import sqlite3
import threading
from pathlib import Path

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
]


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """A connection with the settings every caller wants, schema brought up to date."""
    path = Path(data_dir) / DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    except sqlite3.DatabaseError:
        mode = ""
    if str(mode).lower() != "wal":
        # Network drives and some old filesystems cannot do WAL. The classic
        # journal is slower with many readers but always works.
        conn.execute("PRAGMA journal_mode=DELETE")
    migrate(conn)
    return conn


_MIGRATE_LOCK = threading.Lock()


def migrate(conn: sqlite3.Connection) -> int:
    with _MIGRATE_LOCK:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            log.info("updating the index to version %d", number)
            # One transaction per step: a power cut leaves the old version, whole.
            conn.executescript(f"BEGIN; {script}; PRAGMA user_version={number}; COMMIT;")
        return len(MIGRATIONS)


def sync_folders(conn: sqlite3.Connection, paths: list[str]) -> dict[str, int]:
    """Make the folders table match the settings; returns path → id.

    A folder removed from the settings takes its index entries with it (the
    photos themselves are never touched).
    """
    with conn:
        have = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM folders")}
        for path in paths:
            if path not in have:
                cur = conn.execute("INSERT INTO folders (path) VALUES (?)", (path,))
                have[path] = int(cur.lastrowid)
        for path, fid in list(have.items()):
            if path not in paths:
                conn.execute("DELETE FROM folders WHERE id = ?", (fid,))
                del have[path]
    return have


def rule_for(conn: sqlite3.Connection, folder_id: int, rel_dir: str) -> int | None:
    """The visibility a folder rule gives a file in *rel_dir*: the nearest rule
    on that folder or above it, or None when no rule applies."""
    parts = [p for p in rel_dir.split("/") if p] if rel_dir else []
    rules = {r["dir"]: r["visibility"] for r in conn.execute(
        "SELECT dir, visibility FROM folder_rules WHERE folder_id = ?", (folder_id,))}
    for i in range(len(parts), -1, -1):
        candidate = "/".join(parts[:i])
        if candidate in rules:
            return int(rules[candidate])
    return None
