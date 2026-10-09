"""Audit 2026-10-07, backup and restore (A65-A77) and install, upgrade and the
Control Panel (A103-A115; A70 also covers the panel's empty log). What only
an installer can do is checked by running install.sh with stand-in commands,
or in the NSIS script's text, as the packaging tests do."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ninaivu_lite import __main__ as cli
from ninaivu_lite import auth, backups, control, db, lock, net, panel, pages
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg, sign_in

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "installers" / "linux" / "install.sh"


def read(path: str) -> str:
    file = ROOT / path
    if not file.exists():
        pytest.skip(f"{path} is not here")
    return file.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


@pytest.fixture()
def clean_logging():
    """__main__.main sets up logging on the root logger: take it down again."""
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@contextmanager
def held_by_another_process(data: Path):
    """The server lock on *data*, held by another process (as a running server holds it)."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from ninaivu_lite import lock; "
            "ok = lock.acquire(sys.argv[2]); print('held' if ok else 'not held', flush=True); "
            "sys.stdin.read()")
    holder = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(data)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        yield
    finally:
        holder.stdin.close()
        holder.wait(timeout=60)
        holder.stdout.close()


def scanned_data(tmp_path: Path, library) -> tuple[Path, Path]:
    """A scanned index (with thumbnails) and one administrator, every connection closed."""
    root, data = library
    Config(data_dir=str(data), folders=[str(root)], active=str(root)).save()
    conn = db.connect(data)
    Scanner(str(data), [str(root)]).scan_once(conn)
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    conn.close()
    return root, data


def fake_health(monkeypatch, answer: dict) -> None:
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(answer).encode()))


# --- A65 (B01): removing a folder sets it aside; adding it back brings everything back -----


def test_a65_removing_a_folder_keeps_its_decisions_for_re_adding(app, admin, library, tmp_path):
    root, data = library
    more = tmp_path / "More"
    make_jpeg(more / "a.jpg", "2020:01:01 10:00:00")
    make_jpeg(more / "b.jpg", "2020:01:02 10:00:00", color=(10, 200, 30))
    assert admin.post("/api/library/root", json={"path": str(more)}).status_code == 200
    c = conn_of(app)
    app.config["SCANNER"].scan_once(c)
    i = ids(app)
    family = app.test_client()
    sign_in(app, family, "family", username="amma")
    assert admin.post("/api/visibility", json={"ids": [i["a.jpg"]],
                                               "visibility": "hidden"}).status_code == 200
    assert family.post(f"/api/asset/{i['b.jpg']}", json={"favorite": True}).status_code == 200
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": i["b.jpg"]}).json["token"]

    r = admin.delete("/api/admin/libraries", query_string={"path": str(more)})
    assert r.status_code == 200
    # Out of the gallery and its link paused, but not forgotten.
    assert c.execute("SELECT COUNT(*) FROM assets WHERE id IN (?, ?) AND missing = 0",
                     (i["a.jpg"], i["b.jpg"])).fetchone()[0] == 0
    assert c.execute("SELECT detached_at FROM folders WHERE path = ?",
                     (str(more),)).fetchone()[0] is not None
    assert app.test_client().get(f"/api/share/{token}").status_code == 404
    # A copy was taken first.
    assert list((data / "backups").glob("before-removing-folder-*.zip"))

    assert admin.post("/api/library/root", json={"path": str(more)}).status_code == 200
    app.config["SCANNER"].scan_once(c)
    assert ids(app)["a.jpg"] == i["a.jpg"] and ids(app)["b.jpg"] == i["b.jpg"]
    assert c.execute("SELECT visibility FROM assets WHERE id = ?",
                     (i["a.jpg"],)).fetchone()[0] == 2                    # still hidden
    assert family.get(f"/api/asset/{i['b.jpg']}").json["favorite"] is True
    assert app.test_client().get(f"/api/share/{token}").status_code == 200


def test_a65_settings_recovered_from_the_index_leave_a_removed_folder_out(tmp_path):
    from ninaivu_lite.config import index_folders
    data = tmp_path / "data"
    conn = db.connect(data)
    db.sync_folders(conn, ["/photos/kept", "/photos/removed"])
    db.sync_folders(conn, ["/photos/kept"])
    conn.close()
    assert index_folders(data) == ["/photos/kept"]


# --- A66 (B02): a folder whose photographs moved is pointed at their new place -------------


def test_a66_moving_a_folder_keeps_everything_set_for_it(app, admin, library, tmp_path):
    more = tmp_path / "More"
    make_jpeg(more / "kids" / "a.jpg", "2020:01:01 10:00:00")
    assert admin.post("/api/library/root", json={"path": str(more)}).status_code == 200
    c = conn_of(app)
    app.config["SCANNER"].scan_once(c)
    target = ids(app)["a.jpg"]
    assert admin.post("/api/visibility", json={"ids": [target],
                                               "visibility": "hidden"}).status_code == 200
    auth.create_user(c, "kutti", name="Kutti", role="family", library=str(more / "kids"))

    moved = tmp_path / "Drive E" / "More"
    moved.parent.mkdir()
    os.rename(more, moved)
    assert admin.post("/api/admin/libraries/move",
                      json={"path": str(more), "to": str(tmp_path / "nowhere")}).status_code == 400
    r = admin.post("/api/admin/libraries/move", json={"path": str(more), "to": str(moved)})
    assert r.status_code == 200
    assert str(moved) in [f["path"] for f in r.get_json()["folders"]]
    assert str(moved) in Config.load(app.config["LITE"].data_dir).folders
    assert auth.get_user_by_name(c, "kutti").library == str(moved / "kids")
    app.config["SCANNER"].scan_once(c)
    row = c.execute("SELECT missing, visibility FROM assets WHERE id = ?", (target,)).fetchone()
    assert tuple(row) == (0, 2)                         # the same row, still hidden
    assert ids(app)["a.jpg"] == target


def test_a66_restore_names_the_folders_this_computer_does_not_have(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: False)
    source = tmp_path / "old-pc"
    gone = str(tmp_path / "E-drive" / "Photos")
    Config(data_dir=str(source), folders=[gone]).save()
    conn = db.connect(source)
    db.sync_folders(conn, [gone])
    conn.close()
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(source, bundle)
    assert cli.main(["--data", str(tmp_path / "new-pc"), "--restore", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert f"Not found on this computer: {gone}" in out and '"Moved?"' in out


# --- A67, A68 (B03, B04): a restore makes thumbnails again and brings back no sessions ------


def test_a67_restore_makes_thumbnails_again(tmp_path, library):
    _root, data = scanned_data(tmp_path, library)
    conn = db.connect(data)
    made = {r["id"]: r["thumb_v"] for r in conn.execute(
        "SELECT id, thumb_v FROM assets WHERE thumb = 1")}
    conn.close()
    assert made and any((data / "thumbs").rglob("*"))
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(data, bundle)
    backups.restore(data, bundle)
    conn = db.connect(data)
    after = {r["id"]: (r["thumb"], r["large"], r["thumb_v"]) for r in conn.execute(
        "SELECT id, thumb, large, thumb_v FROM assets")}
    conn.close()
    for asset, version in made.items():
        assert after[asset] == (0, 0, version + 1)
    # The old pictures, which may now belong to other ids, are not served.
    assert not (data / "thumbs").exists() or not any((data / "thumbs").rglob("*.*"))


def test_a68_restore_does_not_bring_back_sessions(tmp_path, library):
    _root, data = scanned_data(tmp_path, library)
    conn = db.connect(data)
    auth.start_session(conn, auth.get_user_by_name(conn, "appa").id)
    conn.close()
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(data, bundle)
    backups.restore(data, bundle)
    conn = db.connect(data)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert auth.get_user_by_name(conn, "appa") is not None     # people are kept
    conn.close()


# --- A69 (B05): the server holds a lock; a restore looks at it -----------------------------


def test_a69_the_server_lock_is_seen_from_another_process(tmp_path):
    data = tmp_path / "data"
    assert not lock.held_elsewhere(data)
    with held_by_another_process(data):
        assert lock.held_elsewhere(data)
        assert lock.acquire(data) is False
    assert not lock.held_elsewhere(data)
    try:
        assert lock.acquire(data) is True
        assert not lock.held_elsewhere(data)          # this process's own lock
    finally:
        lock.release(data)


def test_a69_restore_is_refused_while_a_server_holds_the_lock(tmp_path, library, monkeypatch,
                                                             capsys):
    # Nothing answers on 127.0.0.1 (a server on another address, or in a container).
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: False)
    _root, data = scanned_data(tmp_path, library)
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(data, bundle)
    with held_by_another_process(data):
        assert cli.main(["--data", str(data), "--restore", str(bundle)]) == 2
    assert "is running" in capsys.readouterr().err
    assert not list(data.glob("before-restore-*"))
    assert cli.main(["--data", str(data), "--restore", str(bundle)]) == 0
    assert not lock.held_elsewhere(data) and str(data.resolve()) not in lock._held


# --- A70 (B06, F3): why it did not start is in the log too --------------------------------


def test_a70_a_refused_start_is_written_to_the_log(tmp_path, capsys, clean_logging):
    db.connect(tmp_path).close()
    plain = sqlite3.connect(str(tmp_path / db.DB_FILE))
    plain.execute(f"PRAGMA user_version={len(db.MIGRATIONS) + 1}")
    plain.commit()
    plain.close()
    assert cli.main(["--data", str(tmp_path), "--no-browser"]) == 2
    log = (tmp_path / "logs" / "ninaivu-lite.log").read_text(encoding="utf-8")
    assert "not starting" in log and "newer Ninaivu Lite" in log
    assert capsys.readouterr().err.count("newer Ninaivu Lite") == 1     # said once on the console


def test_a70_a_server_that_crashes_says_why_in_the_log(tmp_path, monkeypatch, clean_logging):
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: False)

    def serve(app, host, port):
        raise RuntimeError("could not listen")

    monkeypatch.setattr(cli, "serve", serve)
    data = tmp_path / "data"
    with pytest.raises(RuntimeError):
        cli.main(["--no-browser", "--host", "127.0.0.1", "--data", str(data)])
    log = (data / "logs" / "ninaivu-lite.log").read_text(encoding="utf-8")
    assert "the web server stopped with an error" in log and "could not listen" in log
    assert str(data.resolve()) not in lock._held                       # let go on the way out


def test_a70_the_panel_shows_what_a_refused_start_said(tmp_path):
    said = tmp_path / "last-start.txt"
    said.write_text("  The index here was made by a newer Ninaivu Lite.\n"
                    "  Update this one first.\n", encoding="utf-8")
    assert control.start_refusal(said) == ("The index here was made by a newer Ninaivu Lite. "
                                           "Update this one first.")
    assert control.start_refusal(tmp_path / "none.txt") == ""


# --- A71 (B07): only a whole Ninaivu Lite index is restored --------------------------------


def _index_before(data: Path) -> bytes:
    db.connect(data).close()
    return (data / db.DB_FILE).read_bytes()


def test_a71_restore_refuses_an_empty_or_foreign_index(tmp_path):
    data = tmp_path / "data"
    before = _index_before(data)
    empty = tmp_path / "empty.db"
    plain = sqlite3.connect(str(empty))
    plain.execute("CREATE TABLE t (x)")
    plain.commit()
    plain.close()
    foreign = tmp_path / "foreign.db"
    plain = sqlite3.connect(str(foreign))
    plain.execute("CREATE TABLE photos (path TEXT)")
    plain.execute("PRAGMA user_version = 3")
    plain.commit()
    plain.close()
    for index in (empty, foreign):
        zipped = tmp_path / f"{index.stem}.zip"
        with zipfile.ZipFile(zipped, "w") as z:
            z.write(index, db.DB_FILE)
        for bundle in (index, zipped):
            with pytest.raises(backups.RestoreError, match="not a Ninaivu Lite index"):
                backups.restore(data, bundle)
    assert (data / db.DB_FILE).read_bytes() == before
    assert not list(data.glob("before-restore-*"))


def test_a71_a_damaged_zip_is_refused_with_a_message(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    before = _index_before(data)
    source = tmp_path / "source"
    conn = db.connect(source)
    db.sync_folders(conn, [f"/photos/{n}" for n in range(3000)])
    conn.close()
    good = tmp_path / "good.zip"
    backups.write_bundle(source, good)
    raw = bytearray(good.read_bytes())
    start = 30 + len(db.DB_FILE)                 # past the first entry's local header
    for offset in range(start + 200, start + 600):
        raw[offset] ^= 0x5A
    damaged = tmp_path / "damaged.zip"
    damaged.write_bytes(bytes(raw))
    with pytest.raises(backups.RestoreError, match="damaged"):
        backups.restore(data, damaged)
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: False)
    assert cli.main(["--data", str(data), "--restore", str(damaged)]) == 2
    assert "damaged" in capsys.readouterr().err
    assert (data / db.DB_FILE).read_bytes() == before


def test_a71_a_zip_too_large_for_the_disk_is_refused(tmp_path, monkeypatch):
    data = tmp_path / "data"
    before = _index_before(data)
    bundle = tmp_path / "backup.zip"
    backups.write_bundle(data, bundle)
    monkeypatch.setattr(backups.shutil, "disk_usage",
                        lambda path: SimpleNamespace(total=1, used=0, free=1024))
    with pytest.raises(backups.RestoreError, match="MB free"):
        backups.restore(data, bundle)
    assert (data / db.DB_FILE).read_bytes() == before


# --- A72 (B08): a full re-index reads every file again -------------------------------------


def test_a72_full_reindex_reads_every_file_and_makes_thumbnails_again(app, admin):
    c = conn_of(app)
    before = {r["id"]: r["thumb_v"] for r in c.execute("SELECT id, thumb_v FROM assets")}
    assert admin.post("/api/scan", json={}).status_code == 200
    assert {r["id"]: r["thumb_v"] for r in c.execute("SELECT id, thumb_v FROM assets")} == before
    assert admin.post("/api/scan", json={"full": True}).status_code == 200
    rows = c.execute("SELECT id, mtime, thumb_v FROM assets").fetchall()
    assert all(r["mtime"] == -1 and r["thumb_v"] == before[r["id"]] + 1 for r in rows)
    app.config["SCANNER"].scan_once(c)
    assert c.execute("SELECT COUNT(*) FROM assets WHERE mtime = -1").fetchone()[0] == 0


# --- A73 (B09): weekly and monthly copies, a copy before a removal, failures shown ---------


def test_a73_older_copies_are_kept_weekly_and_monthly():
    days = [date(2026, 10, 7) - timedelta(days=n) for n in range(130)]
    kept = [Path(f"ninaivu-lite-{d:%Y-%m-%d}.zip") for d in days]       # newest first
    pruned = set(backups._to_prune(kept))
    left = {p.name for p in kept if p not in pruned}
    assert {p.name for p in kept[:backups.KEEP]} <= left
    # A week of bad copies does not push out every good one.
    for name in ("ninaivu-lite-2026-09-27.zip", "ninaivu-lite-2026-09-20.zip",
                 "ninaivu-lite-2026-08-31.zip"):
        assert name in left, name
    assert "ninaivu-lite-2026-09-26.zip" not in left
    assert "ninaivu-lite-2026-06-30.zip" not in left
    assert len(left) <= backups.KEEP + backups.KEEP_WEEKLY + backups.KEEP_MONTHLY


def test_a73_copies_before_a_change_are_kept_to_five(tmp_path):
    data = tmp_path / "data"
    db.connect(data).close()
    folder = data / "backups"
    folder.mkdir()
    for n in range(1, 7):
        (folder / f"before-removing-folder-2026-01-0{n}-000000.zip").write_bytes(b"old")
    made = backups.before_change(data, "removing-folder")
    assert made is not None and made.is_file() and zipfile.is_zipfile(made)
    left = sorted(p.name for p in folder.glob("before-*.zip"))
    assert len(left) == backups.KEEP_BEFORE and made.name in left
    assert "before-removing-folder-2026-01-01-000000.zip" not in left


def test_a73_a_failed_daily_copy_is_remembered_until_one_works(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db.connect(data).close()

    class Twice:
        """The keeper's wait: the first one passes, the second one stops it."""

        def __init__(self):
            self.calls = 0

        def wait(self, timeout):
            self.calls += 1
            return self.calls > 1

    def broken(data_dir):
        raise OSError("the disk is full")

    monkeypatch.setattr(backups, "daily", broken)
    keeper = backups.Keeper(data)
    keeper._stop = Twice()
    keeper._run()
    failure = backups.last_failure(data)
    assert failure is not None and "the disk is full" in failure["why"]
    monkeypatch.setattr(backups, "daily", lambda data_dir: None)
    keeper._stop = Twice()
    keeper._run()
    assert backups.last_failure(data) is None


def test_a73_the_console_lists_the_copies_and_the_last_failure(app, admin, guest, library):
    _root, data = library
    backups.daily(data)
    backups.before_change(data, "removing-folder")
    (data / "backups" / backups.FAILURE_FILE).write_text("OSError: no room", encoding="utf-8")
    r = admin.get("/api/admin/backups")
    assert r.status_code == 200
    body = r.get_json()
    assert sorted(b["before"] for b in body["backups"]) == [False, True]
    assert body["failure"]["why"] == "OSError: no room"
    assert body["folder"] == os.path.join(str(data), "backups")
    assert guest.get("/api/admin/backups").status_code in (401, 403)
    assert "Keep it private" in read("ninaivu_lite/templates/admin.html")


# --- A74 (B10): a bare .db; a zip without settings -----------------------------------------


def test_a74_a_bare_index_copy_is_accepted(tmp_path, library):
    _root, data = scanned_data(tmp_path, library)
    old = tmp_path / "ninaivu-lite-2026-01-01.db"
    backups.snapshot(data, old)
    conn = db.connect(data)
    conn.execute("UPDATE users SET display_name = 'CHANGED LATER'")
    conn.commit()
    conn.close()
    aside = backups.restore(data, old)
    conn = db.connect(data)
    assert conn.execute("SELECT display_name FROM users").fetchone()[0] == "Appa"
    conn.close()
    assert (aside / db.DB_FILE).is_file()


def test_a74_a_zip_without_settings_takes_its_folders_from_the_restored_index(tmp_path):
    source = tmp_path / "source"
    restored_folder = str(tmp_path / "Restored Photos")
    conn = db.connect(source)
    db.sync_folders(conn, [restored_folder])
    conn.close()
    bundle = tmp_path / "old.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.write(backups.snapshot(source, tmp_path / "copy.db"), db.DB_FILE)
    data = tmp_path / "data"
    Config(data_dir=str(data), folders=[str(tmp_path / "Other Photos")]).save()
    db.connect(data).close()
    aside = backups.restore(data, bundle)
    assert (aside / "settings.json").is_file()
    assert Config.load(data).folders == [restored_folder]


# --- A75 (B11): a setting is used only once it is saved ------------------------------------


def test_a75_a_setting_that_cannot_be_saved_is_not_applied(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), folders=["/a"], active="/a")
    cfg.save()

    def full_disk(self):
        raise OSError("no space left on the disk")

    monkeypatch.setattr(Config, "save", full_disk)
    with pytest.raises(OSError):
        cfg.update(folders=["/a", "/b"], active="/b", watch=not cfg.watch)
    assert cfg.folders == ["/a"] and cfg.active == "/a"
    watch = cfg.watch
    monkeypatch.undo()
    cfg.update(folders=["/a", "/b"], active="/b")
    assert cfg.folders == ["/a", "/b"] and cfg.active == "/b" and cfg.watch == watch
    assert Config.load(tmp_path).folders == ["/a", "/b"]


def test_a75_a_folder_removed_while_the_disk_is_full_stays_in_the_library(app, admin, library,
                                                                           monkeypatch):
    root, _data = library
    cfg = app.config["LITE"]

    def full_disk(self):
        raise OSError("no space left on the disk")

    monkeypatch.setattr(Config, "save", full_disk)
    r = admin.delete("/api/admin/libraries", query_string={"path": str(root)})
    assert r.status_code >= 500
    assert cfg.folders == [str(root)] and cfg.active == str(root)
    assert conn_of(app).execute("SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0] == 6


# --- A76 (B12): a library folder that is suddenly empty is unreachable, not emptied ---------


def test_a76_an_emptied_library_root_is_unreachable_not_removed(tmp_path, library):
    root, data = library
    conn = db.connect(data)
    scanner = Scanner(str(data), [str(root)])
    scanner.scan_once(conn)
    count = conn.execute("SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0]
    assert count > 0
    # A drive unplugged; its mount point stays behind, empty.
    shutil.move(str(root), str(tmp_path / "unplugged"))
    root.mkdir()
    scanner.scan_once(conn)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0] == count
    assert scanner.snapshot()["unreachable"] == [str(root)]
    conn.close()


# --- A77 (B13): removing a folder says what really happens to the people assigned ----------


def test_a77_removing_a_folder_says_assignments_are_kept(app, admin, library, tmp_path):
    more = tmp_path / "More"
    make_jpeg(more / "a.jpg")
    admin.post("/api/library/root", json={"path": str(more)})
    auth.create_user(conn_of(app), "kutti", name="Kutti", role="family", library=str(more))
    r = admin.delete("/api/admin/libraries", query_string={"path": str(more)})
    assert r.status_code == 409
    assert "forgets" not in r.get_json()["error"]
    js = read("ninaivu_lite/static/js/admin.js")
    assert "assignments cleared" not in js and "clear those assignments" not in js
    assert "see nothing until you reassign them" in js
    for lang in ("en", "ta"):
        strings = json.loads(read(f"ninaivu_lite/static/i18n/{lang}.json"))
        assert "{name} removed; assignments cleared." not in strings
    r = admin.delete("/api/admin/libraries", query_string={"path": str(more), "force": "1"})
    assert r.status_code == 200
    assert auth.get_user_by_name(conn_of(app), "kutti").library == str(more)


# --- A103 (F1): a server is this library's only if it says this data folder's name ---------


def test_a103_each_data_folder_has_a_name_the_server_says(app, tmp_path):
    data = tmp_path / "somewhere"
    assert lock.known_instance(data) is None
    made = lock.instance_id(data)
    assert re.fullmatch(r"[0-9a-f]{16}", made)
    assert lock.instance_id(data) == made == lock.known_instance(data)
    app.config["INSTANCE"] = made
    assert app.test_client().get("/api/health").get_json()["instance"] == made


def test_a103_another_copys_server_is_not_already_running(monkeypatch):
    fake_health(monkeypatch, {"app": "Ninaivu Lite", "instance": "theirs"})
    assert net.already_running(8080, "mine") is False
    assert net.already_running(8080, "theirs") is True
    # A folder that has no name yet has never had a server that names one:
    # one that does is another copy's (a portable data folder beside an
    # installed copy's server).
    assert net.already_running(8080, None) is False
    fake_health(monkeypatch, {"app": "Ninaivu Lite"})   # a server from before names
    assert net.already_running(8080, "mine") is True
    fake_health(monkeypatch, {"app": "Something else", "instance": "mine"})
    assert net.already_running(8080, "mine") is False


def test_a103_the_panel_does_not_take_another_copys_server_for_its_own(tmp_path, monkeypatch):
    data = tmp_path / "data"
    mine = lock.instance_id(data)
    c = control.Controller(str(data))
    fake_health(monkeypatch, {"app": "Ninaivu Lite", "instance": "an-older-copy"})
    assert c.health() is None and not c.running()
    fake_health(monkeypatch, {"app": "Ninaivu Lite", "instance": mine})
    assert c.health() is not None and c.running()


def test_a_fresh_portable_panel_does_not_take_the_installed_copys_server(tmp_path, monkeypatch):
    """A portable copy's panel, on a data folder no server has named yet,
    showed the installed copy's server on port 8080 as its own: Running,
    "Started from its own window: stop it there.", and Start said it was
    already running."""
    c = control.Controller(str(tmp_path / "data"))
    assert lock.known_instance(c.data_dir) is None
    fake_health(monkeypatch, {"app": "Ninaivu Lite", "instance": "the-installed-copy"})
    assert c.health() is None and not c.running()
    fake_health(monkeypatch, {"app": "Ninaivu Lite"})    # a server from before names
    assert c.running()


def test_a103_a_start_on_the_port_of_another_copy_does_not_stop(tmp_path, monkeypatch,
                                                              clean_logging):
    data = tmp_path / "data"
    asked, served = [], {}
    monkeypatch.setattr(net, "already_running",
                        lambda port, instance=None: asked.append(instance) or False)

    def serve(app, host, port):
        served["instance"] = app.config["INSTANCE"]

    monkeypatch.setattr(cli, "serve", serve)
    assert cli.main(["--no-browser", "--host", "127.0.0.1", "--data", str(data)]) == 0
    assert served["instance"] == lock.known_instance(data)
    assert cli.main(["--no-browser", "--host", "127.0.0.1", "--data", str(data)]) == 0
    assert asked[-1] == served["instance"]          # the second start asked about this folder


def test_a103_a_shortcut_that_starts_another_copy_is_not_this_ones(tmp_path, monkeypatch):
    portable = tmp_path / "new" / "Ninaivu Lite" / "pkgs"
    portable.mkdir(parents=True)
    appdata = tmp_path / "AppData"
    startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(control, "ROOT", portable)
    monkeypatch.setenv("APPDATA", str(appdata))
    own = startup / control.STARTUP_NAMES["portable"]
    old_root = tmp_path / "old" / "Ninaivu Lite" / "pkgs"
    own.write_text(f'shell.CurrentDirectory = "{old_root}"\r\nshell.Run """x"" --data '
                   f'""{tmp_path / "old" / "data"}""", 0, False\r\n', encoding="utf-8")
    c = control.Controller(str(tmp_path / "new" / "data"))
    assert not c.autostart_enabled() and c.autostart_elsewhere()
    c.set_autostart(True)
    assert c.autostart_enabled() and not c.autostart_elsewhere()
    readme = flat(read("installers/windows/portable/README-PORTABLE.txt"))
    assert "in the old folder's Control Panel, press Stop, untick" in readme


# --- A104 (F2): the sign-in script is written so Windows reads any letters ----------------


def test_a104_the_sign_in_script_is_utf16(tmp_path, monkeypatch):
    portable = tmp_path / "Ninaivu Lite" / "pkgs"
    portable.mkdir(parents=True)
    appdata = tmp_path / "AppData"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(control, "ROOT", portable)
    monkeypatch.setenv("APPDATA", str(appdata))
    data = tmp_path / "நினைவு José" / "data"
    c = control.Controller(str(data))
    c.set_autostart(True)
    script = c._autostart_path()
    raw = script.read_bytes()
    assert raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    assert f'""{data}""' in raw.decode("utf-16")
    assert c.autostart_enabled()
    cmd = read("tools/start-with-windows.cmd")
    assert "echo Set shell" not in cmd and "-m ninaivu_lite.control --autostart on" in cmd


# --- A105 (F4): a second server on the same data folder does not start -------------------


def test_a105_a_second_server_on_one_data_folder_is_refused(tmp_path, monkeypatch,
                                                           clean_logging, capsys):
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: False)
    monkeypatch.setattr(cli, "serve", lambda app, host, port: pytest.fail("started"))
    data = tmp_path / "data"
    data.mkdir()
    state = json.dumps({"pid": 4242, "port": 8123, "token": "the running one's"})
    (data / control.STATE_FILE).write_text(state, encoding="utf-8")
    with held_by_another_process(data):
        assert cli.main(["--no-browser", "--host", "127.0.0.1", "--data", str(data)]) == 2
    assert "already running" in capsys.readouterr().err
    # The running one's state (its port, its Stop token) is left as it was.
    assert (data / control.STATE_FILE).read_text(encoding="utf-8") == state
    assert "already running" in (data / "logs" / "ninaivu-lite.log").read_text(encoding="utf-8")


# --- A106 (F5): a free port is one free where the server will really listen --------------


def test_a106_pick_port_probes_the_address_it_will_bind(monkeypatch):
    probed = []

    def port_is_free(host, port):
        probed.append((host, port))
        return not (host == "0.0.0.0" and port == 8080)    # taken on the network address

    monkeypatch.setattr(net, "port_is_free", port_is_free)
    assert net.pick_port("0.0.0.0", 8080) == 8081
    assert ("0.0.0.0", 8080) in probed
    assert net.pick_port("", 8080) == 8081
    assert net.pick_port("127.0.0.1", 8080) == 8080


# --- A107 (F6): a folder named at every start does not come back once removed -------------


def test_a107_a_folder_removed_on_the_admin_page_is_not_added_again(tmp_path, capsys):
    data, photos, other = tmp_path / "data", tmp_path / "Photos", tmp_path / "Other"
    photos.mkdir()
    other.mkdir()
    path = str(photos.resolve())
    conn = db.connect(data)
    db.sync_folders(conn, [path])
    db.sync_folders(conn, [])                    # taken out on the Admin page
    conn.close()
    cfg = Config(data_dir=str(data), folders=[])
    assert cli.add_folders(cfg, [str(photos)]) == []
    assert cfg.folders == []
    assert "taken out of the library" in capsys.readouterr().err
    assert cli.add_folders(cfg, [str(other)]) == [str(other.resolve())]


# --- A108 (F7): a name this computer does not answer to gets a page, in both languages ---


def test_a108_an_unknown_host_gets_a_page_in_english_and_tamil(app, caplog):
    import uuid
    name = f"nas-{uuid.uuid4().hex[:8]}.example"
    client = app.test_client()
    with caplog.at_level(logging.WARNING):
        r = client.get("/", headers={"Host": name})
        client.get("/admin", headers={"Host": name})
    assert r.status_code == 400 and r.mimetype == "text/html"
    page = r.get_data(as_text=True)
    assert name in page and "allowed_hosts" in page and "நினைவு லைட்" in page
    assert sum(name in rec.getMessage() for rec in caplog.records) == 1    # said once
    api = client.get("/api/me", headers={"Host": name})
    assert api.status_code == 400 and "error" in api.get_json()


# --- A109 (F8): on Linux the panel's box is the systemd user service when there is one ---


def test_a109_the_sign_in_box_follows_the_systemd_user_service(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    unit = config / "systemd" / "user" / "ninaivu-lite.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n", encoding="utf-8")
    c = control.Controller(str(tmp_path / "data"))
    assert not c.autostart_enabled()
    wants = unit.parent / "default.target.wants"
    wants.mkdir()
    (wants / unit.name).write_text("", encoding="utf-8")
    assert c.autostart_enabled()
    calls = []
    monkeypatch.setattr(control.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    c.set_autostart(False)
    c.set_autostart(True)
    assert calls == [["systemctl", "--user", "disable", "ninaivu-lite"],
                     ["systemctl", "--user", "enable", "ninaivu-lite"]]
    # No second starter beside the service.
    assert not (config / "autostart" / control.LINUX_AUTOSTART).exists()


# --- A110 (F9): an upgrade starts again what was running ----------------------------------


def test_a110_the_windows_upgrade_starts_it_again_if_it_was_running():
    nsi = read("installers/windows/ninaivu-lite.nsi")
    asked = nsi.index("-m ninaivu_lite.control --running")
    assert asked < nsi.index('RMDir /r "$INSTDIR\\Python"')
    section = nsi[nsi.index('Section "-Start again after an upgrade"'):]
    section = section[:section.index("SectionEnd")]
    assert "StrCmp $nl_was_running \"1\"" in section
    assert "-m ninaivu_lite.control --start" in section


def test_a110_the_control_commands_the_installers_use(tmp_path, monkeypatch, capsys):
    data = str(tmp_path / "data")
    monkeypatch.setattr(control.Controller, "running", lambda self: False)
    assert control.main(["--data", data, "--running"]) == 1
    monkeypatch.setattr(control.Controller, "running", lambda self: True)
    assert control.main(["--data", data, "--running"]) == 0
    monkeypatch.setattr(control.Controller, "start", lambda self: "Ninaivu Lite is running.")
    assert control.main(["--data", data, "--start"]) == 0
    assert "Ninaivu Lite is running." in capsys.readouterr().out


def stand_in_payload(root: Path) -> Path:
    """A payload whose Python does what the environment says: FAIL_PIP makes
    the install fail, WAS_RUNNING answers --running (until --stop, unless
    STUCK: started in a terminal, which --stop cannot stop), NEEDS_SETUP
    --needs-setup."""
    payload = root / "payload"
    python = payload / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\ncase "$*" in\n'
                      '  *"pip install"*) [ -z "$FAIL_PIP" ] || exit 1 ;;\n'
                      '  *--running*) [ -n "$WAS_RUNNING" ] && [ ! -e "$0.stopped" ] || exit 1 ;;\n'
                      '  *--stop*) [ -n "$STUCK" ] || touch "$0.stopped" ;;\n'
                      '  *--start*) rm -f "$0.stopped" ;;\n'
                      '  *--needs-setup*) [ -n "$NEEDS_SETUP" ] || exit 1 ;;\n'
                      'esac\necho /nowhere/icon-192.png\n', encoding="utf-8")
    python.chmod(0o755)
    (payload / "wheels").mkdir()
    (payload / "wheels" / "ninaivu_lite-0-py3-none-any.whl").write_bytes(b"")
    for name, text in (("VERSION", "1.6.1\n"), ("LICENSE", "licence\n"), ("README.md", "readme\n")):
        (payload / name).write_text(text, encoding="utf-8")
    return payload


def user_install_env(tmp_path: Path, **extra: str) -> tuple[dict, Path, Path]:
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the Linux installer needs a POSIX shell")
    home, shims, calls = tmp_path / "home", tmp_path / "shims", tmp_path / "calls.log"
    shims.mkdir()
    for name, body in (("id", '[ "$1" = "-u" ] && echo 1000 || echo person\n'),
                       ("systemctl", f'echo "systemctl $*" >> "{calls}"\nexit 0\n'),
                       ("loginctl", "exit 0\n"), ("hostname", "echo 192.168.1.20\n")):
        (shims / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
        (shims / name).chmod(0o755)
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"),
           "XDG_CONFIG_HOME": str(home / ".config"),
           "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}", **extra}
    for key in ("FAIL_PIP", "WAS_RUNNING", "NEEDS_SETUP", "STUCK"):
        if key not in extra:
            env.pop(key, None)
    return env, home, calls


def test_a110_a_failed_linux_upgrade_leaves_the_old_one_running(tmp_path):
    env, home, calls = user_install_env(tmp_path, FAIL_PIP="1", WAS_RUNNING="1")
    payload = stand_in_payload(tmp_path)
    prefix = home / ".local" / "lib" / "ninaivu-lite"
    (prefix / "python").mkdir(parents=True)
    shutil.copytree(payload / "python" / "bin", prefix / "python" / "bin")   # the earlier one
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "did not finish" in done.stderr and "started again" in done.stderr
    assert not (prefix / "python.new").exists() and (prefix / "python" / "bin" / "python3").is_file()
    log = calls.read_text().splitlines()
    assert log.index("systemctl --user stop ninaivu-lite") \
        < log.index("systemctl --user start ninaivu-lite")


# --- A111 (F10): Stop asks first when it would cut a copy short ----------------------------


def test_a111_health_says_what_the_server_is_in_the_middle_of(app):
    client = app.test_client()
    assert client.get("/api/health").get_json()["busy"] is None
    app.config["EXPORTER"] = SimpleNamespace(running=True)
    assert client.get("/api/health").get_json()["busy"] == "export"
    with app.app_context():
        assert pages.busy() == "export"


def test_a111_the_panel_asks_before_stopping_a_copy(tmp_path, monkeypatch, capsys):
    asked, ran = [], []
    fake = SimpleNamespace(controller=SimpleNamespace(busy=lambda: "import"),
                           ask=lambda q: asked.append(q) or False,
                           run=lambda action, doing: ran.append(doing))
    panel.Panel.stop_asking(fake, None, "Stopping…")
    assert ran == [] and "an import into the archive" in asked[0]
    fake.ask = lambda q: True
    panel.Panel.stop_asking(fake, None, "Stopping…")
    assert ran == ["Stopping…"]
    fake.controller = SimpleNamespace(busy=lambda: None)
    fake.ask = lambda q: pytest.fail("asked with nothing to cut short")
    panel.Panel.stop_asking(fake, None, "Restarting…")
    assert ran[-1] == "Restarting…"

    c = control.Controller(str(tmp_path / "data"))
    fake_health(monkeypatch, {"app": "Ninaivu Lite", "busy": "phone"})
    assert c.busy() == "phone"
    monkeypatch.setattr(control.Controller, "busy", lambda self: "export")
    monkeypatch.setattr(control.Controller, "stop", lambda self: "Ninaivu Lite stopped.")
    assert control.main(["--data", str(tmp_path / "data"), "--stop"]) == 0
    assert "in the middle of copying to a drive" in capsys.readouterr().out


# --- A112 (F11): the panel reads the folder list again --------------------------------------


def test_a112_the_panel_shows_folders_added_while_it_is_open(tmp_path):
    data = tmp_path / "data"
    Config(data_dir=str(data), folders=[]).save()
    c = control.Controller(str(data))
    assert c.library_summary()["folders"] == []
    added = str(tmp_path / "Photos")
    Config(data_dir=str(data), folders=[added]).save()       # the first-day wizard, meanwhile
    assert c.current_folders() == [added]
    assert c.library_summary()["folders"] == [added]


# --- A113 (F12): the index is read from a folder whose name has '#' or '%' ----------------


def test_a113_the_panel_reads_an_index_in_a_folder_named_with_hash(tmp_path):
    data = tmp_path / "Photos #1 at 100%"
    conn = db.connect(data)
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    conn.close()
    control.write_state(data, 8123, "C0DE1234AB")
    c = control.Controller(str(data))
    assert c.setup_code() is None and not c.needs_setup()
    assert c.library_summary()["items"] == 0
    ro = control.read_only(data / db.DB_FILE)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM users")
    finally:
        ro.close()
    with pytest.raises(sqlite3.OperationalError):
        control.read_only(tmp_path / "nothing" / db.DB_FILE)
    assert not (tmp_path / "nothing").exists()


# --- A114 (F13): the launcher says how to get the panel when Python has no Tk --------------


def test_a114_the_launcher_says_how_to_get_tk(monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("start_for_a114", ROOT / "launcher" / "start.py")
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    opened = []
    monkeypatch.setattr(start.subprocess, "call", lambda *a, **k: 1)      # import tkinter fails
    monkeypatch.setattr(start.subprocess, "Popen", lambda *a, **k: opened.append(a))
    assert start.open_panel(Path(sys.executable), []) is False
    assert opened == [] and "python3-tk" in capsys.readouterr().out
    monkeypatch.setattr(start.subprocess, "call", lambda *a, **k: 0)
    assert start.open_panel(Path(sys.executable), []) is True and opened


# --- A115 (F14): the install summary gives the real port; uninstalls say what was kept ----


def test_a115_the_summary_gives_the_port_it_really_uses(tmp_path):
    env, home, _calls = user_install_env(tmp_path)          # set up already: not NEEDS_SETUP
    payload = stand_in_payload(tmp_path)
    data = home / ".local" / "share" / "ninaivu-lite"
    data.mkdir(parents=True)
    (data / "server.json").write_text('{"pid": 1, "port": 8123, "token": "t"}', encoding="utf-8")
    done = subprocess.run(["sh", str(INSTALL), str(payload)], env=env, check=True,
                          capture_output=True, text=True)
    assert "http://192.168.1.20:8123" in done.stdout and ":8080" not in done.stdout
    assert "as they were" in done.stdout and "Setup code" not in done.stdout


def test_a115_uninstalling_says_the_data_is_kept():
    script = read("installers/linux/install.sh")
    wrap = script[script.index('cat > "$prefix/uninstall"'):]
    wrap = wrap[:wrap.index("\nWRAP\n")]
    assert "are kept in $data" in wrap and "--purge" in wrap
    nsi = read("installers/windows/ninaivu-lite.nsi")
    section = nsi[nsi.index('Section "un.Say what was kept"'):]
    section = section[:section.index("SectionEnd")]
    assert "kept in $LOCALAPPDATA\\Ninaivu-lite" in section and "IfSilent" in section
