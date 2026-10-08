"""Parallel work review of 2026-10-08 (A123-A127): scan work scales with the
cores and the memory, slow disks' headers are read several at a time, faces
are looked for several at a time, copies read ahead, and a video's frame is
shrunk by ffmpeg with its share of the cores."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ninaivu_lite import db, drives, importer, media, parallel
from ninaivu_lite import scanner as scanner_module
from ninaivu_lite.scanner import Scanner

from conftest import make_jpeg

GB = 1024 ** 3


# --- A123: as many workers as the computer has room for ----------------------------------


@pytest.mark.parametrize(("cores", "memory", "expected"), [
    (1, 8 * GB, 1),          # one core: one at a time, as always
    (2, 8 * GB, 1),          # the other core is the gallery's
    (4, 1 * GB, 3),          # a Raspberry Pi 4 with 1 GB: as in 1.8.0
    (4, int(0.5 * GB), 2),   # 512 MB: what half of it holds
    (8, 16 * GB, 7),
    (12, 16 * GB, 11),
    (64, 256 * GB, parallel.MAX_WORKERS),
    (8, None, 7),            # memory unknown: the cores decide
])
def test_a123_workers_follow_the_cores_and_the_memory(cores, memory, expected):
    assert parallel.workers(cores, memory) == expected


def test_a123_the_computer_is_measured():
    assert parallel.cores() >= 1
    memory = parallel.memory()
    assert memory is None or memory > 64 * 1024 ** 2
    assert 1 <= parallel.WORKERS <= max(1, parallel.cores() - 1)
    assert scanner_module.THUMB_WORKERS == parallel.WORKERS


def test_a123_in_order_keeps_the_order_and_stops_when_closed():
    from concurrent.futures import ThreadPoolExecutor

    def work(n):
        time.sleep(0.002 * (n % 3))
        return n * n

    with ThreadPoolExecutor(3) as pool:
        assert list(parallel.in_order(range(30), work, pool, 3)) == [(n, n * n) for n in range(30)]
        started: list[int] = []

        def noted(n):
            started.append(n)
            return n

        made = parallel.in_order(range(100), noted, pool, 3)
        for item, _ in made:
            if item == 5:
                break
        made.close()
        time.sleep(0.05)
        assert len(started) <= 10            # at most a few ahead, never the rest
    assert list(parallel.in_order(range(4), work, None, 1)) == [(n, n * n) for n in range(4)]


# --- A124: headers of a slow disk read several at a time --------------------------------------


def _photos(root: Path, count: int) -> None:
    for i in range(count):
        make_jpeg(root / f"d{i % 4}" / f"p{i:03}.jpg", f"2021:02:{1 + i % 28:02d} 09:{i % 60:02d}:00",
                  size=(64, 48))


def _indexed(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT dir, name, captured_at, date_key, width, height, camera, visibility "
        "FROM assets ORDER BY id")]


def test_a124_a_slow_disk_is_read_several_at_a_time_with_the_same_index(tmp_path, monkeypatch):
    _photos(tmp_path / "Photos", 90)
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "BATCH", 20)
    real = media.describe
    threads: set[str] = set()

    def slow(*args):
        threads.add(threading.current_thread().name)
        time.sleep(0.004)                     # a network drive
        return real(*args)

    monkeypatch.setattr(media, "describe", slow)
    results = {}
    for workers in (1, 3):
        monkeypatch.setattr(scanner_module, "THUMB_WORKERS", workers)
        threads.clear()
        data = tmp_path / f"data{workers}"
        s, c = Scanner(data, [str(tmp_path / "Photos")]), db.connect(data)
        s._make_thumbnails = lambda conn: None
        s.scan_once(c)
        results[workers] = _indexed(c)
        if workers == 1:
            assert threads == {threading.current_thread().name}
        else:
            # The first batch tells the disk is slow; the rest go to the workers.
            assert len([t for t in threads if t.startswith("headers")]) >= 2
        assert s.snapshot()["new"] == 90
    assert results[1] == results[3] and len(results[1]) == 90


def test_a124_a_fast_disk_is_read_on_the_scanner_thread(tmp_path, monkeypatch):
    _photos(tmp_path / "Photos", 60)
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "BATCH", 20)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    real = media.describe
    threads: set[str] = set()

    def fast(*args):
        threads.add(threading.current_thread().name)
        return real(*args)

    monkeypatch.setattr(media, "describe", fast)
    monkeypatch.setattr(scanner_module, "SLOW_HEADER", 10.0)
    s, c = Scanner(tmp_path / "data", [str(tmp_path / "Photos")]), db.connect(tmp_path / "data")
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    assert threads == {threading.current_thread().name}
    assert len(_indexed(c)) == 60


def test_a124_stop_during_a_slow_walk_writes_nothing_more(tmp_path, monkeypatch):
    _photos(tmp_path / "Photos", 80)
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(scanner_module, "BATCH", 10)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c = Scanner(tmp_path / "data", [str(tmp_path / "Photos")]), db.connect(tmp_path / "data")
    real = media.describe
    seen: list[int] = []

    def slow(*args):
        seen.append(1)
        if len(seen) == 25:
            s._stop.set()
        time.sleep(0.003)
        return real(*args)

    monkeypatch.setattr(media, "describe", slow)
    s._make_thumbnails = lambda conn: None
    s.scan_once(c)
    assert len(seen) < 45
    assert c.execute("SELECT COUNT(*) FROM assets WHERE missing = 1").fetchone()[0] == 0


def test_a124_a_folders_time_zones_are_read_once_at_a_time(tmp_path, monkeypatch):
    """Several videos of one folder at once must not each read its photos."""
    calls: list[str] = []
    real = media._photo_moment

    def counted(path):
        calls.append(path)
        time.sleep(0.01)
        return real(path)

    monkeypatch.setattr(media, "_photo_moment", counted)
    media._folder_zones.cache_clear()
    folder = tmp_path / "trip"
    for i in range(3):
        make_jpeg(folder / f"p{i}.jpg", "2021:02:01 09:00:00", size=(32, 24))
    from datetime import datetime, timezone
    zone = media.zone_near(str(folder / "v.mp4"))
    threads = [threading.Thread(target=zone, args=(datetime(2021, 2, 1, tzinfo=timezone.utc),))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 3
    media._folder_zones.cache_clear()


# --- A125: faces looked for several at a time ---------------------------------------------------


def _upright_needed(tmp_path: Path, count: int) -> tuple[Scanner, object]:
    root = tmp_path / "Photos"
    for i in range(count):
        make_jpeg(root / f"s{i:03}.jpg", f"2019:05:{1 + i % 28:02d} 12:00:00", size=(80, 60))
    s, c = Scanner(tmp_path / "data", [str(root)]), db.connect(tmp_path / "data")
    s._make_thumbnails = lambda conn: None
    return s, c


def test_a125_faces_are_looked_for_by_the_workers_and_written_by_the_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", True)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c = _upright_needed(tmp_path, 70)
    looked: set[str] = set()

    def detect(path):
        looked.add(threading.current_thread().name)
        time.sleep(0.003)
        return 90 if path.endswith(("0.jpg", "5.jpg")) else 0

    monkeypatch.setattr(media, "detect_rotation", detect)
    writers: set[str] = set()
    real = s.set_rotation

    def written(*args, **kwargs):
        writers.add(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(s, "set_rotation", written)
    s.scan_once(c)
    assert len([t for t in looked if t.startswith("faces")]) >= 2
    assert writers == {threading.current_thread().name}
    rows = {r["name"]: (r["rotation"], r["rot_source"], r["upright"]) for r in c.execute(
        "SELECT name, rotation, rot_source, upright FROM assets")}
    assert len(rows) == 70
    for name, (rotation, source, upright) in rows.items():
        turned = name.endswith(("0.jpg", "5.jpg"))
        assert (rotation, source, upright) == ((90, "faces", 1) if turned else (0, "none", 1))


def test_a125_a_rescan_stops_the_face_check_early(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FACES", True)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 3)
    s, c = _upright_needed(tmp_path, 60)
    looked: list[str] = []

    def detect(path):
        looked.append(path)
        if len(looked) == 5:
            s.rescan()
        time.sleep(0.003)
        return 0

    monkeypatch.setattr(media, "detect_rotation", detect)
    s.scan_once(c)
    assert len(looked) < 20
    assert c.execute("SELECT COUNT(*) FROM assets WHERE upright = 0").fetchone()[0] > 30


@pytest.mark.skipif(not media.FACES, reason="OpenCV is not installed")
def test_a125_each_thread_has_its_own_face_classifiers():
    found: dict[str, list] = {}

    def take():
        found[threading.current_thread().name] = media._face_cascades()

    threads = [threading.Thread(target=take, name=f"t{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    one, two = found["t0"], found["t1"]
    assert one and two and all(a is not b for a, b in zip(one, two, strict=True))
    assert media._face_cascades() is media._face_cascades()


# --- A126: copies and checks read ahead -------------------------------------------------------


def _blob(path: Path, size: int) -> bytes:
    data = os.urandom(size)
    path.write_bytes(data)
    return data


@pytest.mark.parametrize("size", [0, 10, importer.CHUNK, 3 * importer.CHUNK + 17])
def test_a126_reading_ahead_gives_the_same_bytes(tmp_path, size, monkeypatch):
    data = _blob(tmp_path / "f.bin", size)
    for cores in (1, 4):
        monkeypatch.setattr(parallel, "cores", lambda cores=cores: cores)
        with open(tmp_path / "f.bin", "rb") as f:
            assert b"".join(parallel.chunks(f, importer.CHUNK)) == data
        assert importer.hash_file(str(tmp_path / "f.bin")) == hashlib.sha256(data).hexdigest()


def test_a126_the_import_copy_matches_and_stops_when_asked(tmp_path):
    data = _blob(tmp_path / "video.mp4", 5 * importer.CHUNK + 3)
    imp = importer.Importer(tmp_path / "data")
    digest, count = imp._copy_and_hash(str(tmp_path / "video.mp4"), str(tmp_path / "out.tmp"))
    assert (digest, count) == (hashlib.sha256(data).hexdigest(), len(data))
    assert (tmp_path / "out.tmp").read_bytes() == data
    checks = 0
    real = imp.gate.check

    def stop_after_two():
        nonlocal checks
        checks += 1
        if checks == 3:
            imp.gate.cancel()
        real()

    imp.gate.check = stop_after_two
    with pytest.raises(importer.Cancelled):
        imp._copy_and_hash(str(tmp_path / "video.mp4"), str(tmp_path / "out2.tmp"))
    assert (tmp_path / "video.mp4").read_bytes() == data         # the source is only read


def test_a126_the_drive_copy_matches_and_leaves_nothing_when_stopped(tmp_path):
    data = _blob(tmp_path / "src.mov", 4 * drives.CHUNK + 5)
    drives._copy(str(tmp_path / "src.mov"), str(tmp_path / "out" / "a.mov"), threading.Event())
    assert (tmp_path / "out" / "a.mov").read_bytes() == data
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        drives._copy(str(tmp_path / "src.mov"), str(tmp_path / "out" / "b.mov"), cancel)
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.mov"]


def test_a126_a_failed_write_waits_for_the_read_in_flight(tmp_path, monkeypatch):
    """A full disk mid-copy: the error comes out, and nothing is left reading
    a file that is being closed."""
    _blob(tmp_path / "src.bin", 6 * importer.CHUNK)
    imp = importer.Importer(tmp_path / "data")
    real_open = open
    writes = 0

    class Full:
        def __init__(self, f):
            self.f = f

        def write(self, chunk):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError(28, "No space left on device")
            return self.f.write(chunk)

        def __getattr__(self, name):
            return getattr(self.f, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    def opening(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        return Full(f) if "w" in mode else f

    monkeypatch.setattr(importer, "open", opening, raising=False)
    before = threading.active_count()
    with pytest.raises(OSError):
        imp._copy_and_hash(str(tmp_path / "src.bin"), str(tmp_path / "out.tmp"))
    time.sleep(0.05)
    assert threading.active_count() <= before


# --- A127: a video's frame, shrunk by ffmpeg, with a share of the cores ---------------------------


@pytest.mark.skipif(not media.FFMPEG, reason="ffmpeg is not installed")
@pytest.mark.parametrize(("size", "expected"), [((2560, 1440), (1280, 720)), ((320, 240), (320, 240))])
def test_a127_a_video_frame_is_shrunk_but_never_enlarged(tmp_path, size, expected):
    out = tmp_path / "v.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={size[0]}x{size[1]}:rate=10", "-t", "2", "-pix_fmt", "yuv420p",
                    str(out)], check=True)
    for threads in (0, 1):
        frame = media.video_frame(str(out), threads)
        assert frame is not None and frame.size == expected and frame.mode == "RGB"
    ok, colour = media.make_thumbnails(str(out), "video", tmp_path / "t", 7, ("s", "l"), threads=1)
    assert ok and colour


def test_a127_each_video_gets_its_share_of_the_cores(tmp_path, monkeypatch):
    root = tmp_path / "Videos"
    root.mkdir()
    for i in range(6):
        (root / f"v{i}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
    monkeypatch.setattr(media, "FACES", False)
    monkeypatch.setattr(parallel, "cores", lambda: 8)
    asked: list[int] = []

    def made(path, kind, thumbs_dir, asset_id, sizes=("s", "l"), rotation=0, threads=0):
        asked.append(threads)
        return True, "#000000"

    monkeypatch.setattr(media, "make_thumbnails", made)
    for workers, share in ((3, 2), (7, 1), (1, 0)):
        monkeypatch.setattr(scanner_module, "THUMB_WORKERS", workers)
        asked.clear()
        data = tmp_path / f"data{workers}"
        s, c = Scanner(data, [str(root)]), db.connect(data)
        s.scan_once(c)
        assert asked and set(asked) == {share}
    asked.clear()
    c.execute("UPDATE assets SET thumb = 0")
    c.commit()
    s.thumbnail_now(c, 1)                     # one on screen: ffmpeg may use them all
    assert asked == [0]


# --- A128: background work gives way to the gallery ----------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="per-thread niceness is Linux's")
def test_a128_a_background_thread_runs_lower_and_only_it():
    base = os.getpriority(os.PRIO_PROCESS, os.getpid())
    seen: dict[str, int] = {}

    def lowered(name):
        parallel.background()
        parallel.background()                         # twice is still one step down
        seen[name] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
        with parallel.pool(1, "inner") as inner:      # started from it: not lower again
            seen["inner"] = inner.submit(
                lambda: os.getpriority(os.PRIO_PROCESS, threading.get_native_id())).result()

    t = threading.Thread(target=lowered, args=("job",))
    t.start()
    t.join()
    expected = min(19, base + parallel.BACKGROUND_NICE)
    assert seen == {"job": expected, "inner": expected}
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) == base   # the gallery's


def test_a128_every_background_job_lowers_itself(tmp_path, monkeypatch):
    lowered: set[str] = set()
    real = parallel.background

    def noted():
        lowered.add(threading.current_thread().name.split("_")[0])
        real()

    monkeypatch.setattr(parallel, "background", noted)
    monkeypatch.setattr(scanner_module, "THUMB_WORKERS", 2)
    monkeypatch.setattr(media, "FACES", False)
    root = tmp_path / "Photos"
    for i in range(4):
        make_jpeg(root / f"p{i}.jpg", "2020:01:01 10:00:00", size=(64, 48))
    s = Scanner(tmp_path / "data", [str(root)])
    s.start()
    for _ in range(200):
        if s.snapshot()["last_finished"]:
            break
        time.sleep(0.05)
    s.stop()
    engine = importer.Importer(tmp_path / "data")
    big = tmp_path / "card" / "a.jpg"
    big.parent.mkdir()
    big.write_bytes(os.urandom(3 * importer.CHUNK))
    engine.start([str(tmp_path / "card")], str(tmp_path / "archive"), ["image"], "copy")
    engine.wait(30)
    assert {"scanner", "thumbnails", "importer"} <= lowered
    fresh: list[bool] = []
    t = threading.Thread(target=lambda: fresh.append(parallel.in_background()))
    t.start()
    t.join()
    assert fresh == [False]                           # a request's thread is untouched


def test_a128_a_scans_ffmpeg_is_started_below_normal_on_windows(tmp_path, monkeypatch):
    flags: list[int] = []

    class Done:
        returncode, stdout = 1, b""

    def run(args, **kwargs):
        flags.append(kwargs.get("creationflags", 0))
        return Done()

    monkeypatch.setattr(media, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", run)
    monkeypatch.setattr(media.sys, "platform", "win32")
    gallery = threading.Thread(target=media.video_frame, args=(str(tmp_path / "v.mp4"),))
    gallery.start()                                             # asked for by the gallery
    gallery.join()
    t = threading.Thread(target=lambda: (setattr(parallel._marks, "background", True),
                                         media.video_frame(str(tmp_path / "v.mp4"))))
    t.start()
    t.join()
    below = parallel.WIN_BELOW_NORMAL_CLASS
    assert flags and not any(f & below for f in flags[:2]) and all(f & below for f in flags[2:])


# --- A129: an import lets the scan get on with thumbnails ------------------------------------------


def test_a129_an_import_asks_the_library_to_look_now_and_then_not_every_500_files(
        tmp_path, monkeypatch):
    card = tmp_path / "card"
    for i in range(1200):
        (card / f"d{i // 300}").mkdir(parents=True, exist_ok=True)
        (card / f"d{i // 300}" / f"IMG_{i:04}.jpg").write_bytes(
            b"\xff\xd8\xff" + i.to_bytes(4, "big") + bytes(importer.MIN_BYTES))
    looks: list[str] = []
    engine = importer.Importer(tmp_path / "data")
    engine.on_files = looks.append
    engine.start([str(card)], str(tmp_path / "archive"), ["image"], "copy")
    engine.wait(120)
    assert engine.progress()["phase"] == "done", engine.progress()
    assert len(looks) == 1                            # once at the end, within a minute

    looks.clear()
    engine.start([str(card)], str(tmp_path / "archive"), ["image"], "copy")   # all done already
    engine.wait(120)
    assert len(looks) == 1

    looks.clear()
    monkeypatch.setattr(importer, "SHOW_EVERY", 0)
    for i in range(3):
        (card / "new" / f"N{i}.jpg").parent.mkdir(exist_ok=True)
        (card / "new" / f"N{i}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([i]) * importer.MIN_BYTES)
    engine.start([str(card)], str(tmp_path / "archive"), ["image"], "copy")
    engine.wait(120)
    # Something new each time and no wait: a look after each of the three, and the last one.
    assert len(looks) == 4
