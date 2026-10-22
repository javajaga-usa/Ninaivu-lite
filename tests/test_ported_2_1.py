"""Fixes ported from Ninaivu 1.12 to 2.1 (October 2026): the import's count
(every file, one walk however many pages ask, no server thread held for the
walk), a quick dry run, a manifest a spreadsheet cannot run, and the viewer's
visibility change settled before the gallery reloads."""

from __future__ import annotations

import csv
import io
import threading
import time
from pathlib import Path

import pytest
from test_import import noisy_jpeg, rows, run

from ninaivu_lite import db, import_copy, import_walk, importer
from ninaivu_lite.importer import Importer

ROOT = Path(__file__).resolve().parent.parent


# --- the count ---------------------------------------------------------------------------


def tiny_drive(root: Path, files: int, folders: int = 1) -> Path:
    for n in range(files):
        folder = root / f"d{n % folders:03}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"IMG_{n:05}.jpg").write_bytes(b"\xff\xd8\xff" + bytes(30_000))
    return root


def test_the_count_no_longer_stops_at_200000(tmp_path, monkeypatch):
    """The cap is gone, not only raised: a smaller stand-in limit would have
    stopped this count of 450 files had it still been read."""
    assert not hasattr(importer, "ESTIMATE_CAP")
    drive = tiny_drive(tmp_path / "Drive", 450)
    result = Importer(tmp_path / "data").estimate([str(drive)], str(tmp_path / "Archive"),
                                                  ["image"], "t")
    assert result["ok"] and result["files"] == 450 and result["truncated"] is False


class SlowWalk:
    """Holds the walk at its first file until released, counting the walks."""

    def __init__(self, monkeypatch):
        self.walks = 0
        self.release = threading.Event()
        real = Importer._walk

        def walk(engine, *args, **kwargs):
            self.walks += 1
            for n, item in enumerate(real(engine, *args, **kwargs)):
                if n == 0:
                    self.release.wait(10)
                yield item

        monkeypatch.setattr(Importer, "_walk", walk)


def test_a_slow_count_answers_pending_and_frees_the_request(tmp_path, monkeypatch):
    drive = tiny_drive(tmp_path / "Drive", 3)
    slow = SlowWalk(monkeypatch)
    engine = Importer(tmp_path / "data")
    args = ([str(drive)], str(tmp_path / "Archive"), ["image"])
    started = time.monotonic()
    first = engine.ask_estimate(*args, "page-1", wait=0.2)
    assert first == {"ok": False, "pending": True}
    assert time.monotonic() - started < 5
    slow.release.set()
    done = engine.ask_estimate(*args, "page-1", wait=10)
    assert done["ok"] and done["files"] == 3
    assert slow.walks == 1


def test_a_reload_joins_the_count_under_way_and_reuses_a_finished_one(tmp_path, monkeypatch):
    drive = tiny_drive(tmp_path / "Drive", 3)
    slow = SlowWalk(monkeypatch)
    engine = Importer(tmp_path / "data")
    args = ([str(drive)], str(tmp_path / "Archive"), ["image"])
    assert engine.ask_estimate(*args, "before-reload", wait=0.1)["pending"]
    # The reloaded page has a new token but the same folders: no second walk.
    assert engine.ask_estimate(*args, "after-reload", wait=0.1)["pending"]
    assert engine.estimate_progress("after-reload")["known"]
    slow.release.set()
    assert engine.ask_estimate(*args, "after-reload", wait=10)["files"] == 3
    # Asked again within ten minutes, the finished count is handed back.
    assert engine.ask_estimate(*args, "third-page", wait=10)["files"] == 3
    assert slow.walks == 1
    # Other folders are another count.
    other = tiny_drive(tmp_path / "Other", 2)
    assert engine.ask_estimate([str(other)], args[1], args[2], "x", wait=10)["files"] == 2
    assert slow.walks == 2


def test_one_page_cancelling_leaves_the_other_pages_count_going(tmp_path, monkeypatch):
    drive = tiny_drive(tmp_path / "Drive", 3)
    slow = SlowWalk(monkeypatch)
    engine = Importer(tmp_path / "data")
    args = ([str(drive)], str(tmp_path / "Archive"), ["image"])
    engine.ask_estimate(*args, "tab-1", wait=0.1)
    engine.ask_estimate(*args, "tab-2", wait=0.1)
    engine.cancel_estimate("tab-1")
    slow.release.set()
    assert engine.ask_estimate(*args, "tab-2", wait=10)["files"] == 3


def test_a_count_nobody_watches_is_stopped(tmp_path, monkeypatch):
    drive = tiny_drive(tmp_path / "Drive", 1000, folders=50)
    monkeypatch.setattr(import_walk, "ESTIMATE_ABANDONED", 0.05)
    engine = Importer(tmp_path / "data")
    real = Importer._wanted

    def wanted(*args):
        time.sleep(0.001)
        return real(*args)

    monkeypatch.setattr(Importer, "_wanted", staticmethod(wanted))
    assert engine.ask_estimate([str(drive)], str(tmp_path / "Archive"), ["image"], "gone",
                               wait=0)["pending"]
    snapshot = engine._estimates["gone"]
    assert snapshot["done"].wait(20)
    assert snapshot["cancelled"] and snapshot["files"] < 1000


def test_starting_a_run_stops_a_count_and_forgets_finished_ones(tmp_path, monkeypatch):
    drive = tiny_drive(tmp_path / "Drive", 3)
    engine = Importer(tmp_path / "data")
    args = ([str(drive)], str(tmp_path / "Archive"), ["image"])
    assert engine.ask_estimate(*args, "t", wait=10)["ok"]
    engine.start(*args, "copy")
    engine.wait(60)
    assert "t" not in engine._estimates


def test_the_route_asks_without_holding_the_request(tmp_path, monkeypatch):
    source = (ROOT / "ninaivu_lite" / "api_import.py").read_text(encoding="utf-8")
    assert "engine().ask_estimate(" in source and "engine().estimate(" not in source
    page = (ROOT / "ninaivu_lite" / "static" / "js" / "archive.js").read_text(encoding="utf-8")
    assert "while (result.pending && this.estimateToken === token)" in page


# --- a quick dry run ------------------------------------------------------------------------


def test_a_dry_run_reads_only_files_whose_size_matches(tmp_path, monkeypatch):
    src = tmp_path / "src"
    for n in range(6):
        noisy_jpeg(src / f"IMG_{n}.jpg", "2020:01:01 10:00:00", seed=n + 1, size=(640 + n, 480))
    twin = src / "z_twin.jpg"
    twin.write_bytes((src / "IMG_2.jpg").read_bytes())
    stranger = src / "zz_same_size.jpg"
    data = bytearray((src / "IMG_4.jpg").read_bytes())
    data[-3] ^= 0xFF
    stranger.write_bytes(bytes(data))
    read: list[str] = []
    real = import_copy.hash_file

    def counted(path, gate=None):
        read.append(Path(path).name)
        return real(path, gate)

    monkeypatch.setattr(import_copy, "hash_file", counted)
    dest = tmp_path / "Archive"
    run(tmp_path / "data", [src], dest, "dry-run")
    planned = rows(tmp_path / "data")
    assert planned["z_twin.jpg"]["status"] == "plan-duplicate"
    assert planned["zz_same_size.jpg"]["status"] == "planned"
    # Only the two pairs of a size were read, not all eight files.
    assert sorted(set(read)) == ["IMG_2.jpg", "IMG_4.jpg", "z_twin.jpg", "zz_same_size.jpg"]

    monkeypatch.setattr(import_copy, "hash_file", real)
    run(tmp_path / "data", [src], dest)
    real_rows = rows(tmp_path / "data")
    for name, row in planned.items():
        assert real_rows[name]["status"] in ("verified", "duplicate")
        if row["status"] == "planned":
            assert real_rows[name]["destination"] == row["destination"]
        else:
            assert real_rows[name]["status"] == "duplicate"


def test_a_dry_run_still_sees_a_copy_already_at_the_name(tmp_path):
    src = tmp_path / "src"
    photo = noisy_jpeg(src / "IMG_1.jpg", "2020:01:01 10:00:00", seed=1)
    day = tmp_path / "Archive" / "2020" / "01" / "01"
    day.mkdir(parents=True)
    (day / "IMG_1.jpg").write_bytes(photo.read_bytes())       # put there by hand
    run(tmp_path / "data", [src], tmp_path / "Archive", "dry-run")
    assert rows(tmp_path / "data")["IMG_1.jpg"]["status"] == "plan-duplicate"


# --- the manifest -------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["=HYPERLINK(\"x\")", "+1", "-2", "@SUM(A1)", "\tx"])
def test_a_manifest_cell_never_starts_like_a_formula(text):
    from ninaivu_lite.api_import import cell
    assert cell(text) == "'" + text
    assert cell("/Volumes/Drive/IMG_1.jpg") == "/Volumes/Drive/IMG_1.jpg"
    assert cell(1234) == 1234


def test_the_manifest_route_writes_safe_cells(tmp_path, monkeypatch):
    from ninaivu_lite import api_import
    c = db.connect(tmp_path)
    with c:
        c.execute("INSERT INTO import_files (source, name, size, status, updated_at, destination) "
                  "VALUES ('=cmd|x', 'x', 1, 'verified', 0, '+dest')")
    out = io.StringIO()
    csv.writer(out).writerows([api_import.cell(v) for v in row]
                              for row in importer.manifest_rows(c))
    assert out.getvalue().startswith("'=cmd|x,'+dest,")


# --- the viewer ---------------------------------------------------------------------------


def test_closing_the_viewer_waits_for_the_visibility_change():
    js = (ROOT / "ninaivu_lite" / "static" / "js" / "gallery" / "visibility.js").read_text(
        encoding="utf-8")
    marked = js.index("visibilityChanged = true;")
    awaited = js.index("await change;")
    assert marked < awaited, "the flag is set before the answer comes back"
    assert "visibilitySaving.then(() => reload())" in js
