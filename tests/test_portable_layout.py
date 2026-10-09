"""The portable zip's clean top: only Ninaivu Lite.exe and its log, the program in app\\."""
import logging
from pathlib import Path

import pytest

from ninaivu_lite import __main__ as cli
from ninaivu_lite import control

ROOT = Path(__file__).resolve().parent.parent
PORTABLE = ROOT / "installers" / "windows" / "portable"


def extracted(tmp_path: Path, exe: bool = True) -> Path:
    """<tmp>/Ninaivu Lite/app/pkgs, as the zip extracts; the pkgs folder is returned."""
    home = tmp_path / "Ninaivu Lite"
    pkgs = home / "app" / "pkgs"
    pkgs.mkdir(parents=True)
    if exe:
        (home / control.PORTABLE_EXE).write_bytes(b"MZ")
    return pkgs


def test_the_portable_home_is_the_folder_with_the_exe(tmp_path):
    pkgs = extracted(tmp_path)
    assert control.install_kind(pkgs) == "portable"
    assert control.portable_home(pkgs) == tmp_path / "Ninaivu Lite"


def test_other_copies_have_no_portable_home(tmp_path):
    assert control.portable_home(extracted(tmp_path, exe=False)) is None
    installed = tmp_path / "Program Files" / "Ninaivu Lite"
    (installed / "pkgs").mkdir(parents=True)
    (installed / "uninstall.exe").write_bytes(b"")
    assert control.portable_home(installed / "pkgs") is None
    old_zip = tmp_path / "old" / "Ninaivu Lite" / "pkgs"       # before the app\ folder
    old_zip.mkdir(parents=True)
    assert control.portable_home(old_zip) is None
    assert control.portable_home(ROOT) is None


def test_the_log_sits_beside_the_exe_for_its_own_data_folder(tmp_path):
    pkgs = extracted(tmp_path)
    home = tmp_path / "Ninaivu Lite"
    assert control.log_path(home / "data", pkgs) == home / "Ninaivu Lite.log"
    # Another data folder keeps its log to itself.
    other = tmp_path / "elsewhere"
    assert control.log_path(other, pkgs) == other / "logs" / "ninaivu-lite.log"
    # Every other copy, as before.
    assert control.log_path(tmp_path / "d", ROOT) == tmp_path / "d" / "logs" / "ninaivu-lite.log"


@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            handler.close()
            root.removeHandler(handler)


def test_the_server_writes_the_top_log_and_keeps_older_ones_in_logs(tmp_path, monkeypatch,
                                                                    clean_root_logger):
    pkgs = extracted(tmp_path)
    home = tmp_path / "Ninaivu Lite"
    monkeypatch.setattr(control, "ROOT", pkgs)
    cli.setup_logging(str(home / "data"))
    logging.getLogger("portable-test").warning("written beside the exe")
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "baseFilename", None) == str(home / "Ninaivu Lite.log"))
    handler.flush()
    assert "written beside the exe" in (home / "Ninaivu Lite.log").read_text(encoding="utf-8")
    assert not (home / "data" / "logs" / "ninaivu-lite.log").exists()
    handler.doRollover()
    assert (home / "data" / "logs" / "Ninaivu Lite.log.1").is_file()
    assert sorted(p.name for p in home.iterdir()) == ["Ninaivu Lite.exe", "Ninaivu Lite.log",
                                                       "app", "data"]
    assert control.Controller(str(home / "data")).log_file == home / "Ninaivu Lite.log"


def test_the_launcher_opens_the_panel_on_the_data_folder_beside_it():
    nsi = (PORTABLE / "launcher.nsi").read_text(encoding="utf-8")
    assert "Unicode true" in nsi and "RequestExecutionLevel user" in nsi
    assert ('Exec \'"$EXEDIR\\app\\Python\\pythonw.exe" -m ninaivu_lite.panel '
            '--data "$EXEDIR\\data"\'') in nsi
    assert not (PORTABLE / "Ninaivu Lite Control Panel.vbs").exists()


def test_the_zip_is_built_with_only_the_exe_at_its_top():
    script = (PORTABLE / "build-portable.ps1").read_text(encoding="utf-8")
    assert 'Move-Item $Source (Join-Path $top "app")' in script
    assert 'Where-Object Name -ne "Ninaivu Lite.exe"' in script
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "build-portable.ps1" in workflow
    assert '"app|data|Ninaivu Lite.exe|Ninaivu Lite.log"' in workflow
    readme = (PORTABLE / "README-PORTABLE.txt").read_text(encoding="utf-8")
    assert "Ninaivu Lite Control Panel.vbs" not in readme
    assert 'double-click "Ninaivu Lite.exe"' in readme
