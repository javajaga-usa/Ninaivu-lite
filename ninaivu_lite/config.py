"""Settings: one ``settings.json`` in the data folder, sensible defaults for the rest.

The data folder holds what Ninaivu Lite makes (settings, the index, thumbnails,
backups, logs) and never sits inside a photo folder. Photo folders are only
ever read.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PORT = 8080
SETTINGS_FILE = "settings.json"
DEFAULT_HOUSE_NAME = "Ninaivu"


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
    first_day_done: bool = False
    # The importer's last job, so the console's Import page opens as it was left.
    import_sources: list[str] = field(default_factory=list)
    import_destination: str = ""
    import_kinds: list[str] = field(default_factory=lambda: ["image", "video"])
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT

    SAVED = ("folders", "active", "house_name", "open_browsing", "language", "watch",
             "first_day_done", "import_sources", "import_destination", "import_kinds")

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
            return cfg
        except (OSError, ValueError):
            # A damaged file must not stop the server: start on defaults and
            # keep the damaged copy for whoever wants to look at it.
            _set_aside(cfg.settings_path)
            return cfg
        if not isinstance(raw, dict):
            return cfg
        defaults = cls(data_dir=cfg.data_dir)
        for name in cls.SAVED:
            if name in raw and isinstance(raw[name], type(getattr(defaults, name))):
                setattr(cfg, name, raw[name])
        cfg.folders = [str(f) for f in cfg.folders if isinstance(f, str) and f.strip()]
        cfg.import_sources = [str(f) for f in cfg.import_sources
                              if isinstance(f, str) and f.strip()]
        cfg.import_kinds = [k for k in ("image", "video") if k in cfg.import_kinds]
        if cfg.language not in ("en", "ta"):
            cfg.language = "en"
        cfg.house_name = clean_house_name(cfg.house_name)
        return cfg

    def save(self) -> None:
        """Write atomically: a power cut mid-save leaves the old file, never half a file."""
        path = self.settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
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


def _set_aside(path: Path) -> None:
    try:
        os.replace(path, path.with_name(path.name + ".damaged"))
    except OSError:
        pass
