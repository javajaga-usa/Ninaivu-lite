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
import zlib
from datetime import datetime
from pathlib import Path

from . import parallel
from .db import DB_FILE

AVATARS_DIR = "avatars"            # common.AVATARS_DIR; common needs Flask, this must not
SETTINGS_FILE = "settings.json"

log = logging.getLogger(__name__)

KEEP = 7            # daily copies
KEEP_WEEKLY = 4     # and, beyond those, the newest of each of the last four weeks
KEEP_MONTHLY = 3    # and of each of the last three months
KEEP_BEFORE = 5     # copies taken just before a folder or a person is removed
FAILURE_FILE = "last-failure.txt"
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
    whole Ninaivu Lite index from this version or an older one. The index,
    its ``-wal`` and ``-shm`` companions (which SQLite would otherwise replay
    over the restored file), the settings and the profile pictures that
    were there are moved aside into ``before-restore-<time>/``, never
    deleted. A bare index (the daily ``.db`` copies older versions made) is
    accepted too. Returns that folder.

    Sessions are not brought back: a phone signed out after the backup was
    taken stays signed out. Thumbnails and viewing copies are made again,
    since the restored index may give their ids to other photographs."""
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    if _is_sqlite(bundle):
        return _restore_staged(data, bundle, lambda staged: shutil.copyfile(bundle, staged),
                               names=set(), archive=None)
    try:
        archive = zipfile.ZipFile(bundle)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"{bundle} is not a Ninaivu Lite backup zip ({exc}).") from exc
    with archive:
        names = set(archive.namelist())
        if DB_FILE not in names:
            raise RestoreError(f"{bundle} has no {DB_FILE} in it.")
        wanted = [DB_FILE, SETTINGS_FILE, *(n for n in names if _AVATAR_NAME.match(n))]
        size = sum(archive.getinfo(n).file_size for n in wanted if n in names)
        free = shutil.disk_usage(data).free
        if size > free - 64 * 1024 ** 2:
            raise RestoreError(f"{bundle} needs {size // 1024 ** 2} MB to unpack and the data "
                               f"folder's disk has {free // 1024 ** 2} MB free. Nothing was changed.")

        def extract(staged: Path) -> None:
            with archive.open(DB_FILE) as src, open(staged, "wb") as out:
                shutil.copyfileobj(src, out)
        return _restore_staged(data, bundle, extract, names=names, archive=archive)


def _is_sqlite(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


#: Tables every Ninaivu Lite index has; a file without them is something else.
_REQUIRED_TABLES = {"folders", "assets", "users", "sessions", "shares"}


def _restore_staged(data: Path, bundle, extract, *, names: set[str], archive) -> Path:
    from .db import MIGRATIONS
    with tempfile.TemporaryDirectory(dir=data) as tmp:
        staged = Path(tmp) / DB_FILE
        settings = Path(tmp) / SETTINGS_FILE
        avatars = Path(tmp) / AVATARS_DIR
        avatars.mkdir()
        try:
            extract(staged)
            if SETTINGS_FILE in names:
                with archive.open(SETTINGS_FILE) as src, open(settings, "wb") as out:
                    shutil.copyfileobj(src, out)
            for name in sorted(n for n in names if _AVATAR_NAME.match(n)):
                with archive.open(name) as src, \
                        open(avatars / name.split("/", 1)[1], "wb") as out:
                    shutil.copyfileobj(src, out)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            raise RestoreError(f"{bundle} is damaged and cannot be unpacked ({exc}). "
                               "Nothing was changed.") from exc
        try:
            check = sqlite3.connect(str(staged))
            try:
                ok = check.execute("PRAGMA integrity_check").fetchone()[0]
                version = check.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in check.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
                if ok == "ok" and version >= 1 and _REQUIRED_TABLES <= tables \
                        and version <= len(MIGRATIONS):
                    # Who was signed in when the copy was taken is not
                    # brought back; nor are thumbnails, whose files on disk
                    # may now belong to other photographs with the same ids.
                    check.execute("DELETE FROM sessions")
                    check.execute("UPDATE assets SET thumb = 0, large = 0, "
                                  "thumb_v = thumb_v + 1 WHERE thumb != 2 OR large != 0")
                    check.commit()
            finally:
                check.close()
        except sqlite3.Error as exc:
            raise RestoreError(f"The index in {bundle} cannot be read ({exc}).") from exc
        if ok != "ok":
            raise RestoreError(f"The index in {bundle} is damaged ({ok}).")
        if version > len(MIGRATIONS):
            raise RestoreError(f"{bundle} was made by a newer Ninaivu Lite; update this one "
                               "first.")
        if version < 1 or not _REQUIRED_TABLES <= tables:
            raise RestoreError(f"The index in {bundle} is empty or is not a Ninaivu Lite index. "
                               "Nothing was changed.")

        aside = data / f"before-restore-{datetime.now():%Y-%m-%d-%H%M%S}"
        aside.mkdir()
        # Without settings in the backup the folders are taken from the
        # restored index (Config recovers them), not from the settings here,
        # which may list other folders and would drop the restored ones.
        moving = [DB_FILE, f"{DB_FILE}-wal", f"{DB_FILE}-shm", f"{DB_FILE}-journal",
                  SETTINGS_FILE, AVATARS_DIR]
        for name in moving:
            if (data / name).exists():
                (data / name).replace(aside / name)
        staged.replace(data / DB_FILE)
        if settings.is_file():
            settings.replace(data / SETTINGS_FILE)
        avatars.replace(data / AVATARS_DIR)
        for cache in ("thumbs", "views"):
            if (data / cache).exists():
                gone = Path(tmp) / f"old-{cache}"
                (data / cache).replace(gone)
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
    for old in _to_prune(_kept(folder)):
        try:
            old.unlink()
        except OSError:
            pass
    log.info("index, settings and profile pictures backed up to %s", today)
    return today


def _to_prune(kept: list[Path]) -> list[Path]:
    """Beyond the last :data:`KEEP` days, one copy a week for
    :data:`KEEP_WEEKLY` weeks and one a month for :data:`KEEP_MONTHLY`
    months is kept: a week of copies taken after something went wrong (a
    folder removed, a library emptied) must not push out every good one."""
    keep: set[Path] = set(kept[:KEEP])
    # The newest copy of each week and month, counting the daily ones: a
    # week the daily copies already cover needs nothing older from it.
    weeks: dict[tuple, Path] = {}
    months: dict[str, Path] = {}
    for path in kept:
        try:
            day = datetime.strptime(path.stem[len("ninaivu-lite-"):][:10], "%Y-%m-%d")
        except ValueError:
            continue
        weeks.setdefault(day.isocalendar()[:2], path)
        months.setdefault(f"{day:%Y-%m}", path)
    keep.update(list(weeks.values())[:KEEP_WEEKLY])
    keep.update(list(months.values())[:KEEP_MONTHLY])
    return [p for p in kept if p not in keep]


def _kept(folder: Path) -> list[Path]:
    """The daily copies, newest first (by the date in the name)."""
    found = [*folder.glob("ninaivu-lite-*.zip"), *folder.glob("ninaivu-lite-*.db")]
    return sorted(found, key=lambda p: (p.stem, p.suffix == ".zip"), reverse=True)


def before_change(data_dir: str | Path, what: str) -> Path | None:
    """A copy taken just before something that forgets what the household
    decided (a library folder or a person removed), kept beside the daily
    ones as ``before-<what>-<time>.zip``. None, after logging why, if it
    could not be made: the change itself is not held up."""
    folder = Path(data_dir) / "backups"
    target = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Never over an earlier copy: two taken in the same second (two
        # people removed one after the other) get -2, -3. The name is
        # claimed first, so two at once cannot both choose it. Counted on
        # from every copy of that second, whatever it was taken before, so a
        # later copy always sorts as later, even after pruning freed a name.
        when = f"{datetime.now():%Y-%m-%d-%H%M%S}"
        stamp = f"before-{what}-{when}"
        taken = [_taken_at(p) for p in folder.glob(f"before-*-{when}*.zip")]
        first = max((int(t.rsplit("-", 1)[1]) for t in taken if t.startswith(when)),
                    default=0) + 1
        for n in range(first, first + 1000):
            target = folder / (f"{stamp}.zip" if n == 1 else f"{stamp}-{n}.zip")
            try:
                with open(target, "xb"):
                    break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(target)
        tmp = target.with_name(target.name + ".tmp")
        write_bundle(data_dir, tmp)
        tmp.replace(target)
    except Exception:  # noqa: BLE001 — a missing copy must not stop the admin
        log.exception("could not take a backup before %s", what)
        if target is not None:
            try:
                if target.stat().st_size == 0:
                    target.unlink()
            except OSError:
                pass
        return None
    # Newest first by when they were taken, whatever they were taken before
    # (by name, every "before-update" copy would outlive newer "before-person" ones).
    for old in sorted(folder.glob("before-*.zip"), key=_taken_at, reverse=True)[KEEP_BEFORE:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


_TAKEN = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{6})(?:-(\d{1,3}))?$")


def _taken_at(path: Path) -> str:
    """``before-<what>-YYYY-mm-dd-HHMMSS[-n].zip`` → its time (and n), for sorting."""
    found = _TAKEN.search(path.stem)
    if not found:
        return path.stem[-17:]
    return f"{found.group(1)}-{int(found.group(2) or 1):03d}"


def listing(data_dir: str | Path) -> list[dict]:
    """Every copy kept in ``backups/``, newest first, for the console."""
    folder = Path(data_dir) / "backups"
    found = [*_kept(folder), *folder.glob("before-*.zip")] if folder.is_dir() else []
    out = []
    for p in found:
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"name": p.name, "size": st.st_size, "at": st.st_mtime,
                    "before": p.name.startswith("before-")})
    return sorted(out, key=lambda item: item["at"], reverse=True)


def last_failure(data_dir: str | Path) -> dict | None:
    """When and why the last daily copy failed, while no copy has been made since."""
    path = Path(data_dir) / "backups" / FAILURE_FILE
    try:
        st = path.stat()
        return {"at": st.st_mtime, "why": path.read_text(encoding="utf-8")[:300]}
    except (OSError, ValueError):
        return None


def _note_failure(data_dir: str | Path, exc: BaseException | None) -> None:
    path = Path(data_dir) / "backups" / FAILURE_FILE
    try:
        if exc is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
    except OSError:
        pass


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
        parallel.background()
        # Not at the very first second: let the first scan get going.
        if self._stop.wait(120):
            return
        while True:
            try:
                daily(self.data_dir)
                _note_failure(self.data_dir, None)
            except Exception as exc:  # noqa: BLE001 — a failed copy must not stop anything
                log.exception("daily backup failed")
                _note_failure(self.data_dir, exc)    # shown on the console
            try:
                prune_views(self.data_dir)
            except Exception:  # noqa: BLE001
                log.exception("clearing old viewing copies failed")
            if self._stop.wait(3600):     # look again hourly; one copy per day
                return
