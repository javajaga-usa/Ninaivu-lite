"""Copies of what Ninaivu Lite cannot remake: the index (people, albums,
favourites, share links, who-sees-what), the settings (which folders make the
library, and the household's choices) and people's profile pictures.

Photos are never copied: they stay in your folders, and thumbnails can always
be made again. The same zip is taken once a day into ``backups/`` in the data
folder (the last seven kept), and the admin page offers one as a download to
keep somewhere else. Either restores on its own into an empty data folder.

Each copy uses SQLite's own backup, so it is consistent even while the
gallery is in use.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .db import DB_FILE

AVATARS_DIR = "avatars"            # common.AVATARS_DIR; common needs Flask, this must not
SETTINGS_FILE = "settings.json"

log = logging.getLogger(__name__)

KEEP = 7
RESTORE_NOTE = """Ninaivu Lite backup
===================

This holds your people, albums, favourites, share links, settings and
profile pictures. Your photos are not in here: they stay in your own folders.

To restore:
1. Stop Ninaivu Lite.
2. Run:  ninaivu-lite --restore <this zip>
   (from a copy of the source: python -m ninaivu_lite --restore <this zip>;
   add --data <folder> if your data folder is not the usual one).
   What was there before is moved to a "before-restore" folder, not deleted.
3. Start Ninaivu Lite again.

By hand instead: delete ninaivu-lite.db-wal and ninaivu-lite.db-shm from the
data folder first (left there, they bring back what you are undoing), then
copy ninaivu-lite.db, settings.json and the avatars folder into it.
"""


def snapshot(data_dir: str | Path, dest: Path) -> Path:
    """A consistent copy of the index at *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    source = sqlite3.connect(str(Path(data_dir) / DB_FILE), timeout=30)
    try:
        target = sqlite3.connect(str(tmp))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    tmp.replace(dest)
    return dest


def write_bundle(data_dir: str | Path, out) -> None:
    """The recovery zip into *out* (a path or a binary stream): a consistent
    copy of the index, the settings, every profile picture, and how to put
    them back."""
    data = Path(data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        copy = snapshot(data, Path(tmp) / DB_FILE)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, DB_FILE)
            settings = data / SETTINGS_FILE
            if settings.is_file():
                z.write(settings, SETTINGS_FILE)
            avatars = data / AVATARS_DIR
            if avatars.is_dir():
                for picture in sorted(avatars.glob("*.jpg")):
                    if picture.is_file():
                        z.write(picture, f"{AVATARS_DIR}/{picture.name}")
            z.writestr("README.txt", RESTORE_NOTE)


class RestoreError(Exception):
    """A backup that cannot be put back, with why."""


#: What a recovery zip may hold; anything else in one is left out.
_AVATAR_NAME = re.compile(r"^avatars/[A-Za-z0-9_.-]+\.jpg$")


def restore(data_dir: str | Path, bundle: str | Path) -> Path:
    """Put a recovery zip back into *data_dir* (Ninaivu Lite must be stopped).

    The index in it is checked before anything is touched: it must be a
    whole SQLite file from this version or an older one. The index, its
    ``-wal`` and ``-shm`` companions (which SQLite would otherwise replay
    over the restored file), the settings and the profile pictures that
    were there are moved aside into ``before-restore-<time>/``, never
    deleted. Returns that folder."""
    from .db import MIGRATIONS
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(bundle)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"{bundle} is not a Ninaivu Lite backup zip ({exc}).") from exc
    with archive, tempfile.TemporaryDirectory(dir=data) as tmp:
        names = set(archive.namelist())
        if DB_FILE not in names:
            raise RestoreError(f"{bundle} has no {DB_FILE} in it.")
        staged = Path(tmp) / DB_FILE
        with archive.open(DB_FILE) as src, open(staged, "wb") as out:
            shutil.copyfileobj(src, out)
        try:
            check = sqlite3.connect(str(staged))
            try:
                ok = check.execute("PRAGMA integrity_check").fetchone()[0]
                version = check.execute("PRAGMA user_version").fetchone()[0]
            finally:
                check.close()
        except sqlite3.Error as exc:
            raise RestoreError(f"The index in {bundle} cannot be read ({exc}).") from exc
        if ok != "ok":
            raise RestoreError(f"The index in {bundle} is damaged ({ok}).")
        if version > len(MIGRATIONS):
            raise RestoreError(f"{bundle} was made by a newer Ninaivu Lite; update this one "
                               "first.")
        settings = Path(tmp) / SETTINGS_FILE
        if SETTINGS_FILE in names:
            with archive.open(SETTINGS_FILE) as src, open(settings, "wb") as out:
                shutil.copyfileobj(src, out)
        avatars = Path(tmp) / AVATARS_DIR
        avatars.mkdir()
        for name in sorted(n for n in names if _AVATAR_NAME.match(n)):
            with archive.open(name) as src, open(avatars / name.split("/", 1)[1], "wb") as out:
                shutil.copyfileobj(src, out)

        aside = data / f"before-restore-{datetime.now():%Y-%m-%d-%H%M%S}"
        aside.mkdir()
        moving = [DB_FILE, f"{DB_FILE}-wal", f"{DB_FILE}-shm", f"{DB_FILE}-journal"]
        if settings.is_file():
            moving.append(SETTINGS_FILE)
        moving.append(AVATARS_DIR)
        for name in moving:
            if (data / name).exists():
                (data / name).replace(aside / name)
        staged.replace(data / DB_FILE)
        if settings.is_file():
            settings.replace(data / SETTINGS_FILE)
        avatars.replace(data / AVATARS_DIR)
    log.info("restored %s into %s; what was there is in %s", bundle, data, aside)
    return aside


def daily(data_dir: str | Path) -> Path | None:
    """Today's recovery zip, if there is none yet; older ones beyond
    :data:`KEEP` removed (copies of the index alone, from before, too)."""
    folder = Path(data_dir) / "backups"
    today = folder / f"ninaivu-lite-{datetime.now():%Y-%m-%d}.zip"
    if today.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    tmp = today.with_name(today.name + ".tmp")
    write_bundle(data_dir, tmp)
    tmp.replace(today)
    for old in _kept(folder)[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    log.info("index, settings and profile pictures backed up to %s", today)
    return today


def _kept(folder: Path) -> list[Path]:
    """The daily copies, newest first (by the date in the name)."""
    found = [*folder.glob("ninaivu-lite-*.zip"), *folder.glob("ninaivu-lite-*.db")]
    return sorted(found, key=lambda p: (p.stem, p.suffix == ".zip"), reverse=True)


def listing(data_dir: str | Path) -> list[dict]:
    folder = Path(data_dir) / "backups"
    return [{"name": p.name, "size": p.stat().st_size, "at": p.stat().st_mtime}
            for p in _kept(folder)]


def download(data_dir: str | Path) -> bytes:
    """The recovery zip, for the admin to keep elsewhere."""
    buffer = io.BytesIO()
    write_bundle(data_dir, buffer)
    return buffer.getvalue()


VIEWS_KEEP_DAYS = 30


def prune_views(data_dir: str | Path, days: int = VIEWS_KEEP_DAYS) -> int:
    """Remove viewing copies (the metadata-free JPEGs made for guests and share
    links) not made in the last *days* days. They live only in the data folder
    and are made again when needed; photographs are never touched."""
    root = Path(data_dir) / "views"
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in root.glob("*/*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class Keeper:
    """Takes the daily copy in the background."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = data_dir
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="backups", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Not at the very first second: let the first scan get going.
        if self._stop.wait(120):
            return
        while True:
            try:
                daily(self.data_dir)
            except Exception:  # noqa: BLE001 — a failed copy must not stop anything
                log.exception("daily backup failed")
            try:
                prune_views(self.data_dir)
            except Exception:  # noqa: BLE001
                log.exception("clearing old viewing copies failed")
            if self._stop.wait(3600):     # look again hourly; one copy per day
                return
