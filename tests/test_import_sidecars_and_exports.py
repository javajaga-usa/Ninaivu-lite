"""Data-safety findings of the 2026-10-10 audit (A137-A142)."""
import errno
import json
import shutil
import threading
import time

from ninaivu_lite import db, drives, importer
from ninaivu_lite.config import Config
from ninaivu_lite.importer import Importer

from test_import import noisy_jpeg, rows, run


def test_a137_a_reimport_never_writes_over_an_edited_sidecar(tmp_path):
    src, dest = tmp_path / "Card", tmp_path / "Archive"
    noisy_jpeg(src / "IMG_0001.JPG", "2019:05:12 10:00:00", seed=1)
    (src / "IMG_0001.xmp").write_text("<x>camera original</x>")
    run(tmp_path / "data", [src], dest)
    kept = dest / "2019" / "05" / "12" / "IMG_0001.xmp"
    assert kept.read_text() == "<x>camera original</x>"
    kept.write_text("<x>rating 5, faces tagged</x>")      # edited in the archive later
    importer.clear_report(db.connect(tmp_path / "data"))
    run(tmp_path / "data", [src], dest)
    assert kept.read_text() == "<x>rating 5, faces tagged</x>"


def test_a137_another_photos_sidecar_with_the_same_stem_is_kept(tmp_path):
    a, b, dest = tmp_path / "CameraA", tmp_path / "CameraB", tmp_path / "Archive"
    noisy_jpeg(a / "IMG_0001.JPG", "2019:05:12 10:00:00", seed=1)
    (a / "IMG_0001.xmp").write_text("<x>A</x>")
    run(tmp_path / "data", [a], dest)
    noisy_jpeg(b / "IMG_0001.jpeg", "2019:05:12 15:00:00", seed=2)
    (b / "IMG_0001.xmp").write_text("<x>B</x>")
    run(tmp_path / "data", [b], dest)
    day = dest / "2019" / "05" / "12"
    assert (day / "IMG_0001.xmp").read_text() == "<x>A</x>"
    # B's sidecar travels under the full-name form instead of being lost.
    assert (day / "IMG_0001.jpeg.xmp").read_text() == "<x>B</x>"


def test_a138_duplicates_found_in_another_archive_still_reach_this_one(tmp_path):
    laptop, card = tmp_path / "Laptop", tmp_path / "Card"
    first, second = tmp_path / "ArchiveA", tmp_path / "Pendrive"
    noisy_jpeg(laptop / "beach.jpg", "2019:05:12 10:00:00", seed=1)
    run(tmp_path / "data", [laptop], first)
    card.mkdir()
    shutil.copy2(laptop / "beach.jpg", card / "DSC_0042.jpg")
    noisy_jpeg(card / "sunset.jpg", "2019:05:12 18:00:00", seed=2)
    run(tmp_path / "data", [card], first)
    assert rows(tmp_path / "data")["DSC_0042.jpg"]["status"] == "duplicate"
    run(tmp_path / "data", [card], second)
    assert len(list(second.rglob("*.jpg"))) == 2


def _drive(root):
    return drives.Drive("x", str(root), "USB", 64 << 30, 60 << 30)


def test_a139_one_file_too_big_for_the_drive_does_not_stop_the_export(tmp_path, monkeypatch):
    lib = tmp_path / "Photos"
    for i, name in enumerate(["a.jpg", "big.jpg", "c.jpg", "d.jpg"]):
        noisy_jpeg(lib / name, seed=i)
    root = tmp_path / "USB"
    root.mkdir()
    real = drives._copy

    def fat32(src, dest, cancel):
        if src.endswith("big.jpg"):   # Windows calls a >4 GB file on FAT32 a full disk
            raise OSError(errno.ENOSPC, "There is not enough space on the disk")
        return real(src, dest, cancel)

    monkeypatch.setattr(drives, "_copy", fat32)
    ex = drives.Exporter()
    ex.start(_drive(root), [str(lib)], str(tmp_path / "data"))
    ex.wait(30)
    p = ex.progress()
    assert p["phase"] == "done" and p["too_big"] == 1
    assert (root / "Ninaivu Lite" / "Photos" / "d.jpg").exists()
    assert "4 GB" in p["message"]["text"]


def test_a139_one_file_too_big_does_not_stop_every_import(tmp_path, monkeypatch):
    src, dest = tmp_path / "Card", tmp_path / "Pendrive"
    for i, n in enumerate(["a.jpg", "b.jpg", "c.jpg"]):
        noisy_jpeg(src / n, f"2019:05:12 10:00:0{i}", seed=i)
    real = Importer._copy_and_hash

    def fat32(self, s, tmp):
        if s.endswith("b.jpg"):
            raise OSError(errno.ENOSPC, "There is not enough space on the disk")
        return real(self, s, tmp)

    monkeypatch.setattr(Importer, "_copy_and_hash", fat32)
    run(tmp_path / "data", [src], dest)
    assert (dest / "2019" / "05" / "12" / "c.jpg").exists()
    assert rows(tmp_path / "data")["b.jpg"]["status"] == "error"


def test_a140_a_drive_pulled_out_mid_export_is_not_done(tmp_path, monkeypatch):
    lib = tmp_path / "Photos"
    for i in range(6):
        noisy_jpeg(lib / f"p{i}.jpg", seed=i)
    root = tmp_path / "USB"
    root.mkdir()
    real, tries = drives._copy, []

    def pulled(src, dest, cancel):
        tries.append(src)
        if len(tries) > 2:
            shutil.rmtree(root, ignore_errors=True)
            raise OSError(errno.ENOENT, "No such device")
        return real(src, dest, cancel)

    monkeypatch.setattr(drives, "_copy", pulled)
    ex = drives.Exporter()
    ex.start(_drive(root), [str(lib)], str(tmp_path / "d"))
    ex.wait(30)
    p = ex.progress()
    assert p["phase"] == "failed" and "unplugged" in p["message"]["text"]
    assert len(tries) == 3


def test_a141_two_settings_changes_at_once_both_reach_the_disk(tmp_path, monkeypatch):
    c = Config(data_dir=str(tmp_path), folders=["/p"])
    c.save()
    real, gate = Config._write, threading.Event()

    def slow(self):
        if self.drives_never_ask == ["drive-A"]:
            gate.wait(1)
        real(self)

    monkeypatch.setattr(Config, "_write", slow)
    t = threading.Thread(target=lambda: c.update(drives_never_ask=["drive-A"]))
    t.start()
    time.sleep(0.1)
    other = threading.Thread(target=lambda: c.update(folders=["/p", "/q"]))
    other.start()
    gate.set()
    t.join()
    other.join()
    disk = json.loads((tmp_path / "settings.json").read_text())
    assert disk["folders"] == ["/p", "/q"] and disk["drives_never_ask"] == ["drive-A"]


def test_a142_the_estimate_steps_over_the_library_as_the_run_does(tmp_path):
    drive = tmp_path / "D"
    noisy_jpeg(drive / "old" / "a.jpg", seed=1)
    for i in range(3):
        noisy_jpeg(drive / "Photos" / f"lib{i}.jpg", seed=10 + i)
    e = Importer(tmp_path / "data")
    est = e.estimate([str(drive)], str(tmp_path / "Arch"), ["image", "video"], "t",
                     library=[str(drive / "Photos")])
    assert est["files"] == 1
