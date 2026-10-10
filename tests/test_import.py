"""The importer (ninaivu_lite/importer.py, takeout.py, api_import.py): drives
swept into one dated archive, every copy checked, sources never touched."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from ninaivu_lite import db, importer, takeout
from ninaivu_lite.importer import Importer



def noisy_jpeg(path: Path, taken: str | None = None, seed: int = 1, size=(640, 480)) -> Path:
    """A JPEG big enough to count (the importer leaves icons under 24 KB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.effect_noise(size, 60 + seed).convert("RGB")
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    img.save(path, "JPEG", exif=exif.tobytes(), quality=90)
    assert path.stat().st_size > importer.MIN_BYTES
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, tuple[int, float, str]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime, sha(p))
            for p in root.rglob("*") if p.is_file()}


def run(data: Path, sources: list[Path], dest: Path, mode: str = "copy") -> Importer:
    engine = Importer(data)
    engine.start([str(s) for s in sources], str(dest), ["image", "video"], mode)
    engine.wait(120)
    assert not engine.running
    return engine


def rows(data: Path) -> dict[str, dict]:
    c = db.connect(data)
    return {Path(r["source"]).name: dict(r) for r in c.execute("SELECT * FROM import_files")}


@pytest.fixture()
def drive(tmp_path):
    """An old drive: dated photos, one twice over, a mis-named one, junk."""
    src = tmp_path / "OldDrive"
    noisy_jpeg(src / "DCIM" / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    noisy_jpeg(src / "DCIM" / "sunset.jpg", "2019:05:12 18:30:00", seed=2)
    noisy_jpeg(src / "backup" / "IMG_20230115_091500.jpg", seed=3)          # date in the name
    (src / "backup" / "beach copy.jpg").write_bytes((src / "DCIM" / "beach.jpg").read_bytes())
    noisy_jpeg(src / "2008" / "07" / "scan.jpg", seed=4)                     # dated folder
    os.utime(src / "2008" / "07" / "scan.jpg", (time.time(), time.time()))
    dead = noisy_jpeg(src / "misc" / "dead-clock.jpg", seed=5)
    os.utime(dead, (0, 0))                                                   # 1970: no date
    noisy_jpeg(src / "misc" / "CLIP.DAT", "2012:03:04 05:06:07", seed=6)     # wrong extension
    (src / "misc" / "icon.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 100)   # too small
    (src / "misc" / "notes.txt").write_text("not a photo")
    (src / "misc" / "empty.jpg").write_bytes(b"")
    return src


# --- the engine -------------------------------------------------------------------


def test_files_land_by_the_day_they_were_taken(drive, tmp_path):
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [drive], dest)
    assert (dest / "2019" / "05" / "12" / "beach.jpg").is_file()
    assert (dest / "2019" / "05" / "12" / "sunset.jpg").is_file()
    assert (dest / "2023" / "01" / "15" / "IMG_20230115_091500.jpg").is_file()
    assert (dest / "2008" / "07" / "01" / "scan.jpg").is_file()        # folder beats a later copy
    assert (dest / importer.UNDATED / "dead-clock.jpg").is_file()
    assert (dest / "2012" / "03" / "04" / "CLIP.DAT").is_file()        # found by its bytes
    assert not list(dest.rglob("icon.jpg")) and not list(dest.rglob("notes.txt"))
    assert (dest / importer.MARKER).is_file()
    assert not (dest / importer.PARTIAL_DIR).exists()
    got = rows(tmp_path / "data")
    assert got["beach.jpg"]["date_source"] == "exif"
    assert got["IMG_20230115_091500.jpg"]["date_source"] == "filename"
    assert got["scan.jpg"]["date_source"] == "folder"
    assert "icon.jpg" not in got and "empty.jpg" not in got     # under the size floor


def test_every_copy_is_hash_checked_and_sources_are_untouched(drive, tmp_path):
    before = snapshot(drive)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [drive], dest)
    assert snapshot(drive) == before
    for row in rows(tmp_path / "data").values():
        if row["status"] == "verified":
            assert row["dest_hash"] == row["hash"] == sha(Path(row["destination"]))
            assert Path(row["destination"]).read_bytes() == Path(row["source"]).read_bytes()


def test_duplicates_are_left_in_place_and_logged(drive, tmp_path):
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [drive], dest)
    got = rows(tmp_path / "data")
    assert got["beach.jpg"]["status"] == "verified"
    assert got["beach copy.jpg"]["status"] == "duplicate"
    assert got["beach copy.jpg"]["duplicate_of"] == got["beach.jpg"]["destination"]
    assert not list(dest.rglob("beach copy.jpg"))
    assert (drive / "backup" / "beach copy.jpg").is_file()


def test_same_name_different_bytes_is_suffixed(tmp_path):
    src = tmp_path / "src"
    noisy_jpeg(src / "a" / "IMG_1.jpg", "2020:01:01 10:00:00", seed=1)
    noisy_jpeg(src / "b" / "IMG_1.jpg", "2020:01:01 11:00:00", seed=2)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest)
    day = dest / "2020" / "01" / "01"
    assert sorted(p.name for p in day.iterdir()) == ["IMG_1.jpg", "IMG_1_1.jpg"]


def test_dry_run_writes_nothing_and_predicts_the_real_run(drive, tmp_path):
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [drive], dest, "dry-run")
    assert not dest.exists()
    planned = rows(tmp_path / "data")
    assert planned["beach.jpg"]["status"] == "planned"
    assert planned["beach copy.jpg"]["status"] == "plan-duplicate"
    stats = importer.stats(db.connect(tmp_path / "data"))
    assert stats["planned"] == 6 and stats["plan_duplicates"] == 1
    run(tmp_path / "data", [drive], dest)
    real = rows(tmp_path / "data")
    for name, row in planned.items():
        if row["status"] == "planned":
            assert real[name]["destination"] == row["destination"]
            assert Path(row["destination"]).is_file()


def test_start_resumes_without_copying_again(drive, tmp_path):
    dest = tmp_path / "Archive"
    first = run(tmp_path / "data", [drive], dest)
    assert first.job["bytes_copied"] > 0
    archived = snapshot(dest)
    noisy_jpeg(drive / "DCIM" / "new.jpg", "2021:02:03 04:05:06", seed=9)
    second = run(tmp_path / "data", [drive], dest)
    assert second.job["stepped_over"] == 7
    assert second.job["bytes_copied"] == (drive / "DCIM" / "new.jpg").stat().st_size
    after = snapshot(dest)
    assert {k: v for k, v in after.items() if k in archived} == archived
    assert (dest / "2021" / "02" / "03" / "new.jpg").is_file()
    assert "already done" in second.job["message"]["text"]


def test_takeout_sidecar_gives_the_date_and_travels_along(tmp_path):
    src = tmp_path / "Takeout" / "Google Photos" / "Photos from 2016"
    photo = noisy_jpeg(src / "IMG_0001.jpg", seed=1)
    when = int(datetime(2016, 8, 15, 9, 30).timestamp())
    (src / "IMG_0001.jpg.json").write_text(json.dumps({
        "title": "IMG_0001.jpg", "description": "Appa at the temple",
        "photoTakenTime": {"timestamp": str(when)},
        "geoData": {"latitude": 13.0827, "longitude": 80.2707}}))
    (src / "IMG_0001.jpg.xmp").write_text("<x:xmpmeta/>")
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [tmp_path / "Takeout"], dest)
    day = dest / "2016" / "08" / "15"
    assert (day / "IMG_0001.jpg").read_bytes() == photo.read_bytes()
    assert (day / "IMG_0001.jpg.json").is_file() and (day / "IMG_0001.jpg.xmp").is_file()
    assert rows(tmp_path / "data")["IMG_0001.jpg"]["date_source"] == "takeout-json"


def test_audit_finds_a_copy_that_changed(drive, tmp_path):
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [drive], dest)
    target = dest / "2019" / "05" / "12" / "sunset.jpg"
    kept = target.stat()            # damage where it lies: same size and time
    target.write_bytes(target.read_bytes()[:-10] + b"\0" * 10)
    os.utime(target, ns=(kept.st_atime_ns, kept.st_mtime_ns))
    (dest / "2019" / "05" / "12" / "beach.jpg").unlink()
    engine = run(tmp_path / "data", [drive], dest, "verify")
    got = rows(tmp_path / "data")
    assert got["sunset.jpg"]["status"] == "error" and "match" in got["sunset.jpg"]["error"]
    assert got["beach.jpg"]["status"] == "error" and "missing" in got["beach.jpg"]["error"]
    assert "2 archived files" in engine.job["message"]["text"]
    # Start copies the two again; the rest is stepped over.
    again = run(tmp_path / "data", [drive], dest)
    assert again.job["stepped_over"] == 5
    assert rows(tmp_path / "data")["sunset.jpg"]["status"] == "verified"
    assert sha(target) == rows(tmp_path / "data")["sunset.jpg"]["dest_hash"]


def test_pause_and_stop_leave_nothing_half_written(tmp_path, monkeypatch):
    src = tmp_path / "src"
    for i in range(12):
        noisy_jpeg(src / f"p{i}.jpg", "2020:01:01 10:00:00", seed=i, size=(1200, 900))
    dest = tmp_path / "Archive"
    # A slow disk, so the run is still going when it is paused and stopped,
    # however fast the machine running this test is.
    real_copy = Importer._copy_and_hash

    def slow_copy(self, source, tmp):
        time.sleep(0.05)
        return real_copy(self, source, tmp)

    monkeypatch.setattr(Importer, "_copy_and_hash", slow_copy)
    engine = Importer(tmp_path / "data")
    engine.pause()                      # before a run: nothing to pause
    engine.start([str(src)], str(dest), ["image"], "copy")
    engine.pause()
    time.sleep(0.3)
    paused_at = engine.progress()["processed"]
    time.sleep(0.3)
    assert engine.progress()["is_paused"]
    assert engine.progress()["processed"] == paused_at
    engine.stop()
    engine.wait(30)
    assert engine.progress()["phase"] == "stopped"
    assert not (dest / importer.PARTIAL_DIR).exists()
    assert all(r["status"] in ("verified", "pending") for r in rows(tmp_path / "data").values())
    done = run(tmp_path / "data", [src], dest)
    assert done.job["phase"] == "done"
    assert sum(r["status"] == "verified" for r in rows(tmp_path / "data").values()) == 12


def test_destination_inside_a_source_is_stepped_around(tmp_path):
    src = tmp_path / "Pictures"
    noisy_jpeg(src / "one.jpg", "2020:01:01 10:00:00", seed=1)
    dest = src / "Archive"
    assert importer.validate([str(src)], str(dest), str(tmp_path / "data")) == []
    assert importer.notices([str(src)], str(dest))
    run(tmp_path / "data", [src], dest)
    run(tmp_path / "data", [src], dest)
    assert len(list(dest.rglob("one*.jpg"))) == 1


def test_choosing_a_folder_inside_an_archive_uses_its_root(tmp_path):
    archive = tmp_path / "Archive"
    (archive / "2019" / "05").mkdir(parents=True)
    (archive / "2020").mkdir()
    assert importer.resolve_destination(str(archive / "2019" / "05"))["destination"] == str(archive)
    assert importer.resolve_destination(str(archive))["corrected"] is False
    plain = tmp_path / "Backups" / "2015"
    plain.mkdir(parents=True)
    assert importer.resolve_destination(str(plain))["corrected"] is False


def said(problems: list[dict]) -> list[str]:
    return [p["text"] for p in problems]


def test_validation_refuses_what_cannot_work(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    data = str(tmp_path / "data")
    assert said(importer.validate([], "", data)) == ["Add at least one source folder.",
                                                     "Choose a destination folder for the archive."]
    assert "same folder" in said(importer.validate([str(src)], str(src), data))[0]
    assert "inside the destination" in said(importer.validate([str(src)], str(tmp_path), data))[0]
    assert "already covered" in said(importer.validate([str(src), str(tmp_path)],
                                                        str(tmp_path / "x"), data))[-1]
    assert "data folder" in said(importer.validate([str(src)], data + "/archive", data))[0]
    assert "can be opened" in said(importer.validate([str(tmp_path / "nope")],
                                                     str(tmp_path / "x"), data))[0]
    # The sentence travels as its key and values, so a Tamil console can
    # translate the words and keep the path.
    problem = importer.validate([str(tmp_path / "nope")], str(tmp_path / "x"), data)[0]
    assert problem["key"] == "Source “{path}” is not a folder that can be opened."
    assert problem["vars"] == {"path": str(tmp_path / "nope")}


def test_a_source_already_in_the_library_is_refused(tmp_path):
    """Archiving the library beside itself would show every photo twice."""
    library = tmp_path / "Pictures"
    (library / "2019").mkdir(parents=True)
    data = str(tmp_path / "data")
    for source in (library, library / "2019"):
        problems = said(importer.validate([str(source)], str(tmp_path / "Archive"), data,
                                          [str(library)]))
        assert problems and "in the library already" in problems[0]
    assert importer.validate([str(tmp_path)], str(tmp_path / "Archive"), data,
                             [str(library)]) == []        # the library inside a source is fine


def test_estimate_counts_what_would_be_taken(drive, tmp_path):
    engine = Importer(tmp_path / "data")
    result = engine.estimate([str(drive)], str(tmp_path / "Archive"), ["image", "video"], "t1")
    assert result["ok"] and result["files"] == 7 and result["by_kind"] == {"image": 7}
    assert result["too_small"] == 2 and result["fits"]
    assert engine.estimate_progress("t1")["finished"]
    only_video = engine.estimate([str(drive)], str(tmp_path / "Archive"), ["video"], "t2")
    assert only_video["files"] == 0 and only_video["left_out"] == 7


# --- over HTTP --------------------------------------------------------------------------

ROUTES = [
    ("get", "/api/archive/status"), ("get", "/api/archive/settings"),
    ("post", "/api/archive/validate"), ("post", "/api/archive/capacity"),
    ("get", "/api/archive/capacity/progress?token=x"), ("post", "/api/archive/capacity/cancel"),
    ("post", "/api/archive/start"), ("post", "/api/archive/stop"), ("post", "/api/archive/pause"),
    ("post", "/api/archive/resume"), ("get", "/api/archive/recent"), ("get", "/api/archive/years"),
    ("get", "/api/archive/manifest.csv"), ("post", "/api/archive/retry-errors"),
    ("post", "/api/archive/reset"), ("post", "/api/archive/adopt"),
    ("get", "/api/archive/takeout-albums"), ("post", "/api/archive/takeout-albums"),
]


@pytest.mark.parametrize("method,url", ROUTES)
def test_only_an_administrator(app, family, method, url):
    r = getattr(app.test_client(), method)(url, json={})
    assert r.status_code == 401
    r = getattr(family, method)(url, json={})
    assert r.status_code == 403


def wait_for(client, seconds: float = 60) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = client.get("/api/archive/status").get_json()
        if not status["is_scanning"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def test_the_console_runs_an_import(app, admin, drive, tmp_path):
    dest = tmp_path / "Archive"
    job = {"source_dirs": [{"path": str(drive)}], "destination_dir": str(dest),
           "media_types": ["image", "video"]}
    v = admin.post("/api/archive/validate", json=job).get_json()
    assert v["ok"] and v["problems"] == []
    strip = admin.get("/api/status/activity").get_json()
    assert [j["id"] for j in strip["jobs"]] == []
    est = admin.post("/api/archive/capacity", json={**job, "progress_token": "abc"}).get_json()
    assert est["ok"] and est["files"] == 7
    r = admin.post("/api/archive/start", json={**job, "mode": "copy"})
    assert r.status_code == 200, r.get_json()
    strip = admin.get("/api/status/activity").get_json()
    if strip["running"]:                      # the run is quick; the strip shows it while it lasts
        job_row = next(j for j in strip["jobs"] if j["id"] == "import")
        assert job_row["page"] == "archive" and job_row["title"] == "Consolidating"
    status = wait_for(admin)
    assert status["phase"] == "done" and status["verified"] == 6 and status["duplicates"] == 1
    assert status["handoff"] == {"destination": str(dest), "verified": 6, "available": True,
                                 "in_library": False}
    assert admin.get("/api/archive/settings").get_json() == {
        "source_dirs": [{"path": str(drive)}], "destination_dir": str(dest),
        "media_types": ["image", "video"], "destination_is_default": False}
    recent = admin.get("/api/archive/recent?status=duplicate").get_json()
    assert [r["filename"] for r in recent] == ["beach copy.jpg"]
    years = {y["year"]: y["count"] for y in admin.get("/api/archive/years").get_json()}
    assert years == {"2008": 1, "2012": 1, "2019": 2, "2023": 1, importer.UNDATED: 1}
    csv = admin.get("/api/archive/manifest.csv")
    assert csv.status_code == 200 and csv.data.count(b"\n") == 8
    # The archive joins the library with one press.
    r = admin.post("/api/archive/adopt", json={})
    assert r.status_code == 200 and r.get_json()["path"] == str(dest)
    assert str(dest) in app.config["LITE"].folders
    assert admin.get("/api/archive/status").get_json()["handoff"]["in_library"]
    # Reset forgets the report, never the files.
    assert admin.post("/api/archive/reset", json={}).status_code == 200
    assert admin.get("/api/archive/status").get_json()["total_scanned"] == 0
    assert (dest / "2019" / "05" / "12" / "beach.jpg").is_file()


def test_start_refuses_a_bad_job_with_its_reasons(app, admin, tmp_path):
    r = admin.post("/api/archive/start", json={"source_dirs": [{"path": str(tmp_path)}],
                                                "destination_dir": str(tmp_path)})
    assert r.status_code == 409
    assert "same folder" in r.get_json()["error"]
    assert "same folder" in r.get_json()["problems"][0]["text"]
    # The library folder itself is refused as a source.
    root = app.config["LITE"].folders[0]
    r = admin.post("/api/archive/start", json={"source_dirs": [{"path": root}],
                                                "destination_dir": str(tmp_path / "Archive")})
    assert r.status_code == 409 and "in the library already" in r.get_json()["error"]
    r = admin.post("/api/archive/start", json={"mode": "sideways"})
    assert r.status_code == 400


def test_takeout_albums_are_made_after_the_import(app, admin, tmp_path):
    export = tmp_path / "Takeout" / "Google Photos"
    for name in ("one", "two", "three"):
        noisy_jpeg(export / "Photos from 2018" / f"{name}.jpg", "2018:04:01 10:00:00",
                   seed=len(name))
    album = export / "Pongal 2018"
    (album / "metadata.json").parent.mkdir(parents=True)
    (album / "metadata.json").write_text(json.dumps({"title": "Pongal 2018"}))
    for name in ("one", "two"):
        (album / f"{name}.jpg").write_bytes((export / "Photos from 2018" / f"{name}.jpg").read_bytes())
    (export / "Photos from 2018" / "metadata.json").write_text(json.dumps({"title": "Photos from 2018"}))
    found = takeout.albums_in([str(tmp_path / "Takeout")])
    assert [(a["title"], len(a["files"])) for a in found] == [("Pongal 2018", 2)]

    dest = tmp_path / "Archive"
    job = {"source_dirs": [{"path": str(tmp_path / "Takeout")}], "destination_dir": str(dest),
           "mode": "copy"}
    assert admin.post("/api/archive/start", json=job).status_code == 200
    wait_for(admin)
    assert admin.get("/api/archive/takeout-albums").get_json() == {
        "albums": [{"title": "Pongal 2018", "files": 2}]}
    # Before the archive is indexed, nothing can be matched yet.
    assert admin.post("/api/archive/adopt", json={}).status_code == 200
    app.config["SCANNER"].folders = list(app.config["LITE"].folders)
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))
    made = admin.post("/api/archive/takeout-albums", json={}).get_json()
    assert made["unmatched"] == 0
    assert [(a["title"], a["added"]) for a in made["albums"]] == [("Pongal 2018", 2)]
    albums = admin.get("/api/albums").get_json()["albums"]
    assert [(a["name"], a["n"]) for a in albums] == [("Pongal 2018", 2)]
    # Again adds nothing twice.
    assert admin.post("/api/archive/takeout-albums", json={}).get_json()["albums"][0]["added"] == 0


def test_console_settings_survive_a_restart(tmp_path):
    from ninaivu_lite.config import Config
    cfg = Config(data_dir=str(tmp_path / "data"))
    cfg.import_sources, cfg.import_destination, cfg.import_kinds = ["/a"], "/b", ["video"]
    cfg.save()
    again = Config.load(tmp_path / "data")
    assert (again.import_sources, again.import_destination, again.import_kinds) == (
        ["/a"], "/b", ["video"])


def test_first_day_offers_the_import(app, admin, library):
    root, _data = library
    data = admin.get("/api/admin/first-day").get_json()
    assert data["import"] == {"sources": [], "running": False,
                              "destination": os.path.join(str(root), "Ninaivu Archive")}
    cfg = app.config["LITE"]
    cfg.import_sources, cfg.import_destination = ["/old/drive"], "/elsewhere"
    data = admin.get("/api/admin/first-day").get_json()
    assert data["import"]["sources"] == ["/old/drive"]
    assert data["import"]["destination"] == "/elsewhere"


def test_the_destination_is_never_empty(app, admin, library, tmp_path):
    root, _ = library
    saved = admin.get("/api/archive/settings").get_json()
    assert saved["destination_dir"] == os.path.join(str(root), "Ninaivu Archive")
    assert saved["destination_is_default"] is True
    # A folder the administrator chose is kept instead.
    cfg = app.config["LITE"]
    cfg.import_destination = str(tmp_path / "Elsewhere")
    saved = admin.get("/api/archive/settings").get_json()
    assert saved["destination_dir"] == str(tmp_path / "Elsewhere")
    assert saved["destination_is_default"] is False
    # Without a library yet, under Pictures (or home), never nothing.
    assert importer.default_destination("").endswith("Ninaivu Archive")
