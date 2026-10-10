"""Install, upgrade and Control Panel fixes from the 2026-10-10 audit (A165-A173).

What only Windows can run (the NSIS installer) and what only GitHub runs (the
release workflow) is checked in their text; the Linux installer is run with
the stand-in payload and commands the packaging tests use."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ninaivu_lite import __main__ as cli
from ninaivu_lite import control

from test_audit_fixes_2026_10_07_backup_install import (INSTALL, read, stand_in_payload,
                                                        user_install_env)

ROOT = Path(__file__).resolve().parent.parent


def nsi_block(nsi: str, start: str, end: str = "SectionEnd") -> str:
    part = nsi[nsi.index(start):]
    return part[:part.index(end)]


# --- A165: what the Windows installer opens is not elevated ---------------------------------


def test_a165_the_control_panel_is_opened_through_explorer():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    function = nsi_block(nsi, "Function OpenControlPanel", "FunctionEnd")
    explorer = function.index('Exec \'"$WINDIR\\explorer.exe" "$SMPROGRAMS\\[[scname]].lnk"\'')
    # The direct start is only for when the shortcut is not there.
    assert explorer < function.index("nl_open_direct:")
    assert 'IfFileExists "$SMPROGRAMS\\[[scname]].lnk" 0 nl_open_direct' in function


def test_a165_the_restart_after_an_upgrade_runs_as_the_person():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    section = nsi_block(nsi, 'Section "-Start again after an upgrade"')
    assert "IfSilent nl_restart_direct" in section
    assert "FileWriteUTF16LE /BOM" in section          # Tamil folder names survive
    assert "-m ninaivu_lite.control --start" in section
    assert section.index('Exec \'"$WINDIR\\explorer.exe" "$TEMP\\ninaivu-lite-start-again.vbs"\'') \
        < section.index("nl_restart_direct:")


# --- A166: the sign-in box is set once the folder is known -----------------------------------


def test_a166_the_components_page_comes_after_the_folder_page():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    pages = nsi_block(nsi, "[% block ui_pages %]", "[% endblock %]")
    assert "[[ super() ]]" not in pages
    order = ["MUI_PAGE_WELCOME", "MULTIUSER_PAGE_INSTALLMODE", "MUI_PAGE_DIRECTORY",
             "MUI_PAGE_COMPONENTS", "MUI_PAGE_INSTFILES", "MUI_PAGE_FINISH"]
    at = [pages.index("!insertmacro " + name) for name in order]
    assert at == sorted(at)
    assert pages.index("MUI_PAGE_CUSTOMFUNCTION_PRE RememberStartAtSignIn") \
        < pages.index("!insertmacro MUI_PAGE_COMPONENTS")
    # pynsist's licence page is kept.
    assert "MUI_PAGE_LICENSE [[license_file]]" in pages


def test_a166_the_section_checks_the_folder_again():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    function = nsi_block(nsi, "Function RememberStartAtSignIn", "FunctionEnd")
    assert "StrCpy $nl_checked_dir $INSTDIR" in function
    section = nsi_block(nsi, 'Section "Start Ninaivu Lite at sign-in" sec_autostart')
    assert section.index("StrCmp $nl_checked_dir $INSTDIR 0 nl_autostart_keep") \
        < section.index("IfSilent 0 nl_autostart_on") < section.index("nl_autostart_keep:") \
        < section.index('StrCmp $nl_ours "1"')


# --- A168: the uninstaller names the person's own data folder --------------------------------


def test_a168_the_uninstaller_says_where_the_data_really_is():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    section = nsi_block(nsi, 'Section "un.Say what was kept"')
    assert section.index("SetShellVarContext current") < section.index("DetailPrint")


# --- A167: the Linux installer on the wrong machine ------------------------------------------


def header_for(arch: str, tmp_path: Path) -> Path:
    text = read("installers/linux/header.sh").replace("__ARCH__", arch).replace("__VERSION__", "9.9.9")
    script = tmp_path / "installer.sh"
    script.write_text(text, encoding="utf-8")
    return script


def test_a167_the_header_refuses_another_machines_file(tmp_path):
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("needs a POSIX shell")
    done = subprocess.run(["sh", str(header_for("riscv-nothing", tmp_path))],
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "This installer is for 64-bit Linux on riscv-nothing" in done.stderr
    assert "Nothing was changed." in done.stderr


def test_a167_a_python_that_cannot_run_changes_nothing(tmp_path):
    env, home, _calls = user_install_env(tmp_path)
    payload = stand_in_payload(tmp_path)
    (payload / "python" / "bin" / "python3").write_text("#!/bin/sh\nexit 126\n", encoding="utf-8")
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "cannot run on this machine" in done.stderr and "Nothing was changed." in done.stderr
    assert not (home / ".local" / "lib" / "ninaivu-lite").exists()


def test_a167_a_failed_first_install_does_not_speak_of_an_earlier_version(tmp_path):
    env, home, _calls = user_install_env(tmp_path, FAIL_PIP="1")
    payload = stand_in_payload(tmp_path)
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "Nothing was installed." in done.stderr
    assert "earlier version" not in done.stderr


# --- A169: the same address after a restart or an upgrade ------------------------------------


def test_a169_a_stopped_server_leaves_its_port(tmp_path):
    control.write_state(tmp_path, 8093)
    control.clear_state(tmp_path)
    assert not (tmp_path / control.STATE_FILE).exists()
    assert control.Controller(str(tmp_path)).last_port() == 8093


def test_a169_start_asks_for_the_last_port(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / control.LAST_PORT_FILE).write_text("8093", encoding="ascii")
    seen = {}

    class Ended:
        pid = -1

        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd

        def poll(self):
            return 1

    monkeypatch.setattr(control.Controller, "running", lambda self: False)
    monkeypatch.setattr(control.subprocess, "Popen", Ended)
    control.Controller(str(data)).start(wait=1)
    assert seen["cmd"][-2:] == ["--port", "8093"]


@pytest.mark.parametrize("text", ["0", "70000", "nonsense", ""])
def test_a169_a_bad_last_port_is_not_used(tmp_path, text):
    (tmp_path / control.LAST_PORT_FILE).write_text(text, encoding="ascii")
    assert control.Controller(str(tmp_path)).last_port() is None


def test_a169_a_server_left_running_from_an_unclean_end_gives_its_port(tmp_path):
    (tmp_path / control.STATE_FILE).write_text(json.dumps({"pid": -5, "port": 8095}))
    (tmp_path / control.LAST_PORT_FILE).write_text("8093", encoding="ascii")
    assert control.Controller(str(tmp_path)).last_port() == 8095


def test_a169_the_setup_code_hint_follows_who_started_it(tmp_path):
    env, home, _calls = user_install_env(tmp_path, WAS_RUNNING="1", NEEDS_SETUP="1")
    payload = stand_in_payload(tmp_path)
    prefix = home / ".local" / "lib" / "ninaivu-lite"
    (prefix / "python").mkdir(parents=True)
    shutil.copytree(payload / "python" / "bin", prefix / "python" / "bin")
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--no-service"], env=env,
                          check=True, capture_output=True, text=True)
    assert "so it was started again" in done.stdout
    assert "journalctl" not in done.stdout
    assert "Setup code:     in the Control Panel" in done.stdout


def test_a169_the_unit_says_how_to_keep_a_change_of_your_own():
    script = read("installers/linux/install.sh")
    unit = script[script.index('cat > "$unit" <<UNIT'):]
    unit = unit[:unit.index("\nUNIT\n")]
    assert "edit ninaivu-lite" in unit and "drop-in" in unit


# --- A170: a data folder nothing can be written to -------------------------------------------


def test_a170_a_read_only_place_is_said_plainly(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    started = []
    monkeypatch.setattr(control.Controller, "running", lambda self: False)
    monkeypatch.setattr(control.subprocess, "Popen", lambda *a, **k: started.append(a))
    message = control.Controller(str(blocker / "data")).start(wait=1)
    assert not started
    assert "cannot write to its data folder" in message
    assert "எழுத முடியவில்லை" in message
    assert "The log says why" not in message


# --- A171: ports a server can be found at ----------------------------------------------------


@pytest.mark.parametrize("port", ["0", "65536", "99999", "-1", "eighty"])
def test_a171_impossible_ports_are_refused(port, capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["--port", port])
    assert "--port" in capsys.readouterr().err


def test_a171_real_ports_are_taken():
    assert cli.parse_args(["--port", "1"]).port == 1
    assert cli.parse_args(["--port", "65535"]).port == 65535
    assert cli.parse_args([]).port == 8080


# --- A172: one release at a time, checked again before it is published ----------------------


def test_a172_releases_run_one_at_a_time_and_check_again():
    workflow = read(".github/workflows/release.yml")
    head = workflow[:workflow.index("jobs:")]
    assert "concurrency:" in head and "cancel-in-progress: false" in head
    release = workflow[workflow.index("  release:"):]
    assert release.index("sh tools/check-release-version.sh") \
        < release.index("softprops/action-gh-release")


# --- A173: a desktop entry for a folder with a space ------------------------------------------


def test_a173_the_desktop_entry_quotes_the_panel(tmp_path):
    env, home, _calls = user_install_env(tmp_path)
    payload = stand_in_payload(tmp_path)
    prefix = tmp_path / "My Apps" / "ninaivu-lite"
    subprocess.run(["sh", str(INSTALL), str(payload), "--prefix", str(prefix), "--quiet",
                    "--no-service"], env=env, check=True, capture_output=True, text=True)
    entry = (home / ".local" / "share" / "applications" / "ninaivu-lite.desktop").read_text()
    assert f'Exec="{prefix}/ninaivu-lite-panel"' in entry
