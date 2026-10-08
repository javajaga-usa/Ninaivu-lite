"""Regression tests for the project audit of 2026-10-06: the critical finding
(A11) and the high-priority findings (A12-A19). Each test sets up the scenario
the audit described and checks that it no longer happens."""

from __future__ import annotations

from pathlib import Path

from test_import import noisy_jpeg, rows, run, sha

from ninaivu_lite import importer

ROOT = Path(__file__).resolve().parent.parent


# --- A11: a retried import never writes over a good earlier copy -----------------------------


def test_a11_retry_after_a_read_error_keeps_the_earlier_copy(tmp_path, monkeypatch):
    card, dest, data = tmp_path / "Card", tmp_path / "Archive", tmp_path / "data"
    photo = noisy_jpeg(card / "DCIM" / "100CANON" / "IMG_0001.JPG", "2019:05:12 10:00:00", seed=1)
    run(data, [card], dest)
    first = Path(rows(data)["IMG_0001.JPG"]["destination"])
    kept = sha(first)
    # The card is formatted and reused: a new photo arrives under the same name,
    # and the first attempt at it fails part way.
    photo.unlink()
    noisy_jpeg(photo, "2024:12:25 09:00:00", seed=9)
    real_copy = importer.Importer._copy_and_hash

    def broken(self, src, tmp):
        raise OSError("I/O error on the card")

    monkeypatch.setattr(importer.Importer, "_copy_and_hash", broken)
    run(data, [card], dest)
    assert rows(data)["IMG_0001.JPG"]["status"] == "error"
    monkeypatch.setattr(importer.Importer, "_copy_and_hash", real_copy)
    run(data, [card], dest)
    archived = {sha(p) for p in dest.rglob("*.JPG")}
    assert kept in archived and sha(first) == kept
    assert sha(photo) in archived and len(archived) == 2


def test_a11_a_copy_the_audit_found_damaged_is_still_replaced(tmp_path):
    card, dest, data = tmp_path / "Card", tmp_path / "Archive", tmp_path / "data"
    photo = noisy_jpeg(card / "IMG_0002.JPG", "2020:01:01 10:00:00", seed=2)
    run(data, [card], dest)
    copy = Path(rows(data)["IMG_0002.JPG"]["destination"])
    copy.write_bytes(copy.read_bytes()[:-10] + b"0123456789")
    run(data, [card], dest, mode="verify")
    assert rows(data)["IMG_0002.JPG"]["status"] == "error"
    run(data, [card], dest)
    assert sha(copy) == sha(photo)
    assert len(list(dest.rglob("*.JPG"))) == 1


# --- A13: a restore puts back the backup, not what a leftover WAL holds --------------------


def _index_left_open_with_later_changes(data: Path) -> None:
    """Changes made after the backup, left in the WAL by a process that ended
    without closing the index (as a closed window or a power cut does)."""
    import subprocess
    import sys
    code = (f"import os, sys; sys.path.insert(0, {str(ROOT)!r})\n"
            "from ninaivu_lite import db\n"
            f"c = db.connect({str(data)!r})\n"
            "for i in range(50):\n"
            "    c.execute('INSERT INTO folders (path) VALUES (?)', (f'/later/{i}',))\n"
            "c.commit()\n"
            "c.execute(\"UPDATE users SET display_name = 'CHANGED LATER'\"); c.commit()\n"
            "os._exit(0)\n")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a13_restore_ignores_a_leftover_wal_and_keeps_what_was_there(tmp_path):
    from ninaivu_lite import auth, backups, db
    data = tmp_path / "data"
    conn = db.connect(data)
    db.sync_folders(conn, [str(tmp_path / "Photos")])
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    conn.close()
    (data / "avatars").mkdir()
    (data / "avatars" / "1.jpg").write_bytes(b"picture")
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(data, bundle)
    _index_left_open_with_later_changes(data)
    assert (data / "ninaivu-lite.db-wal").exists()

    aside = backups.restore(data, bundle)
    c = db.connect(data)
    assert c.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1
    assert c.execute("SELECT display_name FROM users").fetchone()[0] == "Appa"
    assert (data / "avatars" / "1.jpg").read_bytes() == b"picture"
    # Nothing that was there is lost: it is moved aside.
    assert (aside / "ninaivu-lite.db").is_file()


def test_a13_restore_refuses_what_it_cannot_trust(tmp_path):
    import zipfile

    import pytest

    from ninaivu_lite import backups, db
    data = tmp_path / "data"
    db.connect(data).close()
    before = (data / "ninaivu-lite.db").read_bytes()
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(damaged, "w") as z:
        z.writestr("ninaivu-lite.db", b"SQLite format 3\0" + b"\xff" * 4000)
        z.writestr("../escape.txt", "outside")
    newer = tmp_path / "newer.zip"
    other = tmp_path / "newer.db"
    c = db.connect(tmp_path / "other")
    c.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    c.close()
    other.write_bytes((tmp_path / "other" / "ninaivu-lite.db").read_bytes())
    with zipfile.ZipFile(newer, "w") as z:
        z.write(other, "ninaivu-lite.db")
    for bad in (damaged, newer, tmp_path / "missing.zip"):
        with pytest.raises(backups.RestoreError):
            backups.restore(data, bad)
    assert (data / "ninaivu-lite.db").read_bytes() == before
    assert not (tmp_path / "escape.txt").exists()
    assert not list(data.glob("before-restore-*"))


def test_a13_the_command_will_not_restore_under_a_running_server(tmp_path, monkeypatch):
    from ninaivu_lite import __main__ as cli
    from ninaivu_lite import net
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: True)
    assert cli.main(["--data", str(tmp_path / "data"), "--restore", str(tmp_path / "x.zip")]) == 2


# --- A14: an index that cannot be read never turns into "no library folders" -----------------


def test_a14_unreadable_index_with_missing_settings_changes_nothing(tmp_path, monkeypatch):
    from ninaivu_lite import __main__ as cli
    from ninaivu_lite import config as config_module
    from ninaivu_lite import db
    from ninaivu_lite.config import Config
    photos = tmp_path / "Photos"
    photos.mkdir()
    data = tmp_path / "data"
    c = db.connect(data)
    db.sync_folders(c, [str(photos)])
    c.close()
    before = (data / "ninaivu-lite.db").read_bytes()
    # As a network share's file:// URI, or a lock that outlasts the wait, would.
    real_connect = config_module.sqlite3.connect

    def refuse(*args, **kwargs):
        raise config_module.sqlite3.OperationalError("invalid uri authority")

    monkeypatch.setattr(config_module.sqlite3, "connect", refuse)
    cfg = Config.load(data)
    assert cfg.folders_unknown and cfg.folders == []
    cfg.save()                                    # e.g. the Control Panel saving a choice
    assert not (data / "settings.json").exists()
    import logging
    root = logging.getLogger()
    handlers = list(root.handlers)
    try:
        assert cli.main(["--data", str(data), "--no-browser"]) == 2
    finally:
        for handler in [h for h in root.handlers if h not in handlers]:
            root.removeHandler(handler)
            handler.close()
    monkeypatch.setattr(config_module.sqlite3, "connect", real_connect)
    assert (data / "ninaivu-lite.db").read_bytes() == before
    assert Config.load(data).folders == [str(photos)]


def test_a14_index_is_read_by_plain_path_not_uri(tmp_path):
    from ninaivu_lite import db
    from ninaivu_lite.config import index_folders
    data = tmp_path / "data #1 நினைவு"            # "?" is not allowed on Windows
    c = db.connect(data)
    db.sync_folders(c, ["/photos/a"])
    c.close()
    assert index_folders(data) == ["/photos/a"]
    source = (ROOT / "ninaivu_lite" / "config.py").read_text(encoding="utf-8")
    assert "as_uri()" not in source


# --- A12: a name someone else owns, pointed at this computer, is not answered ----------------


REBOUND = {"Host": "attacker.example:8080", "Origin": "http://attacker.example:8080"}


def test_a12_a_rebound_name_gets_nothing(app):
    from ninaivu_lite import auth, db
    conn = db.connect(app.config["LITE"].data_dir)
    amma = auth.create_user(conn, "amma", name="Amma", role="family")   # no PIN
    target = conn.execute("SELECT id FROM assets").fetchone()[0]
    conn.close()
    page = app.test_client()
    assert page.get("/api/auth/profiles", headers=REBOUND).status_code == 400
    assert page.post("/api/auth/enter", json={"id": amma.id}, headers=REBOUND,
                     environ_base={"REMOTE_ADDR": "192.168.1.50"}).status_code == 400
    assert page.get(f"/api/file/{target}", headers=REBOUND).status_code == 400
    assert page.get("/", headers={"Host": "attacker.example"}).status_code == 400


def test_a12_setup_cannot_be_taken_through_a_rebound_loopback(tmp_path):
    from ninaivu_lite import create_app
    from ninaivu_lite.config import Config
    app = create_app(Config(data_dir=str(tmp_path / "data"), folders=[]))
    r = app.test_client().post(
        "/api/auth/setup", json={"username": "evil", "password": "evil passphrase"},
        headers=REBOUND, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400


def test_a12_this_computers_own_names_still_work(monkeypatch):
    from ninaivu_lite import app as app_module
    monkeypatch.setattr(app_module.socket, "gethostname", lambda: "Family-PC")
    for host in ("localhost:8080", "127.0.0.1:8080", "192.168.1.20:8080", "[::1]:8080",
                 "[fe80::1]", "10.0.0.5", "family-pc:8080", "FAMILY-PC", "family-pc.local:8080",
                 "family-pc.lan", "family-pc.home.arpa:8080", "photos.example.org"):
        allowed = app_module.host_allowed(host, ["photos.example.org"])
        assert allowed, host
    for host in ("attacker.example:8080", "family-pc.attacker.example", "", "localhost.evil.com",
                 "127.0.0.1.nip.io"):
        assert not app_module.host_allowed(host), host
    monkeypatch.setenv("NINAIVU_ALLOWED_HOSTS", "pi.local, Photos.Home.Example")
    assert app_module.host_allowed("pi.local:8080")
    assert app_module.host_allowed("photos.home.example")
    assert not app_module.host_allowed("attacker.example")


# --- A18: an upgrade brings a new service worker, and scripts come from the server --------


def test_a18_service_worker_version_follows_the_app_files(app, monkeypatch):
    import re

    from ninaivu_lite import pages
    client = app.test_client()
    first = client.get("/sw.js")
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-cache"
    version = re.search(r"const CACHE_VERSION = '([^']+)'", first.get_data(as_text=True))[1]
    assert version != "lite-1" and pages.__version__ in version
    # Another release of the files is another worker.
    pages._shell_version.clear()
    monkeypatch.setattr(pages, "__version__", "99.0.0")
    again = client.get("/sw.js").get_data(as_text=True)
    assert re.search(r"const CACHE_VERSION = '([^']+)'", again)[1] != version
    pages._shell_version.clear()


def test_a18_app_files_are_not_answered_from_the_cache_first():
    worker = (ROOT / "ninaivu_lite" / "static" / "sw.js").read_text(encoding="utf-8")
    static_route = worker[worker.index("url.pathname.startsWith('/static/')"):]
    assert "networkFirst(request, SHELL_CACHE)" in static_route.split("}", 1)[0]
    assert "staleWhileRevalidate" not in worker


# --- A19: a long first-day step scrolls inside its card -------------------------------------


def test_a19_first_day_body_scrolls_instead_of_spilling():
    import re
    css = (ROOT / "ninaivu_lite" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
    rule = re.search(r"\.fd-body \{([^}]*)\}", css)[1]
    assert "overflow-y: auto" in rule


# --- A15: the Linux uninstaller removes only what was installed -----------------------------


def _fake_payload(root: Path) -> Path:
    """Just enough of an installer payload for install.sh: a stand-in Python
    that accepts what the script asks of it."""
    payload = root / "payload"
    python = payload / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    # Nothing of it is running: an upgrade stops what is, and checks.
    python.write_text('#!/bin/sh\ncase "$*" in *--running*) exit 1 ;; esac\n'
                      'echo /nowhere/icon-192.png\n', encoding="utf-8")
    python.chmod(0o755)
    (payload / "wheels").mkdir()
    (payload / "wheels" / "ninaivu_lite-0-py3-none-any.whl").write_bytes(b"")
    for name, text in (("VERSION", "1.6.0\n"), ("LICENSE", "licence\n"), ("README.md", "readme\n")):
        (payload / name).write_text(text, encoding="utf-8")
    return payload


def test_a15_uninstall_leaves_the_rest_of_a_shared_prefix(tmp_path):
    import os
    import shutil
    import subprocess

    import pytest
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the Linux installer needs a POSIX shell")
    payload = _fake_payload(tmp_path)
    home = tmp_path / "home"
    shims = tmp_path / "shims"
    shims.mkdir()
    # Installed as an ordinary person, whoever runs the tests.
    (shims / "id").write_text('#!/bin/sh\n[ "$1" = "-u" ] && echo 1000 || echo person\n',
                              encoding="utf-8")
    (shims / "id").chmod(0o755)
    shared = home / "apps"
    keep = shared / "other-program" / "important.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("not Ninaivu Lite's", encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"),
           "XDG_CONFIG_HOME": str(home / ".config"),
           "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = ROOT / "installers" / "linux" / "install.sh"
    subprocess.run(["sh", str(script), str(payload), "--prefix", str(shared), "--no-service",
                    "--quiet"], check=True, env=env)
    assert (shared / "python").is_dir() and (shared / "uninstall").is_file()
    subprocess.run(["sh", str(shared / "uninstall")], check=True, env=env)
    assert keep.read_text(encoding="utf-8") == "not Ninaivu Lite's"
    assert not (shared / "python").exists() and not (shared / "uninstall").exists()
    # Installed into a folder of its own, the folder goes too.
    own = home / "ninaivu"
    subprocess.run(["sh", str(script), str(payload), "--prefix", str(own), "--no-service",
                    "--quiet"], check=True, env=env)
    subprocess.run(["sh", str(own / "uninstall")], check=True, env=env)
    assert not own.exists()


# --- A16: the Windows uninstaller stops Ninaivu Lite before its packages go ------------------


def test_a16_uninstall_stops_it_before_the_packages_are_removed():
    nsi = (ROOT / "installers" / "windows" / "ninaivu-lite.nsi").read_text(encoding="utf-8")
    sections = nsi[nsi.index("[% block sections %]"):]
    ours = sections.index('Section "un.Stop Ninaivu Lite"')
    # pynsist's "Uninstall" section (which removes pkgs first) comes from super().
    assert ours < sections.index("[[ super() ]]")
    stop = sections[ours:sections.index("SectionEnd", ours)]
    assert "--stop --autostart off" in stop
    assert 'Delete "$SMSTARTUP\\Ninaivu Lite.vbs"' in stop
    assert "WaitUntilNotInUse" in stop
    # Nothing of ours is left to run after the packages are gone.
    assert "block uninstall_files" not in nsi
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "uninstall left the start-at-sign-in shortcut behind" in release
    assert "uninstall left Ninaivu Lite running" in release


# --- A17: a release is only ever a new version ---------------------------------------------


def _release_check(tmp_path: Path, *, version: str, ref_type: str, ref_name: str,
                   tagged: bool, released: bool, changelog: str) -> int:
    import os
    import shutil
    import subprocess

    import pytest
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the check is a POSIX shell script, run on Linux")
    repo = tmp_path / "repo"
    (repo / "ninaivu_lite").mkdir(parents=True, exist_ok=True)
    (repo / "ninaivu_lite" / "version.py").write_text(f'__version__ = "{version}"\n',
                                                     encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    for name, found in (("git", tagged), ("gh", released)):
        (shims / name).write_text(f"#!/bin/sh\nexit {0 if found else 2}\n", encoding="utf-8")
        (shims / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
           "REF_TYPE": ref_type, "REF_NAME": ref_name}
    script = ROOT / "tools" / "check-release-version.sh"
    return subprocess.run(["sh", str(script)], cwd=repo, env=env).returncode


NOTES = "# Changelog\n\n## 1.6.0 — 2026-10-06\n\n- New.\n\n## 1.5.1 — 2026-10-04\n"


def test_a17_run_workflow_refuses_a_version_already_released(tmp_path):
    assert _release_check(tmp_path, version="1.5.1", ref_type="branch", ref_name="main",
                          tagged=True, released=True, changelog=NOTES) != 0
    assert _release_check(tmp_path, version="1.6.0", ref_type="branch", ref_name="main",
                          tagged=False, released=True, changelog=NOTES) != 0
    assert _release_check(tmp_path, version="1.6.0", ref_type="branch", ref_name="main",
                          tagged=False, released=False, changelog=NOTES) == 0


def test_a17_a_tag_must_match_version_py_and_have_notes(tmp_path):
    assert _release_check(tmp_path, version="1.5.1", ref_type="tag", ref_name="v1.6.0",
                          tagged=True, released=False, changelog=NOTES) != 0
    assert _release_check(tmp_path, version="1.6.0", ref_type="tag", ref_name="v1.6.0",
                          tagged=True, released=False, changelog=NOTES) == 0
    assert _release_check(tmp_path, version="1.6.1", ref_type="tag", ref_name="v1.6.1",
                          tagged=True, released=False, changelog=NOTES) != 0


def test_a17_nothing_is_built_before_the_version_is_checked():
    import re
    flow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = dict(re.findall(r"(?ms)^  ([a-z]+):\n(.*?)(?=^  [a-z]+:\n|\Z)",
                           flow[flow.index("\njobs:\n"):]))
    assert "check-release-version.sh" in jobs["version"]
    for name in ("windows", "macos", "linux", "release"):
        needs = re.search(r"^    needs: \[([^\]]*)\]", jobs[name], re.M)
        assert needs and "version" in needs[1].split(", "), name
