"""Updates are put in by hand, safely (2026-10-08): nothing in Ninaivu Lite
asks GitHub (or anywhere) about newer versions; a newer setup file keeps the
library's data; and every installer says to stop the server first, and checks.
What only an installer can do is checked by running install.sh with stand-in
commands, or in the NSIS script's text, as the packaging tests do."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ninaivu_lite import backups, db, panel

from test_audit_fixes_2026_10_07_backup_install import (INSTALL, read, stand_in_payload,
                                                        user_install_env)

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ninaivu_lite"


# --- no update check, anywhere ------------------------------------------------------------


def test_nothing_asks_about_newer_versions():
    assert not (PACKAGE / "updates.py").exists()
    looked = re.compile(r"api\.github\.com|releases/latest|update-check\.json|latest release",
                        re.IGNORECASE)
    for path in [*PACKAGE.rglob("*.py"), *PACKAGE.rglob("*.js"), *PACKAGE.rglob("*.html"),
                 *(ROOT / "launcher").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        found = [line for line in text.splitlines()
                 if looked.search(line) and "update-check.json" not in line]
        assert not found, f"{path.relative_to(ROOT)}: {found}"
    # The panel opens nothing on the internet: only this computer's own pages.
    source = (PACKAGE / "panel.py").read_text(encoding="utf-8")
    assert "github" not in source.lower() and "urllib.request" not in source


def test_the_panel_has_no_update_card_or_button():
    """The household asked for no update section at all (it showed on a
    first install as if an update were under way); the guides say how."""
    source = (PACKAGE / "panel.py").read_text(encoding="utf-8")
    assert "UPDATING" not in source and "Get ready to update" not in source


def test_the_panel_points_out_a_server_left_running_from_before_an_update():
    said = []
    view = SimpleNamespace(stale_version=None, notice=SimpleNamespace(set=said.append))
    view.say_if_stale = lambda version: panel.Panel.say_if_stale(view, version)
    panel.Panel.say_if_stale(view, "1.0.0")
    assert said and "1.0.0 is still running from before the update" in said[0]
    assert "Restart" in said[0]
    panel.Panel.say_if_stale(view, "1.0.0")                 # said once
    assert len(said) == 1
    panel.Panel.say_if_stale(view, panel.__version__)       # the same version: nothing
    assert len(said) == 1 and view.stale_version is None


# --- the index: carried forward, with a copy first ------------------------------------------


def older_index(data: Path, version: int) -> None:
    """An index as an earlier Ninaivu Lite left it: the first *version* steps only."""
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data / db.DB_FILE))
    for script in db.MIGRATIONS[:version]:
        for statement in db._statements(script):
            conn.execute(statement)
    conn.execute("INSERT INTO folders (path) VALUES ('/photos/family')")
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()


def test_an_older_index_is_copied_then_carried_forward(tmp_path):
    data = tmp_path / "data"
    older_index(data, 1)
    conn = db.connect(data)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
        assert conn.execute("SELECT path FROM folders").fetchone()[0] == "/photos/family"
    finally:
        conn.close()
    copies = list((data / "backups").glob("before-update-from-index-1-*.zip"))
    assert len(copies) == 1
    with zipfile.ZipFile(copies[0]) as z:
        z.extract(db.DB_FILE, tmp_path / "copy")
    old = sqlite3.connect(str(tmp_path / "copy" / db.DB_FILE))
    try:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1   # as it was
    finally:
        old.close()
    # Started again: already up to date, so no second copy.
    db.connect(data).close()
    assert len(list((data / "backups").glob("before-*.zip"))) == 1


def test_a_new_index_needs_no_copy(tmp_path):
    db.connect(tmp_path / "data").close()
    assert not list((tmp_path / "data" / "backups").glob("before-*.zip"))


def test_the_newest_copies_taken_before_a_change_are_kept_whatever_they_were_for(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    # Five old copies from before an update, named after it ("u" sorts after "r").
    for day in range(1, 6):
        (folder / f"before-update-from-index-8-2026-01-0{day}-120000.zip").write_bytes(b"")
    data = tmp_path
    db.connect(data).close()
    newest = backups.before_change(data, "removing-person")
    assert newest is not None and newest.exists()
    left = sorted(p.name for p in folder.glob("before-*.zip"))
    assert newest.name in left and len(left) == backups.KEEP_BEFORE
    assert "before-update-from-index-8-2026-01-01-120000.zip" not in left


# --- the Linux installer: stops first, and never replaces a running one ---------------------


def test_linux_upgrade_says_to_stop_first_and_stops_it(tmp_path):
    env, home, calls = user_install_env(tmp_path, WAS_RUNNING="1")
    payload = stand_in_payload(tmp_path)
    prefix = home / ".local" / "lib" / "ninaivu-lite"
    (prefix / "python").mkdir(parents=True)
    import shutil
    shutil.copytree(payload / "python" / "bin", prefix / "python" / "bin")   # the earlier one
    (prefix / "VERSION").write_text("1.8.0\n", encoding="utf-8")
    data = home / ".local" / "share" / "ninaivu-lite"
    data.mkdir(parents=True)
    (data / "settings.json").write_text('{"house_name": "Ours"}', encoding="utf-8")
    done = subprocess.run(["sh", str(INSTALL), str(payload)], env=env, check=True,
                          capture_output=True, text=True)
    assert "Ninaivu Lite 1.8.0 is running. It is best stopped before an update" in done.stdout
    assert (prefix / "VERSION").read_text().strip() == "1.6.1"
    assert (data / "settings.json").read_text() == '{"house_name": "Ours"}'


def test_linux_upgrade_leaves_a_server_it_cannot_stop_alone(tmp_path):
    env, home, calls = user_install_env(tmp_path, WAS_RUNNING="1", STUCK="1")
    payload = stand_in_payload(tmp_path)
    prefix = home / ".local" / "lib" / "ninaivu-lite"
    (prefix / "python").mkdir(parents=True)
    import shutil
    shutil.copytree(payload / "python" / "bin", prefix / "python" / "bin")
    (prefix / "VERSION").write_text("1.8.0\n", encoding="utf-8")
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "still running, so it was not updated. Nothing was changed." in done.stderr
    assert (prefix / "VERSION").read_text().strip() == "1.8.0"
    assert not (prefix / "python.new").exists()


# --- the Windows installer: says so before it stops anything ----------------------------------


def test_windows_upgrade_advises_stopping_before_it_stops_anything():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    block = nsi[nsi.index("[% block install_pkgs %]"):]
    block = block[:block.index("[% endblock %]")]
    advice = block.index("It is best to stop it before updating")
    assert block.index("-m ninaivu_lite.control --running") < advice
    assert advice < block.index("!insertmacro WaitUntilNotInUse") < block.index("RMDir /r")
    assert "IfSilent nl_not_running" in block and "Abort" in block
    assert "kept either way" in block


def test_every_way_to_update_says_to_stop_first():
    for path, words in (
            ("README.md", "stop Ninaivu Lite"),
            ("installers/windows/portable/README-PORTABLE.txt", "press Stop"),
            ("installers/README.md", "Stop"),
            ("docs/guide/en.html", "stop Ninaivu Lite"),
            (".github/workflows/release.yml", "Stop Ninaivu Lite first")):
        text = " ".join(read(path).split())
        assert re.search(r"updat|upgrad", text, re.IGNORECASE), path
        assert words.lower() in text.lower(), path


@pytest.mark.parametrize("path", ["installers/macos/build.sh"])
def test_the_mac_disk_image_carries_the_update_note(path):
    script = read(path)
    assert "Before updating - read me.txt" in script
