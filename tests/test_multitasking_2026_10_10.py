"""Multitasking review of 2026-10-10 (A130-A136): the server keeps threads
free while tiles are made for phones, a library folder on a sleeping NAS
does not hold up page loads, an import walks only the folder it filled,
thumbnails are written down in batches and as they are ready, the Control
Panel tells a busy server from a stopped one, and an export says how far
its counting has got."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ninaivu_lite import api_gallery, common, control, db, drives, media, parallel
from ninaivu_lite import scanner as scanner_module
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg

ROOT = Path(__file__).resolve().parents[1]


def _library(tmp_path, count: int, folder: str = "Photos") -> tuple[Scanner, object, Path]:
    root = tmp_path / folder
    for i in range(count):
        make_jpeg(root / f"{i % 3}" / f"p{i:03}.jpg", f"2020:01:{1 + i % 28:02d} 10:00:00",
                  size=(64, 48))
    data = tmp_path / "data"
    return Scanner(data, [str(root)]), db.connect(data), root


# --- A130: tiles made for phones leave the server threads to spare -------------------------


def test_a130_the_server_always_has_threads_to_spare():
    assert parallel.SERVER_THREADS >= 16
    assert 2 <= parallel.REQUEST_WORK <= 6
    busy = parallel.REQUEST_WORK + parallel.REQUEST_WAITING
    assert parallel.SERVER_THREADS - busy >= parallel.SERVER_SPARE
    assert api_gallery.TILES.width == parallel.REQUEST_WORK


def test_a130_the_web_server_gets_the_threads(monkeypatch):
    import waitress

    from ninaivu_lite import __main__ as entry
    asked = {}
    monkeypatch.setattr(waitress, "serve", lambda app, **kw: asked.update(kw))
    entry.serve(object(), "127.0.0.1", 8080)
    assert asked["threads"] == parallel.SERVER_THREADS


def test_a130_slots_limit_the_work_and_the_waiting():
    slots = parallel.Slots(1, 1, wait_for=0.3)
    inside, release = threading.Event(), threading.Event()

    def hold():
        with slots.slot():
            inside.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    inside.wait(5)
    assert slots.in_use == 1
    waited: list = []

    def wait_for_one():
        try:
            with slots.slot():
                waited.append("made")
        except parallel.Busy:
            waited.append("busy")

    waiter = threading.Thread(target=wait_for_one)
    waiter.start()
    time.sleep(0.05)
    began = time.perf_counter()
    with pytest.raises(parallel.Busy):          # the waiting room is full: told at once
        with slots.slot():
            pass
    assert time.perf_counter() - began < 0.1
    waiter.join()
    assert waited == ["busy"]                   # waited its while, then told to ask again
    release.set()
    holder.join()
    with slots.slot():                          # free again
        assert slots.in_use == 1
    assert slots.in_use == 0


def test_a130_the_same_thumbnail_asked_for_twice_at_once_is_made_once(tmp_path, monkeypatch):
    s, c, _root = _library(tmp_path, 1)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    (asset_id,) = [r[0] for r in c.execute("SELECT id FROM assets")]
    made: list[int] = []
    real = media.make_thumbnails

    def slow(*args, **kwargs):
        made.append(1)
        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(media, "make_thumbnails", slow)
    slots = parallel.Slots(4, 4)
    answers: list[bool] = []

    def ask():
        conn = db.connect(tmp_path / "data")
        answers.append(s.thumbnail_now(conn, asset_id, "s", slots=slots))

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert answers == [True, True, True] and len(made) == 1


def test_a130_a_video_on_demand_shares_the_cores_with_the_other_tiles(tmp_path, monkeypatch):
    s, c, _root = _library(tmp_path, 1)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    monkeypatch.setattr(parallel, "cores", lambda: 8)
    asked: list[int] = []
    monkeypatch.setattr(media, "make_thumbnails",
                        lambda *a, threads=0, **k: (asked.append(threads), (True, "#000000"))[1])
    slots = parallel.Slots(4, 4)
    with slots.slot():                          # another tile being made right now
        s.thumbnail_now(c, 1, "s", slots=slots)
    assert asked == [4]


def test_a130_a_tile_past_the_waiting_room_is_told_to_ask_again(app, admin, monkeypatch):
    item = ids(app)["beach.jpg"]
    db.connect(app.config["LITE"].data_dir).execute(
        "UPDATE assets SET thumb = 0 WHERE id = ?", (item,)).connection.commit()
    full = parallel.Slots(1, 0)
    monkeypatch.setattr(api_gallery, "TILES", full)
    with full.slot():
        r = admin.get(f"/api/thumb/{item}?s=256")
    assert r.status_code == 503 and r.headers["Retry-After"] == str(api_gallery.TILE_RETRY)
    assert "no-store" in r.headers["Cache-Control"]
    r = admin.get(f"/api/thumb/{item}?s=256")   # asked again: made
    assert r.status_code == 200 and r.mimetype == "image/webp"


def test_a130_the_grid_asks_once_more_before_giving_a_tile_up():
    grid = (ROOT / "ninaivu_lite" / "static" / "js" / "grid.js").read_text(encoding="utf-8")
    assert "img.dataset.retried !== src" in grid and "delete img.dataset.retried" in grid


def test_a130_viewing_copies_take_turns(app, guest, monkeypatch):
    item = ids(app)["beach.jpg"]
    db.connect(app.config["LITE"].data_dir).execute(
        "UPDATE assets SET visibility = 0 WHERE id = ?", (item,)).connection.commit()
    used: list[int] = []
    real = media.viewing_copy

    def copy(*args, **kwargs):
        used.append(api_gallery.VIEWS.in_use)
        return real(*args, **kwargs)

    monkeypatch.setattr(media, "viewing_copy", copy)
    assert guest.get(f"/api/file/{item}").status_code == 200
    assert used == [1]


# --- A131: a library folder that is slow to answer does not hold the gallery up ---------------


def test_a131_a_sleeping_library_folder_does_not_hold_up_the_status(app, admin, tmp_path,
                                                                    monkeypatch):
    asleep = str(tmp_path / "NAS")
    real = os.path.isdir

    def slow(path):
        if str(path).endswith("NAS"):
            time.sleep(1.5)
            return False
        return real(path)

    monkeypatch.setattr(common.os.path, "isdir", slow)
    app.config["LITE"].folders.append(asleep)
    began = time.perf_counter()
    first = admin.get("/api/status?stats=0").json
    assert time.perf_counter() - began < 1.2
    assert first["libraries_away"] == 0          # not known yet: taken as there
    time.sleep(1.6)
    second = admin.get("/api/status?stats=0").json
    assert second["libraries_away"] == 1 and second["libraries_away_paths"] == [asleep]
    began = time.perf_counter()
    assert admin.get("/api/status?stats=0").json["libraries_away"] == 1
    assert time.perf_counter() - began < 0.5     # kept for a while, not asked again


def test_a131_an_answer_is_kept_for_a_few_seconds(tmp_path, monkeypatch):
    folder = tmp_path / "Library"
    folder.mkdir()
    asked: list[str] = []
    real = os.path.isdir
    monkeypatch.setattr(common.os.path, "isdir", lambda p: (asked.append(p), real(p))[1])
    assert common.library_exists(str(folder)) and common.library_exists(str(folder))
    assert len(asked) == 1
    monkeypatch.setattr(common, "REACH_KEEP", 0.0)
    folder.rmdir()
    assert common.library_exists(str(folder)) is False


# --- A132: an import walks the folder it filled, not the whole library -------------------------


def test_a132_rescans_asked_for_folders_and_everything(tmp_path):
    s = Scanner(tmp_path / "data", [])
    s.rescan("/a")
    s.rescan("/b")
    assert s._asked() == ["/a", "/b"]
    assert s._asked() is None                   # nothing asked: everything
    s.rescan("/a")
    s.rescan()
    assert s._asked() is None                   # the Rescan button wins


def test_a132_a_walk_of_one_folder_leaves_the_rest_alone(tmp_path):
    s, c, root = _library(tmp_path, 9)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    walked: list = []
    real = s._files

    def files(root_, failed=None, under=None):
        walked.append(under)
        return real(root_, failed, under)

    s._files = files
    (root / "0" / "p000.jpg").unlink()           # gone from the folder asked about
    (root / "1" / "p001.jpg").unlink()           # and from one not asked about
    make_jpeg(root / "0" / "new.jpg", "2021:01:01 10:00:00", size=(64, 48))
    s.scan_once(c, [str(root / "0")])
    assert walked == [["0"]]
    rows = {r["name"]: r["missing"] for r in c.execute("SELECT name, missing FROM assets")}
    assert rows["new.jpg"] == 0 and rows["p000.jpg"] == 1
    assert rows["p001.jpg"] == 0                 # left for the next full look
    s.scan_once(c)
    assert c.execute("SELECT missing FROM assets WHERE name = 'p001.jpg'").fetchone()[0] == 1


def test_a132_a_folder_outside_the_library_walks_nothing(tmp_path):
    s, c, _root = _library(tmp_path, 3)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    s._walk_folder = lambda *a, **k: pytest.fail("walked")
    s.scan_once(c, [str(tmp_path / "Elsewhere")])


def test_a132_a_folder_holding_the_library_walks_all_of_it(tmp_path):
    s, c, root = _library(tmp_path, 3)
    s._make_thumbnails = lambda conn: None
    walked: list = []
    real = s._walk_folder
    s._walk_folder = lambda conn, fid, path, under=None: (walked.append(under),
                                                         real(conn, fid, path, under))
    s.scan_once(c, [str(tmp_path)])
    assert walked == [None]


def test_a132_the_folder_is_spelled_as_it_is_on_disk(tmp_path):
    (tmp_path / "Archive" / "2024").mkdir(parents=True)
    (tmp_path / ".hidden").mkdir()
    assert scanner_module.spelled(str(tmp_path), "Archive/2024") == "Archive/2024"
    assert scanner_module.spelled(str(tmp_path), "archive/2024") == "Archive/2024"
    assert scanner_module.spelled(str(tmp_path), "Archive/2025") is None
    assert scanner_module.spelled(str(tmp_path), ".hidden") is None
    assert scanner_module.spelled(str(tmp_path), "") == ""
    assert scanner_module.inside(str(tmp_path / "Archive" / "2024"), str(tmp_path)) \
        == "Archive/2024"
    assert scanner_module.inside(str(tmp_path), str(tmp_path / "Archive")) == ""
    assert scanner_module.inside(str(tmp_path / "Other"), str(tmp_path / "Archive")) is None


def test_a132_an_import_asks_for_its_own_folder(app, admin, library, tmp_path, monkeypatch):
    root, _data = library
    asked: list = []
    monkeypatch.setattr(app.config["SCANNER"], "rescan", lambda within=None: asked.append(within))
    src = tmp_path / "OldDrive"
    make_jpeg(src / "a.jpg", "2020:02:02 10:00:00")
    job = {"source_dirs": [{"path": str(src)}], "destination_dir": str(root / "Archive"),
           "media_types": ["image"], "mode": "copy"}
    assert admin.post("/api/archive/start", json=job).status_code == 200
    for _ in range(200):
        if not app.config["IMPORTER"].running:
            break
        time.sleep(0.05)
    assert asked and set(asked) == {str(root / "Archive")}


def test_a132_the_scanner_loop_walks_only_what_was_asked(tmp_path, monkeypatch):
    s, _c, root = _library(tmp_path, 3)
    looks: list = []
    done = threading.Event()

    def once(conn, within=None):
        looks.append(within)
        if len(looks) == 2:
            done.set()

    s.scan_once = once
    s.start()
    for _ in range(100):
        if looks:
            break
        time.sleep(0.02)
    s.rescan(str(root / "0"))
    done.wait(5)
    s.stop()
    assert looks[:2] == [None, [str(root / "0")]]


# --- A133: thumbnails written down in batches ----------------------------------------------------


def test_a133_thumbnails_are_written_down_in_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c, _root = _library(tmp_path, 120)
    batches: list[int] = []
    real = Scanner._record_many
    monkeypatch.setattr(Scanner, "_record_many",
                        staticmethod(lambda conn, made: (batches.append(len(made)),
                                                         real(conn, made))))
    s.scan_once(c)
    assert sum(batches) == 240                   # small and large, every one
    assert len(batches) <= 8 and max(batches) == scanner_module.RECORD_EVERY_ROWS
    assert c.execute("SELECT COUNT(*) FROM assets WHERE thumb = 1 AND large = 1").fetchone()[0] \
        == 120


def test_a133_a_batch_is_written_down_within_a_second(tmp_path):
    written: list[list] = []
    with scanner_module.Batch(None, lambda conn, made: written.append(list(made))) as batch:
        assert batch.add(("a",)) is False
        batch.since -= scanner_module.RECORD_EVERY_SECONDS
        assert batch.add(("b",)) is True
        assert batch.add(("c",)) is False
    assert written == [[("a",), ("b",)], [("c",)]]


def test_a133_a_rescan_mid_pass_keeps_what_was_made(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c, _root = _library(tmp_path, 40)
    real = s._render
    count = [0]

    def render(row, sizes, threads=0):
        count[0] += 1
        if count[0] == 10:
            s.rescan()
        return real(row, sizes, threads)

    s._render = render
    s.scan_once(c)
    made = c.execute("SELECT COUNT(*) FROM assets WHERE thumb = 1").fetchone()[0]
    assert 0 < made < 40                         # stopped early; what was handed over is kept
    for row in c.execute("SELECT id FROM assets WHERE thumb = 1"):
        assert media.thumb_path(s.thumbs_dir, row[0], "s").is_file()


# --- A134: one slow file does not keep the others waiting --------------------------------------


def test_a134_as_done_hands_each_answer_over_when_it_is_ready():
    def work(n):
        time.sleep(0.5 if n == 0 else 0.01)
        return n * 10

    with ThreadPoolExecutor(3) as pool:
        got = list(parallel.as_done(range(12), work, pool, 3))
    assert sorted(got) == [(n, n * 10) for n in range(12)]
    assert got[-1] == (0, 0)                     # the slow first one came last
    assert list(parallel.as_done(range(3), work, None, 3)) == [(0, 0), (1, 10), (2, 20)]


def test_a134_as_done_stops_when_closed():
    started: list[int] = []

    def work(n):
        started.append(n)
        time.sleep(0.01)
        return n

    with ThreadPoolExecutor(2) as pool:
        made = parallel.as_done(range(1000), work, pool, 2)
        for _ in range(3):
            next(made)
        made.close()
    assert len(started) < 10


def test_a134_a_slow_thumbnail_does_not_hold_back_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    monkeypatch.setattr(scanner_module, "RECORD_EVERY_ROWS", 1)
    s, c, _root = _library(tmp_path, 30)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)                               # walked; no thumbnails yet
    del s._make_thumbnails
    newest = c.execute("SELECT id FROM assets ORDER BY captured_at DESC, id DESC").fetchone()
    real = s._render
    order: list[int] = []

    def render(row, sizes, threads=0):
        if row["id"] == newest[0] and sizes == ("s",):
            time.sleep(0.6)
        return real(row, sizes, threads)

    s._render = render
    real_many = Scanner._record_many
    monkeypatch.setattr(Scanner, "_record_many", staticmethod(
        lambda conn, made: (order.extend(r[0]["id"] for r in made), real_many(conn, made))))
    s.scan_once(c)
    first_pass = order[:30]
    assert first_pass.index(newest[0]) > 10      # the others were written down meanwhile
    assert sorted(first_pass) == sorted(set(first_pass))


def test_a134_each_photo_is_looked_at_for_faces_once(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", True)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c, _root = _library(tmp_path, 130)
    s._make_thumbnails = lambda conn: None
    looked: list[str] = []
    lock = threading.Lock()

    def detect(path):
        with lock:
            looked.append(path)
        time.sleep(0.001)
        return 0

    monkeypatch.setattr(media, "detect_rotation", detect)
    s.scan_once(c)
    assert len(looked) == 130 == len(set(looked))
    assert c.execute("SELECT COUNT(*) FROM assets WHERE upright = 0").fetchone()[0] == 0


# --- A135: the Control Panel tells a busy server from a stopped one ----------------------------


class _Slow(BaseHTTPRequestHandler):
    delay = 1.0

    def do_GET(self):  # noqa: N802
        time.sleep(self.delay)
        body = json.dumps({"ok": True, "app": "Ninaivu Lite", "version": "x"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def slow_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _hold_lock(data: Path):
    """Another process holding the data folder's lock, as a running server does."""
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from ninaivu_lite import lock
        assert lock.acquire({str(data)!r})
        print("held", flush=True)
        time.sleep(30)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_a135_a_busy_server_is_running_not_stopped(tmp_path, slow_server, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    c = control.Controller(str(data))
    c.state = lambda: {"port": slow_server.server_address[1]}
    assert c.health(timeout=0.2) is None        # nobody holds the lock: not ours
    holder = _hold_lock(data)
    try:
        _Slow.delay = 1.0
        monkeypatch.setattr(control, "BUSY_WAIT", 3.0)
        answer = c.health(timeout=0.2)           # late the first time, then answered
        assert answer and answer["version"] == "x"
        _Slow.delay = 2.0
        monkeypatch.setattr(control, "BUSY_WAIT", 0.3)
        answer = c.health(timeout=0.2)           # still busy: running all the same
        assert answer == {"ok": True, "app": "Ninaivu Lite", "slow": True}
        assert c.running()
    finally:
        holder.kill()
        holder.wait()
        _Slow.delay = 1.0


def test_a135_a_refused_connection_is_not_waited_for(tmp_path):
    c = control.Controller(str(tmp_path))
    c.state = lambda: {"port": 1}
    began = time.perf_counter()
    assert c.health(timeout=0.3) is None
    assert time.perf_counter() - began < 1


# --- A136: an export says how far its counting has got; drive folders listed once --------------


def test_a136_an_export_counts_out_loud(tmp_path):
    _s, _c, root = _library(tmp_path, 7)
    drive = drives.Drive("d1", str(tmp_path / "Stick"), "Stick", 0, 0)
    Path(drive.path).mkdir()
    job = drives.Exporter()
    job.start(drive, [str(root)], str(tmp_path / "data"))
    job.wait(30)
    state = job.progress()
    assert state["phase"] == "done" and state["found"] == 7 and state["copied"] == 7
    js = (ROOT / "ninaivu_lite" / "static" / "js" / "drives.js").read_text(encoding="utf-8")
    assert "Counting the photos and videos… {found} so far" in js
    for lang in ("en", "ta"):
        strings = json.loads((ROOT / "ninaivu_lite" / "static" / "i18n" / f"{lang}.json")
                             .read_text(encoding="utf-8"))
        assert "{found}" in strings["Counting the photos and videos… {found} so far"]


def test_a136_a_folders_listing_answers_as_the_drive_would(tmp_path):
    folder = tmp_path / "Ninaivu Lite" / "Photos"
    folder.mkdir(parents=True)
    (folder / "IMG_1.JPG").write_bytes(b"12345")
    there = drives.Listing()
    for listing in (None, there):
        claimed: set[str] = set()
        assert drives._place(str(folder / "IMG_1.JPG"), 5, claimed, listing) is None
        assert drives._place(str(folder / "IMG_1.JPG"), 9, claimed, listing) \
            == str(folder / "IMG_1 (2).JPG")
        assert drives._place(str(tmp_path / "New" / "a.jpg"), 1, claimed, listing) \
            == str(tmp_path / "New" / "a.jpg")
    # Another spelling of the name: the drive itself decides, as before.
    other = drives._place(str(folder / "img_1.jpg"), 5, set(), there)
    assert other == drives._place(str(folder / "img_1.jpg"), 5, set(), None)
    assert list(there.folders) == [str(folder), str(tmp_path / "New")]
