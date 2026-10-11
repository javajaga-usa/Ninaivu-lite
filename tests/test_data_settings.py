"""Data safety (2026-10-11-b audit): the settings file is only set aside
when it is really damaged."""

from __future__ import annotations

import json
from pathlib import Path

from ninaivu_lite import config
from ninaivu_lite.config import Config


def test_settings_busy_for_a_moment_are_not_taken_for_damage(tmp_path, monkeypatch):
    library = tmp_path / "Photos"
    library.mkdir()
    Config(data_dir=str(tmp_path), folders=[str(library)], active=str(library), language="ta",
           house_name="Veedu", allow_internet=True, first_day_done=True).save()
    real, refused = Path.read_text, []

    def busy(self, *args, **kwargs):
        # Windows: "being used by another process" (an antivirus, a backup).
        if self.name == config.SETTINGS_FILE and len(refused) < 2:
            refused.append(self)
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(config.time, "sleep", lambda _s: None)
    monkeypatch.setattr(Path, "read_text", busy)
    loaded = Config.load(tmp_path)
    monkeypatch.setattr(Path, "read_text", real)
    assert refused
    assert (loaded.language, loaded.house_name, loaded.allow_internet) == ("ta", "Veedu", True)
    assert not (tmp_path / "settings.json.damaged").exists()
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["language"] == "ta" and on_disk["house_name"] == "Veedu"
