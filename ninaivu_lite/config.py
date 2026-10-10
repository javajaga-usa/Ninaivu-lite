"""Settings: one ``settings.json`` in the data folder, sensible defaults for the rest.

The data folder holds what Ninaivu Lite makes (settings, the index, thumbnails,
backups, logs) and never sits inside a photo folder. Photo folders are only
ever read.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PORT = 8080
SETTINGS_FILE = "settings.json"
DEFAULT_HOUSE_NAME = "Ninaivu"
INDEX_FILE = "ninaivu-lite.db"      # db.DB_FILE; db is not imported here to keep this module light

log = logging.getLogger(__name__)


def default_data_dir() -> Path:
    """Where this user's Ninaivu Lite data lives, by platform convention."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Ninaivu-lite"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Ninaivu-lite"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ninaivu-lite"


def clean_house_name(value: str) -> str:
    return " ".join(str(value or "").split())[:40]


@dataclass
class Config:
    """What the household chose. Runtime values (data_dir, host, port from the
    command line) are not written back to the file."""

    data_dir: str = field(default_factory=lambda: str(default_data_dir()))
    folders: list[str] = field(default_factory=list)
    active: str = ""                 # the default folder: where the picker and the tree start
    house_name: str = ""             # "" means "Ninaivu"
    open_browsing: bool = True       # "Just looking" without signing in: Public photos only
    language: str = "en"             # for people and devices that have not chosen
    watch: bool = True               # look for new photos by itself (every 30 minutes)
    # A video whose metadata (a phone's GPS location among it) cannot be
    # removed, for want of ffmpeg, is refused to guests and share links
    # unless the household chooses to send such videos as they are.
    video_originals: bool = False
    #: Extra names this computer is reached by (a reverse proxy, a name of the
    #: household's own); its addresses and network name always work.
    allowed_hosts: list[str] = field(default_factory=list)
    #: Requests from outside the home network (a forwarded port, a tunnel)
    #: are refused unless this is set on purpose.
    allow_internet: bool = False
    first_day_done: bool = False
    # The importer's last job, so the console's Import page opens as it was left.
    import_sources: list[str] = field(default_factory=list)
    import_destination: str = ""
    import_kinds: list[str] = field(default_factory=lambda: ["image", "video"])
    #: Drives and phones whose notice was answered with Don't ask again
    #: (their ids, see drives.Drive): never offered again, until Ask again
    #: in Settings clears the list.
    drives_never_ask: list[str] = field(default_factory=list)
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    #: Set when the settings file was missing or damaged and the library
    #: folders were taken back from the index instead (never saved).
    recovered: bool = False
    #: Settings were missing or damaged and the index could not be read to
    #: recover the folders, so the folder list is unknown, not empty: the
    #: server must not start on it (a scan would remove every folder).
    folders_unknown: bool = False

    SAVED = ("folders", "active", "house_name", "open_browsing", "language", "watch",
             "video_originals", "allowed_hosts", "allow_internet",
             "first_day_done", "import_sources", "import_destination", "import_kinds",
             "drives_never_ask")

    @property
    def settings_path(self) -> Path:
        return Path(self.data_dir) / SETTINGS_FILE

    @property
    def house_name_effective(self) -> str:
        return self.house_name or DEFAULT_HOUSE_NAME

    @property
    def active_folder(self) -> str:
        if self.active in self.folders:
            return self.active
        return self.folders[0] if self.folders else ""

    @classmethod
    def load(cls, data_dir: str | os.PathLike | None = None) -> Config:
        cfg = cls() if data_dir is None else cls(data_dir=str(data_dir))
        try:
            raw = json.loads(cfg.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cfg._recover("missing")
        except (OSError, ValueError):
            # A damaged file must not stop the server: start on defaults and
            # keep the damaged copy for whoever wants to look at it.
            _set_aside(cfg.settings_path)
            return cfg._recover("damaged")
        if not isinstance(raw, dict):
            _set_aside(cfg.settings_path)
            return cfg._recover("damaged")
        defaults = cls(data_dir=cfg.data_dir)
        for name in cls.SAVED:
            if name in raw and isinstance(raw[name], type(getattr(defaults, name))):
                setattr(cfg, name, raw[name])
        cfg.folders = [str(f) for f in cfg.folders if isinstance(f, str) and f.strip()]
        cfg.import_sources = [str(f) for f in cfg.import_sources
                              if isinstance(f, str) and f.strip()]
        cfg.drives_never_ask = [str(d) for d in cfg.drives_never_ask
                                 if isinstance(d, str) and d.strip()]
        cfg.allowed_hosts = [str(h) for h in cfg.allowed_hosts if isinstance(h, str) and h.strip()]
        cfg.import_kinds = [k for k in ("image", "video") if k in cfg.import_kinds]
        if cfg.language not in ("en", "ta"):
            cfg.language = "en"
        cfg.house_name = clean_house_name(cfg.house_name)
        if not isinstance(raw.get("folders"), list):
            # No folder list at all is damage, not a choice to have none.
            return cfg._recover("without a folder list")
        return cfg

    def _recover(self, why: str) -> Config:
        """Settings that are missing or damaged say nothing about the library
        folders, and the scan takes an empty list to mean every folder was
        removed, which would delete their favourites, albums and visibility
        from the index. So the folders come back from the index itself, and
        the recovered settings are written out for the next start."""
        try:
            folders = index_folders(self.data_dir)
        except IndexUnreadable as exc:
            log.error("settings %s and the index cannot be read (%s); the library folders "
                      "are unknown, so nothing will be scanned", why, exc)
            self.folders_unknown = True
            return self
        if not folders:
            return self                    # a fresh data folder: nothing to lose
        log.warning("settings %s; %d library folder(s) recovered from the index", why,
                    len(folders))
        self.folders = folders
        self.active = self.active if self.active in folders else folders[0]
        self.first_day_done = True
        self.recovered = True
        try:
            self.save()
        except OSError:
            log.warning("could not write the recovered settings", exc_info=True)
        return self

    def update(self, **changes) -> None:
        """Change settings only once they are written: a save that fails (a
        full disk) leaves both the file and what the server does as they
        were, instead of a choice that holds until the next restart."""
        trial = copy.deepcopy(self)
        for key, value in changes.items():
            setattr(trial, key, value)
        trial.save()
        for key, value in changes.items():
            setattr(self, key, copy.deepcopy(value))

    def save(self) -> None:
        """Write atomically: a power cut mid-save leaves the old file, never half a file."""
        if self.folders_unknown:
            # Writing now would record "no library folders" as a choice.
            log.warning("settings not saved: the library folders are not known")
            return
        path = self.settings_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k in self.SAVED}
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class IndexUnreadable(Exception):
    """The index is there but could not be read."""


def index_folders(data_dir: str | os.PathLike) -> list[str]:
    """The library folders the index holds. Only read, never changed. An
    index that is not there holds none; one that is there but cannot be read
    raises :class:`IndexUnreadable`, because "no folders" would empty the
    library."""
    path = Path(data_dir) / INDEX_FILE
    if not path.is_file():
        return []
    try:
        # A plain path, not a file: URI: a URI cannot name a network share
        # (file://server/...), and a read-only query changes nothing.
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(folders)")}
            # Folders taken out of the library (index version 9) stay out.
            rows = conn.execute(
                "SELECT path FROM folders "
                + ("WHERE detached_at IS NULL " if "detached_at" in columns else "")
                + "ORDER BY id").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise IndexUnreadable(str(exc)) from exc
    return [str(r[0]) for r in rows if isinstance(r[0], str) and r[0].strip()]


def _set_aside(path: Path) -> None:
    """Keep a damaged settings file as ``settings.json.damaged``; one kept
    from an earlier time moves aside under its own date, never overwritten."""
    aside = path.with_name(path.name + ".damaged")
    try:
        if aside.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(aside.stat().st_mtime))
            older, n = aside.with_name(f"{aside.name}-{stamp}"), 2
            while older.exists():
                older, n = aside.with_name(f"{aside.name}-{stamp}-{n}"), n + 1
            os.replace(aside, older)
        os.replace(path, aside)
    except OSError:
        pass


def make_private(data_dir: str | os.PathLike) -> None:
    """The data folder holds the index (share links, PIN hashes), thumbnails
    of Hidden photos and the backups: on Linux and macOS, only its owner may
    look inside. Made so when new; an existing one is tightened only when it
    is clearly this program's (it holds the index or the settings)."""
    folder = Path(data_dir)
    if os.name == "nt":
        return                          # a per-user folder under LOCALAPPDATA already
    try:
        if not folder.exists():
            folder.mkdir(mode=0o700, parents=True)
        elif ((folder / INDEX_FILE).exists() or (folder / SETTINGS_FILE).exists()) \
                and folder.stat().st_mode & 0o077:
            os.chmod(folder, 0o700)
    except OSError as exc:
        log.warning("could not make %s private: %s", folder, exc)
