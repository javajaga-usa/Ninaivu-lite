"""Regression tests for the project audit of 2026-10-05, medium-priority
findings (A05-A10). Each test sets up the scenario the audit described and
checks that it no longer happens."""

from __future__ import annotations

import os
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest
from conftest import ids, make_jpeg

from ninaivu_lite import auth, backups, db, media, scanner as scanner_module
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

ROOT = Path(__file__).resolve().parent.parent


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


# --- A05: a video whose metadata cannot be removed is not sent to strangers --------------------


def test_a05_video_privacy_fails_closed(app, admin, guest, monkeypatch):
    target = ids(app)["clip.mp4"]
    assert admin.post("/api/visibility", json={"ids": [target], "visibility": "public"}
                      ).status_code == 200
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": target}).json["token"]
    original = admin.get(f"/api/file/{target}").data
    monkeypatch.setattr(media, "strip_video", lambda path, out: False)
    stranger = app.test_client()
    for client, url in ((guest, f"/api/file/{target}"),
                        (stranger, f"/api/share/{token}/file/{target}")):
        r = client.get(url)
        assert r.status_code == 415 and r.data != original
        assert "location" in r.json["error"]
    # Only an administrator's choice sends it as it is, and it says so.
    assert admin.post("/api/admin/settings", json={"video_originals": True}).status_code == 200
    assert Config.load(app.config["LITE"].data_dir).video_originals is True
    r = stranger.get(f"/api/share/{token}/file/{target}")
    assert r.status_code == 200 and r.data == original
    assert r.headers["X-Ninaivu-Metadata"] == "original"
    # The family's own view is unchanged: the original, as always.
    assert admin.get(f"/api/file/{target}").data == original


# --- A06: the thumbnail queue reaches every pending row ---------------------------------------


def _seed_pending(tmp_path: Path, count: int) -> tuple[Scanner, sqlite3.Connection]:
    root = tmp_path / "Photos"
    root.mkdir()
    c = db.connect(tmp_path / "data")
    folder = db.sync_folders(c, [str(root)])[str(root)]
    with c:
        c.executemany(
            "INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at, "
            "date_key, date_source) VALUES (?, '', ?, 'jpg', 'picture', 1, 0, ?, "
            "'2020-01-01', 'mtime')",
            [(folder, f"p{i:03}.jpg", 1_600_000_000 + (i % 7)) for i in range(count)])
    return Scanner(tmp_path / "data", [str(root)]), c


def test_a06_one_pass_makes_every_pending_thumbnail(tmp_path, monkeypatch):
    s, c = _seed_pending(tmp_path, 120)
    calls: list[tuple[int, tuple]] = []

    def made(row, sizes):
        calls.append((row["id"], sizes))
        return sizes, True, None

    monkeypatch.setattr(s, "_render", made)
    s._make_thumbnails(c)
    assert c.execute("SELECT COUNT(*) FROM assets WHERE thumb = 0").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM assets WHERE large = 0").fetchone()[0] == 0
    assert len(calls) == 240 and len(set(calls)) == 240      # each row once per pass


def test_a06_rows_that_fail_are_tried_once_and_do_not_hide_others(tmp_path, monkeypatch):
    s, c = _seed_pending(tmp_path, 130)
    tried: list[int] = []

    def made(row, sizes):
        tried.append(row["id"])
        if row["id"] % 3 == 0:
            return None                     # its drive is asleep: stays in the queue
        return ("s", "l"), True, None

    monkeypatch.setattr(s, "_render", made)
    s._make_thumbnails(c)
    left = {r[0] for r in c.execute("SELECT id FROM assets WHERE thumb = 0")}
    assert left == {i for i in range(1, 131) if i % 3 == 0}
    assert sorted(tried) == sorted(set(tried))                 # no row twice: no spinning


# --- A07: backups hold everything a restore needs --------------------------------------------


def test_a07_daily_and_downloaded_backups_restore_on_their_own(library, tmp_path):
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root), first_day_done=True)
    cfg.save()
    c = db.connect(data)
    Scanner(cfg.data_dir, cfg.folders).scan_once(c)
    person = auth.create_user(c, "amma", password="family passphrase", name="Amma",
                              role="family")
    auth.update_profile(c, person.id, avatar_at=1234.5)
    (data / "avatars").mkdir()
    (data / "avatars" / f"{person.id}-1234.jpg").write_bytes(b"\xff\xd8 a small square")

    made = backups.daily(data)
    assert made is not None and made.suffix == ".zip"
    assert backups.daily(data) is None                          # once a day
    for name, bundle in (("daily", made.read_bytes()), ("download", backups.download(data))):
        target = tmp_path / f"restored-{name}"
        target.mkdir()
        (target / "b.zip").write_bytes(bundle)
        with zipfile.ZipFile(target / "b.zip") as z:
            assert {"ninaivu-lite.db", "settings.json", "README.txt",
                    f"avatars/{person.id}-1234.jpg"} <= set(z.namelist())
            z.extractall(target / "data")
        restored = Config.load(target / "data")
        assert restored.folders == [str(root)]
        rc = db.connect(target / "data")
        assert rc.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == \
            c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        assert auth.get_user_by_name(rc, "amma").avatar_at == 1234.5
        assert (target / "data" / "avatars" / f"{person.id}-1234.jpg").is_file()


def test_a07_old_index_only_copies_are_pruned_with_the_new(tmp_path):
    db.connect(tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 10):
        (folder / f"ninaivu-lite-2020-01-{day:02}.db").write_bytes(b"old")
    backups.daily(tmp_path)
    kept = backups.listing(tmp_path)
    assert len(kept) == backups.KEEP and kept[0]["name"].endswith(".zip")


# --- A08: a folder that cannot be read is not a folder emptied ---------------------------------


def test_a08_unreadable_subfolder_keeps_its_photos(tmp_path, monkeypatch):
    root = tmp_path / "Photos"
    make_jpeg(root / "2019" / "trip" / "beach.jpg", "2019:05:12 10:00:00")
    make_jpeg(root / "top.jpg", "2019:05:12 10:00:00")
    c = db.connect(tmp_path / "data")
    s = Scanner(tmp_path / "data", [str(root)])
    s.scan_once(c)
    assert c.execute("SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0] == 2

    real = os.scandir
    blocked = str(root / "2019")

    def scandir(path="."):
        if os.path.normpath(str(path)).endswith(os.path.normpath(blocked)):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(scanner_module.os, "scandir", scandir)
    s.scan_once(c)
    assert c.execute("SELECT missing FROM assets WHERE name = 'beach.jpg'").fetchone()[0] == 0
    assert s.snapshot()["unreadable"] == 1 and s.snapshot()["removed"] == 0

    # Readable again and really gone: now it is missing.
    monkeypatch.setattr(scanner_module.os, "scandir", real)
    (root / "2019" / "trip" / "beach.jpg").unlink()
    s.scan_once(c)
    assert c.execute("SELECT missing FROM assets WHERE name = 'beach.jpg'").fetchone()[0] == 1
    assert s.snapshot()["unreadable"] == 0


def test_a08_under_any():
    assert scanner_module.under_any("2019/trip", "a.jpg", ["2019"])
    assert scanner_module.under_any("", "a.jpg", [""])
    assert scanner_module.under_any("2019", "a.jpg", ["2019/a.jpg"])
    assert not scanner_module.under_any("20190", "a.jpg", ["2019"])
    assert not scanner_module.under_any("2020", "a.jpg", ["2019"])


# --- A09: the system service never runs as root -----------------------------------------------


def read(path: str) -> str:
    file = ROOT / path
    if not file.exists():            # installers and workflows are not in every copy of the tree
        pytest.skip(f"{path} is not here")
    return file.read_text(encoding="utf-8")


def test_a09_root_install_runs_the_service_as_its_own_account():
    script = read("installers/linux/install.sh")
    unit = script[script.index("cat > \"$unit\" <<UNIT"):script.index("\nUNIT\n")]
    assert "$run_as" in unit.split("[Service]")[1].split("[Install]")[0]
    run_as = re.search(r'run_as="(User=\$account\n.*?)"', script, re.S)
    assert run_as, "the root branch must set User= for the service"
    for line in ("User=$account", "Group=$account", "NoNewPrivileges=yes"):
        assert line in run_as.group(1)
    assert 'chown -R "$account:$account" "$data"' in script
    # No account, no system service: never a fallback to root.
    assert "service=0" in script[script.index("if make_account; then"):]


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell")
def test_a09_install_script_parses():
    import shutil
    import subprocess
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh")
    subprocess.run([sh, "-n", str(ROOT / "installers" / "linux" / "install.sh")], check=True)


# --- A10: nothing is published unless the tests pass on that commit ----------------------------


def test_a10_release_needs_the_tests():
    tests = read(".github/workflows/tests.yml")
    release = read(".github/workflows/release.yml")
    assert re.search(r"^  workflow_call:", tests, re.M)
    job = re.search(r"^  tests:\n((?:    .*\n)+)", release, re.M)
    assert job and "uses: ./.github/workflows/tests.yml" in job.group(1)
    needs = re.search(r"^  release:\n(?:    .*\n)*?    needs: \[(.*)\]", release, re.M)
    assert needs and "tests" in [n.strip() for n in needs.group(1).split(",")]
