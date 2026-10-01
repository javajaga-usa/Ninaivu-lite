"""Copies of what Ninaivu Lite cannot remake: the index (people, albums,
favourites, share links, who-sees-what) and the settings.

Photos are never copied: they stay in your folders, and thumbnails can always
be made again. A copy is taken once a day into ``backups/`` in the data folder
(the last seven kept), and the admin page offers one as a download to keep
somewhere else.

Each copy uses SQLite's own backup, so it is consistent even while the
gallery is in use.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .db import DB_FILE

log = logging.getLogger(__name__)

KEEP = 7
RESTORE_NOTE = """Ninaivu Lite backup
===================

This holds your people, albums, favourites, share links and settings.
Your photos are not in here: they stay in your own folders.

To restore:
1. Stop Ninaivu Lite.
2. Copy ninaivu-lite.db and settings.json into the data folder
   (it is shown on the Admin page), replacing the files there.
3. Start Ninaivu Lite again.
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


def daily(data_dir: str | Path) -> Path | None:
    """Today's copy, if there is none yet; older ones beyond :data:`KEEP` removed."""
    folder = Path(data_dir) / "backups"
    today = folder / f"ninaivu-lite-{datetime.now():%Y-%m-%d}.db"
    if today.exists():
        return None
    made = snapshot(data_dir, today)
    for old in sorted(folder.glob("ninaivu-lite-*.db"))[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    log.info("index backed up to %s", made)
    return made


def listing(data_dir: str | Path) -> list[dict]:
    folder = Path(data_dir) / "backups"
    return [{"name": p.name, "size": p.stat().st_size, "at": p.stat().st_mtime}
            for p in sorted(folder.glob("ninaivu-lite-*.db"), reverse=True)]


def download(data_dir: str | Path) -> bytes:
    """A zip of the index and settings, for the admin to keep elsewhere."""
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        copy = snapshot(data_dir, Path(tmp) / DB_FILE)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, DB_FILE)
            settings = Path(data_dir) / "settings.json"
            if settings.exists():
                z.write(settings, "settings.json")
            z.writestr("README.txt", RESTORE_NOTE)
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
