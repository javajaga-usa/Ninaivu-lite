"""Regression tests for the project audit of 2026-10-05 (findings A01-A10).

Each test sets up the scenario the audit reproduced and checks that it no
longer happens."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from conftest import ids

from ninaivu_lite import db, importer
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

from test_import import noisy_jpeg, rows, run, sha


def conn_of(app) -> sqlite3.Connection:
    return db.connect(app.config["LITE"].data_dir)


def make_share(client, scope: str, target: int) -> str:
    r = client.post("/api/shares", json={"scope": scope, "target_id": target})
    assert r.status_code == 200, r.json
    return r.json["token"]


# --- A01: a share link never outlives what it points at ------------------------------------


def _reuse_id(c: sqlite3.Connection, asset_id: int, name: str) -> None:
    """Delete an asset and index a different photograph under the same id,
    as SQLite may do once a row is gone."""
    row = dict(c.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone())
    with c:
        c.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        row.update(name=name, visibility=db.VIS_FAMILY)
        cols = ", ".join(row)
        c.execute(f"INSERT INTO assets ({cols}) VALUES ({', '.join('?' * len(row))})",
                  list(row.values()))


def test_a01_asset_link_does_not_follow_a_reused_id(app, admin):
    target = ids(app)["beach.jpg"]
    token = make_share(admin, "asset", target)
    assert app.test_client().get(f"/api/share/{token}").status_code == 200
    c = conn_of(app)
    _reuse_id(c, target, "someone-else.jpg")
    assert c.execute("SELECT name FROM assets WHERE id = ?", (target,)).fetchone()[0] \
        == "someone-else.jpg"
    stranger = app.test_client()
    assert stranger.get(f"/api/share/{token}").status_code == 404
    assert stranger.get(f"/api/share/{token}/file/{target}").status_code == 404
    assert c.execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 0


def test_a01_removing_a_library_folder_ends_its_links(app, admin):
    token = make_share(admin, "asset", ids(app)["beach.jpg"])
    c = conn_of(app)
    folders = [r[0] for r in c.execute("SELECT path FROM folders")]
    db.sync_folders(c, [])            # the folder taken out of the library
    assert app.test_client().get(f"/api/share/{token}").status_code == 404
    # Set aside, not deleted (index version 9): the same folder added again
    # and scanned brings the link back to the same photograph.
    db.sync_folders(c, folders)
    app.config["SCANNER"].scan_once(c)
    assert app.test_client().get(f"/api/share/{token}").status_code == 200


def test_a01_album_link_does_not_follow_a_reused_id(app, family):
    i = ids(app)
    first = family.post("/api/albums", json={"name": "Ours", "ids": [i["beach.jpg"]]}
                        ).json["id"]
    token = make_share(family, "album", first)
    assert family.delete(f"/api/albums/{first}").status_code == 200
    second = family.post("/api/albums", json={"name": "Other", "ids": [i["sunset.jpg"]]}
                         ).json["id"]
    assert second == first             # the id really was handed out again
    assert app.test_client().get(f"/api/share/{token}").status_code == 404


def test_a01_upgrade_drops_links_already_pointing_at_nothing(tmp_path):
    c = db.connect(tmp_path)
    with c:
        c.execute("DROP TRIGGER shares_asset_gone")
        c.execute("DROP TRIGGER shares_album_gone")
        c.execute("INSERT INTO shares (token, scope, target_id) VALUES ('a', 'asset', 41)")
        c.execute("INSERT INTO shares (token, scope, target_id) VALUES ('b', 'album', 42)")
    c.execute("PRAGMA user_version = 6")      # before index version 7
    db.migrate(c)
    assert c.execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 0
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


# --- A02: damaged settings never empty the index -----------------------------------------------


def _indexed(library) -> tuple[Path, Path, int]:
    root, data = library
    cfg = Config(data_dir=str(data), folders=[str(root)], active=str(root), first_day_done=True)
    cfg.save()
    c = db.connect(data)
    Scanner(cfg.data_dir, cfg.folders).scan_once(c)
    count = c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    assert count > 0
    return root, data, count


def _scan_with_loaded_settings(data: Path) -> tuple[Config, int]:
    cfg = Config.load(data)
    c = db.connect(data)
    Scanner(cfg.data_dir, cfg.folders).scan_once(c)
    return cfg, c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]


def test_a02_malformed_settings_keep_the_index(library):
    root, data, count = _indexed(library)
    (data / "settings.json").write_text("{ not json", encoding="utf-8")
    cfg, after = _scan_with_loaded_settings(data)
    assert after == count
    assert cfg.folders == [str(root)] and cfg.recovered
    assert (data / "settings.json.damaged").read_text(encoding="utf-8") == "{ not json"
    # The recovered settings are written back, so the next start is ordinary.
    again = Config.load(data)
    assert again.folders == [str(root)] and not again.recovered


def test_a02_missing_or_non_object_settings_keep_the_index(library):
    root, data, count = _indexed(library)
    (data / "settings.json").unlink()
    cfg, after = _scan_with_loaded_settings(data)
    assert after == count and cfg.folders == [str(root)]
    (data / "settings.json").write_text("[1, 2]", encoding="utf-8")
    cfg, after = _scan_with_loaded_settings(data)
    assert after == count and cfg.folders == [str(root)]
    (data / "settings.json").write_text(json.dumps({"language": "ta"}), encoding="utf-8")
    cfg, after = _scan_with_loaded_settings(data)
    assert after == count and cfg.folders == [str(root)] and cfg.language == "ta"


def test_a02_a_fresh_data_folder_starts_empty(tmp_path):
    cfg = Config.load(tmp_path / "data")
    assert cfg.folders == [] and not cfg.recovered
    assert not (tmp_path / "data" / "settings.json").exists()


def test_a02_an_explicitly_empty_library_is_still_honoured(library):
    _root, data, _count = _indexed(library)
    cfg = Config.load(data)
    cfg.folders = []
    cfg.save()
    _cfg, _after = _scan_with_loaded_settings(data)
    c = db.connect(data)
    assert c.execute("SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0] == 0


# --- A03: resuming checks the source and the archived copy -------------------------------------


def test_a03_a_lost_archive_copy_is_copied_again(tmp_path):
    src = tmp_path / "src"
    photo = noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    archived = Path(rows(tmp_path / "data")["beach.jpg"]["destination"])
    archived.unlink()
    again = run(tmp_path / "data", [src], dest)
    assert again.job["stepped_over"] == 0
    assert archived.read_bytes() == photo.read_bytes()
    assert rows(tmp_path / "data")["beach.jpg"]["status"] == "verified"


def test_a03_a_changed_source_is_imported_again(tmp_path):
    src = tmp_path / "src"
    noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    first = Path(rows(tmp_path / "data")["beach.jpg"]["destination"])
    old = first.read_bytes()
    noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=7)
    os.utime(src / "beach.jpg", (time.time() + 5, time.time() + 5))
    again = run(tmp_path / "data", [src], dest)
    assert again.job["stepped_over"] == 0
    row = rows(tmp_path / "data")["beach.jpg"]
    assert row["status"] == "verified" and row["dest_hash"] == sha(src / "beach.jpg")
    assert sha(Path(row["destination"])) == sha(src / "beach.jpg")
    assert first.read_bytes() == old              # the earlier copy is kept, not overwritten


def test_a03_unchanged_work_is_still_stepped_over(tmp_path):
    src = tmp_path / "src"
    noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    again = run(tmp_path / "data", [src], dest)
    assert again.job["stepped_over"] == 1 and again.job["bytes_copied"] == 0


def test_a03_still_done_checks_source_and_copy(tmp_path):
    src = noisy_jpeg(tmp_path / "s.jpg", seed=1)
    copy = tmp_path / "copy.jpg"
    copy.write_bytes(src.read_bytes())
    st = src.stat()
    row = {"status": "verified", "destination": str(copy), "duplicate_of": None,
           "size": st.st_size, "mtime": st.st_mtime}
    assert importer.still_done(row, st)
    assert not importer.still_done({**row, "size": st.st_size + 1}, st)
    assert not importer.still_done({**row, "mtime": st.st_mtime - 60}, st)
    assert not importer.still_done({**row, "mtime": None}, st)
    copy.unlink()
    assert not importer.still_done(row, st)
    dup = {**row, "status": "duplicate", "destination": None, "duplicate_of": str(src)}
    assert importer.still_done(dup, st)


# --- A04: a duplicate needs a real, matching archived copy ----------------------------------------


def test_a04_duplicate_of_a_deleted_copy_is_imported(tmp_path):
    src = tmp_path / "src"
    photo = noisy_jpeg(src / "a" / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    archived = Path(rows(tmp_path / "data")["beach.jpg"]["destination"])
    archived.unlink()
    (src / "b").mkdir()
    (src / "b" / "beach again.jpg").write_bytes(photo.read_bytes())
    run(tmp_path / "data", [src / "b"], dest)
    row = rows(tmp_path / "data")["beach again.jpg"]
    assert row["status"] == "verified"
    assert Path(row["destination"]).read_bytes() == photo.read_bytes()


def test_a04_duplicate_of_a_corrupted_copy_is_imported(tmp_path):
    src = tmp_path / "src"
    photo = noisy_jpeg(src / "a" / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    archived = Path(rows(tmp_path / "data")["beach.jpg"]["destination"])
    archived.write_bytes(b"\0" * archived.stat().st_size)     # same size, other bytes
    (src / "b").mkdir()
    (src / "b" / "beach again.jpg").write_bytes(photo.read_bytes())
    run(tmp_path / "data", [src / "b"], dest)
    row = rows(tmp_path / "data")["beach again.jpg"]
    assert row["status"] == "verified"
    assert sha(Path(row["destination"])) == sha(photo)


def test_a04_same_bytes_reads_the_file_not_the_record(tmp_path):
    src = tmp_path / "src"
    noisy_jpeg(src / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    dest = tmp_path / "Archive"
    engine = run(tmp_path / "data", [src], dest)
    row = rows(tmp_path / "data")["beach.jpg"]
    archived = Path(row["destination"])
    archived.write_bytes(b"x" * archived.stat().st_size)
    c = db.connect(tmp_path / "data")
    assert not engine._same_bytes(c, str(archived), row["dest_hash"], row["size"])
    engine.job["destination"] = str(dest)
    assert engine._duplicate_of(c, row["hash"], "elsewhere", row["size"]) is None
    archived.unlink()
    assert engine._duplicate_of(c, row["hash"], "elsewhere", row["size"]) is None
