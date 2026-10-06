"""Audit 2026-10-06, packaging and CI: A23 (installer part), A24, A29, A30,
A31, A38, A56, A57, A58, A59, A62. The Linux installer is run here with a
stand-in payload and stand-in system commands; what only a Windows or macOS
build, or GitHub, can run is checked in the files that drive it."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ninaivu_lite import control, panel

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "installers" / "linux" / "install.sh"
PINNED = (ROOT / ".python-version").read_text().strip()
PBS_RELEASE = (ROOT / "installers" / "PBS_RELEASE").read_text().strip()


def read(path: str) -> str:
    file = ROOT / path
    if not file.exists():            # workflows are not in every copy of the tree
        pytest.skip(f"{path} is not here")
    return file.read_text(encoding="utf-8")


def needs_posix_shell():
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the Linux installer needs a POSIX shell")


def fake_payload(root: Path) -> Path:
    """Just enough of an installer payload for install.sh: a stand-in Python
    that accepts what the script asks of it."""
    payload = root / "payload"
    python = payload / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho /nowhere/icon-192.png\n", encoding="utf-8")
    python.chmod(0o755)
    (payload / "wheels").mkdir()
    (payload / "wheels" / "ninaivu_lite-0-py3-none-any.whl").write_bytes(b"")
    for name, text in (("VERSION", "1.6.0\n"), ("LICENSE", "licence\n"), ("README.md", "readme\n")):
        (payload / name).write_text(text, encoding="utf-8")
    return payload


def shim(folder: Path, name: str, body: str) -> None:
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
    (folder / name).chmod(0o755)


def logging_shims(folder: Path, log: Path, *names: str) -> None:
    for name in names:
        shim(folder, name, f'echo "{name} $*" >> "{log}"\nexit 0\n')


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- A23, A57: a person's own install ---------------------------------------------------


def test_a23_a57_user_install_keeps_its_data_private_and_restarts_its_service(tmp_path):
    needs_posix_shell()
    payload = fake_payload(tmp_path)
    home = tmp_path / "home"
    shims, log = tmp_path / "shims", tmp_path / "calls.log"
    shim(shims, "id", '[ "$1" = "-u" ] && echo 1000 || echo person\n')
    logging_shims(shims, log, "systemctl", "loginctl")
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"),
           "XDG_CONFIG_HOME": str(home / ".config"),
           "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"}
    for _ in ("install", "upgrade"):
        subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], check=True, env=env)
    data = home / ".local" / "share" / "ninaivu-lite"
    assert mode(data) == 0o700
    unit = (home / ".config" / "systemd" / "user" / "ninaivu-lite.service").read_text()
    assert "\nUMask=0027\n" in unit
    calls = log.read_text().splitlines()
    systemctl = [c for c in calls if c.startswith("systemctl")]
    # An upgrade stops the service before the files are replaced, and restarts
    # it after (enable --now leaves an active service running the old code).
    one_round = ["systemctl --user stop ninaivu-lite", "systemctl --user daemon-reload",
                 "systemctl --user enable ninaivu-lite", "systemctl --user restart ninaivu-lite"]
    assert systemctl == one_round * 2
    # A person's own install has its Control Panel.
    assert (home / ".local" / "bin" / "ninaivu-lite-panel").is_symlink()


# --- A23, A24, A57: a system-wide install, as root ---------------------------------------


@pytest.fixture()
def as_root(tmp_path):
    """Run install.sh as if by root, with every system folder under tmp_path/sys
    and the system's commands replaced by ones that write down what they are asked."""
    needs_posix_shell()
    payload = fake_payload(tmp_path)
    shims, log = tmp_path / "shims", tmp_path / "calls.log"
    shim(shims, "id", 'case "$*" in\n  "-u") echo 0 ;;\n  "-un") echo root ;;\n'
                      '  "-u ninaivu-lite") echo 990 ;;\n  *) echo 0 ;;\nesac\n')
    logging_shims(shims, log, "systemctl", "chown", "useradd", "adduser", "sudo", "loginctl")
    # The service account can read everything but what is under a "locked" folder.
    shim(shims, "runuser", f'echo "runuser $*" >> "{log}"\nshift 2\n[ "$1" = "--" ] && shift\n'
                           'case "$*" in test*locked*) exit 1 ;; test*) exit 0 ;; esac\n'
                           'exec "$@"\n')
    system = tmp_path / "sys"
    env = {**os.environ, "HOME": str(tmp_path / "root-home"), "NINAIVU_TEST_ROOT": str(system),
           "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"}

    def run(*args: str) -> str:
        done = subprocess.run(["sh", str(INSTALL), str(payload), *args], check=True, env=env,
                              capture_output=True, text=True)
        return done.stdout

    return SimpleNamespace(run=run, env=env, log=log, sys=system,
                           data=system / "var" / "lib" / "ninaivu-lite",
                           prefix=system / "opt" / "ninaivu-lite",
                           bindir=system / "usr" / "local" / "bin",
                           apps=system / "usr" / "local" / "share" / "applications",
                           unit=system / "etc" / "systemd" / "system" / "ninaivu-lite.service")


def test_a24_root_install_runs_as_its_account_with_no_desktop_panel(tmp_path, as_root):
    photos = tmp_path / "locked" / "Photos"
    photos.mkdir(parents=True)
    # What an earlier version left for everyone: a panel that could not work.
    as_root.bindir.mkdir(parents=True)
    (as_root.bindir / "ninaivu-lite-panel").write_text("old")
    as_root.apps.mkdir(parents=True)
    (as_root.apps / "ninaivu-lite.desktop").write_text("old")

    out = as_root.run("--photos", str(photos))
    calls = as_root.log.read_text().splitlines()
    # A23: the data folder is the account's, and only its group may look in.
    assert mode(as_root.data) == 0o700
    assert f"chown -R ninaivu-lite:ninaivu-lite {as_root.data}" in calls
    unit = as_root.unit.read_text()
    assert "\nUser=ninaivu-lite\n" in unit and "\nUMask=0027\n" in unit
    # A57: stopped before the files are replaced, restarted after.
    systemctl = [c for c in calls if c.startswith("systemctl")]
    assert systemctl[0] == "systemctl stop ninaivu-lite"
    assert systemctl[-1] == "systemctl restart ninaivu-lite"
    # A24: no Control Panel or desktop entry for everyone, and the earlier ones gone.
    for path in (as_root.bindir / "ninaivu-lite-panel", as_root.apps / "ninaivu-lite.desktop",
                 as_root.prefix / "ninaivu-lite-panel"):
        assert not path.exists(), path
    assert "Control Panel:" not in out and "systemctl start|stop|restart ninaivu-lite" in out
    # A24: the advice reaches the folder through the ones above it, and covers
    # what is added to it later.
    assert f'setfacl -m u:ninaivu-lite:x "{tmp_path / "locked"}"' in out
    assert f'setfacl -R -m u:ninaivu-lite:rX "{photos}"' in out
    assert f'find "{photos}" -type d -exec setfacl -m d:u:ninaivu-lite:rX {{}} +' in out
    # A24: the command runs Ninaivu Lite as the account, never as root.
    wrapper = as_root.prefix / "ninaivu-lite"
    subprocess.run([str(wrapper), "--version"], check=True, env=as_root.env)
    py = as_root.prefix / "python" / "bin" / "python3"
    assert (f"runuser -u ninaivu-lite -- {py} -m ninaivu_lite --data {as_root.data} --version"
            in as_root.log.read_text())
    assert "sudo -u ninaivu-lite" in wrapper.read_text()


def test_a24_root_install_without_a_service_still_gives_the_data_to_the_account(as_root):
    as_root.run("--no-service")
    calls = as_root.log.read_text().splitlines()
    assert f"chown -R ninaivu-lite:ninaivu-lite {as_root.data}" in calls
    assert mode(as_root.data) == 0o700
    assert not as_root.unit.exists()
    assert "runuser -u ninaivu-lite" in (as_root.prefix / "ninaivu-lite").read_text()


# --- A29: the bundled Python is the one whose hash is written down -------------------------


def sums() -> dict[str, str]:
    lines = (ROOT / "installers" / "PYTHON_SHA256SUMS").read_text().split("\n")
    pairs = [line.split() for line in lines if line.strip()]
    assert all(len(p) == 2 and re.fullmatch(r"[0-9a-f]{64}", p[0]) for p in pairs), pairs
    return {name: digest for digest, name in pairs}


def test_a29_every_bundled_python_has_its_sha256_written_down():
    listed = sums()
    for triple in ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
                   "aarch64-apple-darwin", "x86_64-apple-darwin"):
        name = f"cpython-{PINNED}+{PBS_RELEASE}-{triple}-install_only_stripped.tar.gz"
        assert name in listed, f"{name} is not in installers/PYTHON_SHA256SUMS"
    for script in ("installers/linux/build.sh", "installers/macos/build.sh"):
        text = read(script)
        assert "PYTHON_SHA256SUMS" in text and 'expected "$expected"' not in text
        assert re.search(r'if \[ "\$actual" != "\$expected" \]; then\n\s+rm -f', text), script


def test_a29_linux_build_refuses_a_cached_python_that_does_not_match(tmp_path):
    needs_posix_shell()
    if shutil.which("bash") is None or shutil.which("sha256sum") is None:
        pytest.skip("needs bash and sha256sum")
    tree = tmp_path / "tree"
    (tree / "installers" / "linux" / "build").mkdir(parents=True)
    (tree / "ninaivu_lite").mkdir()
    shutil.copy(ROOT / "installers" / "linux" / "build.sh", tree / "installers" / "linux")
    for name in ("PBS_RELEASE", "PYTHON_SHA256SUMS"):
        shutil.copy(ROOT / "installers" / name, tree / "installers")
    shutil.copy(ROOT / ".python-version", tree)
    shutil.copy(ROOT / "ninaivu_lite" / "version.py", tree / "ninaivu_lite")
    cached = (tree / "installers" / "linux" / "build"
              / f"cpython-{PINNED}+{PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz")
    cached.write_bytes(b"not the file upstream published")
    done = subprocess.run(["bash", str(tree / "installers" / "linux" / "build.sh"), "amd64"],
                          capture_output=True, text=True)
    assert done.returncode != 0
    assert "SHA-256" in done.stderr and sums()[cached.name] in done.stderr
    assert not cached.exists()           # the next build downloads it again


def test_a29_windows_build_checks_the_embeddable_python_and_pins_its_tools():
    build = read("installers/windows/build.ps1")
    assert "$env:PYNSIST_CACHE_DIR = $cache" in build
    assert "Get-AuthenticodeSignature" in build and "Python Software Foundation" in build
    assert build.index("PYNSIST_CACHE_DIR") < build.index("python -m nsist")
    release = read(".github/workflows/release.yml")
    assert "pip install pynsist==2.8" in release
    assert re.search(r"choco install nsis --version=3\.\d+", release)
    assert 'if ($nsisVersion -ne "v3.' in release


# --- A30, A31, A56, A59: the release workflow ---------------------------------------------


def test_a30_actions_that_hold_secrets_or_write_access_are_pinned_to_a_commit():
    release = read(".github/workflows/release.yml")
    third_party = re.findall(r"uses: ((?!actions/|\./)[\w.-]+/[\w.-]+)@(\S+)(.*)", release)
    assert {name for name, _, _ in third_party} >= {
        "signpath/github-action-submit-signing-request", "softprops/action-gh-release"}
    for name, ref, comment in third_party:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name}@{ref}"
        assert re.fullmatch(r" # v\d+(\.\d+)*", comment), f"{name}: say which version"


PUBLISHING = ("github.ref_type == 'tag' || (github.event_name == 'workflow_dispatch' "
              "&& github.ref == 'refs/heads/main')")


def job(release: str, name: str) -> str:
    start = release.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z]+:\n", release[start + 1:])
    return release[start:start + 1 + following.start()] if following else release[start:]


def test_a31_only_a_tag_or_main_publishes_or_signs():
    release = read(".github/workflows/release.yml")
    assert f"({PUBLISHING})" in job(release, "release")
    assert f"USE_SIGNPATH: ${{{{ secrets.SIGNPATH_API_TOKEN != '' && ({PUBLISHING}) }}}}" in release
    assert f"if: {PUBLISHING}" in job(release, "tests")
    assert "github.event_name == 'workflow_dispatch'" not in re.sub(
        re.escape(PUBLISHING), "", release)


def test_a56_signing_waits_for_the_tests_and_nothing_is_published_incomplete():
    release = read(".github/workflows/release.yml")
    for name in ("windows", "macos"):
        text = job(release, name)
        assert "needs: [version, tests]" in text, name
        assert "needs.tests.result == 'success' || needs.tests.result == 'skipped'" in text
    publish = job(release, "release")
    assert "!contains(needs.*.result, 'failure')" in publish
    assert "!contains(needs.*.result, 'skipped')" in publish
    assert "fail_on_unmatched_files: true" in publish
    for name in ("windows", "macos-${{ matrix.arch }}", "linux-${{ matrix.arch }}"):
        upload = release[release.index(f"name: {name}\n"):]
        assert upload.index("if-no-files-found: error") < upload.index("path:"), name
    for file in ("windows-x64.exe", "windows-x64-portable.zip", "macos-arm64.dmg",
                 "macos-x86_64.dmg", "linux-amd64.sh", "linux-arm64.sh"):
        assert file in publish
    assert ("HAS_NOTARY: ${{ secrets.MAC_SIGN_P12_BASE64 != '' && secrets.MAC_SIGN_IDENTITY != '' "
            "&& secrets.MAC_NOTARY_PASSWORD != '' }}") in publish


def test_a59_every_released_installer_is_also_run():
    release = read(".github/workflows/release.yml")
    mac = job(release, "macos")
    assert "hdiutil attach" in mac and "/api/health" in mac and "--stop" in mac
    linux = job(release, "linux")
    assert "runner: ubuntu-24.04-arm" in linux and "if: matrix.arch == 'amd64'" not in linux
    assert 'sudo sh "$installer"' in linux and "uninstall --purge" in linux
    docker = job(release, "docker")
    assert "docker build -f installers/docker/Dockerfile" in docker and "/api/health" in docker


# --- A62: tool versions do not float ------------------------------------------------------


def test_a62_ruff_is_pinned():
    lines = (ROOT / "requirements-dev.txt").read_text().splitlines()
    ruff = [line for line in lines if line.startswith("ruff")]
    assert len(ruff) == 1 and re.fullmatch(r"ruff==\d+\.\d+\.\d+", ruff[0])


# --- A38: the first-run setup code where installer users can see it ----------------------


def test_a38_the_setup_code_is_in_server_json_until_there_is_an_administrator(tmp_path):
    control.write_state(tmp_path, 8123, "C0DE1234AB")
    state = json.loads((tmp_path / control.STATE_FILE).read_text())
    assert state["setup_code"] == "C0DE1234AB"
    c = control.Controller(str(tmp_path))
    assert c.setup_code() == "C0DE1234AB"            # no index yet: no administrator
    db = sqlite3.connect(tmp_path / "ninaivu-lite.db")
    db.execute("CREATE TABLE users (role TEXT, active INTEGER)")
    db.execute("INSERT INTO users VALUES ('family', 1), ('admin', 0)")
    db.commit()
    assert c.setup_code() == "C0DE1234AB"            # an inactive one does not count
    db.execute("INSERT INTO users VALUES ('admin', 1)")
    db.commit()
    db.close()
    assert c.setup_code() is None
    control.write_state(tmp_path, 8123)
    assert "setup_code" not in json.loads((tmp_path / control.STATE_FILE).read_text())
    assert control.Controller(str(tmp_path)).setup_code() is None


def test_a38_the_panel_shows_the_setup_code():
    rows = []
    fake = SimpleNamespace(busy=True, is_running=False, show_addresses=rows.extend,
                           library=SimpleNamespace(set=lambda text: None),
                           _set_buttons=lambda: None)
    panel.Panel.show_reading(fake, True, 8080, [], {"folders": []}, True, "C0DE1234AB")
    assert ("Setup code", "C0DE1234AB") in rows
    rows.clear()
    panel.Panel.show_reading(fake, True, 8080, [], {"folders": []}, True, None)
    assert not any(label == "Setup code" for label, _ in rows)


def test_a38_the_server_logs_the_setup_code_and_hands_it_to_the_panel(tmp_path, monkeypatch):
    import logging

    from ninaivu_lite import __main__ as entry
    from ninaivu_lite import net

    seen = {}

    def serve(app, host, port):
        seen["state"] = json.loads((tmp_path / "data" / control.STATE_FILE).read_text())

    monkeypatch.setattr(entry, "serve", serve)
    monkeypatch.setattr(net, "already_running", lambda port: False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert entry.main(["--no-browser", "--host", "127.0.0.1",
                           "--data", str(tmp_path / "data")]) == 0
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
    code = seen["state"]["setup_code"]
    assert re.fullmatch(r"[0-9A-F]{10}", code)
    log = (tmp_path / "data" / "logs" / "ninaivu-lite.log").read_text(encoding="utf-8")
    assert code in log and " INFO " in log


# --- A58: Windows install edge cases ----------------------------------------------------


def test_a58_the_upgrade_removes_only_a_ninaivu_lite_it_finds():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    block = nsi[nsi.index("[% block install_pkgs %]"):nsi.index("[% endblock %]",
                                                                nsi.index("[% block install_pkgs %]"))]
    marker = block.index('IfFileExists "$INSTDIR\\pkgs\\ninaivu_lite\\__init__.py" 0 nl_first_install')
    for removal in ('RMDir /r "$INSTDIR\\Python"', 'RMDir /r "$INSTDIR\\bin"',
                    "!insertmacro WaitUntilNotInUse"):
        assert marker < block.index(removal) < block.index("nl_first_install:"), removal


def test_a58_an_upgrade_keeps_start_at_sign_in_as_it_was_left():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    assert nsi.index("!define MUI_PAGE_CUSTOMFUNCTION_PRE RememberStartAtSignIn") \
        < nsi.index("!insertmacro MUI_PAGE_COMPONENTS")
    function = nsi[nsi.index("Function RememberStartAtSignIn"):]
    function = function[:function.index("FunctionEnd")]
    assert "SectionSetFlags ${sec_autostart} 0" in function
    assert f"Startup\\{control.STARTUP_NAME}" in function
    section = nsi[nsi.index('Section "Start Ninaivu Lite at sign-in" sec_autostart'):]
    section = section[:section.index("SectionEnd")]
    assert "IfSilent 0 nl_autostart_on" in section
    assert section.index("nl_autostart_on:") < section.index("--autostart on")


def test_a58_each_kind_of_copy_has_its_own_startup_name(tmp_path):
    assert len(set(control.STARTUP_NAMES.values())) == 3
    assert control.STARTUP_NAMES["installed"] == control.STARTUP_NAME == "Ninaivu Lite.vbs"
    installed = tmp_path / "Ninaivu Lite"
    (installed / "pkgs").mkdir(parents=True)
    (installed / "uninstall.exe").write_bytes(b"")
    portable = tmp_path / "portable" / "Ninaivu Lite"
    (portable / "pkgs").mkdir(parents=True)
    assert control.install_kind(installed / "pkgs") == "installed"
    assert control.install_kind(portable / "pkgs") == "portable"
    assert control.install_kind(ROOT) == "checkout"
    cmd = read("tools/start-with-windows.cmd")
    assert f'\\{control.STARTUP_NAMES["checkout"]}"' in cmd


def test_a58_a_portable_copy_moves_its_old_startup_file_to_its_own_name(tmp_path, monkeypatch):
    portable = tmp_path / "pendrive" / "Ninaivu Lite" / "pkgs"
    portable.mkdir(parents=True)
    appdata = tmp_path / "AppData"
    startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(control, "ROOT", portable)
    monkeypatch.setenv("APPDATA", str(appdata))
    old = startup / control.STARTUP_NAME
    own = startup / control.STARTUP_NAMES["portable"]
    c = control.Controller(str(tmp_path / "data"))

    # The installed copy's shortcut is not this copy's: left alone, and not "on" here.
    old.write_text('shell.CurrentDirectory = "C:\\Program Files\\Ninaivu Lite\\pkgs"\r\n')
    assert not c.autostart_enabled()
    c.set_autostart(False)
    assert old.exists()

    # This copy's, from before 1.6.0: on, and moved to its own name when switched.
    old.write_text(f'shell.CurrentDirectory = "{portable}"\r\n')
    assert c.autostart_enabled()
    c.set_autostart(True)
    assert own.is_file() and not old.exists()
    assert f'"{portable}"' in own.read_text(encoding="utf-8")
    c.set_autostart(False)
    assert not own.exists() and not c.autostart_enabled()
