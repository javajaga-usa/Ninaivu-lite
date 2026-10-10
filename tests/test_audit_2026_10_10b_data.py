"""Data safety and file writes: the fifth audit of 2026-10-10 (after the
Rotate bar, index version 10)."""
from __future__ import annotations

import os
import sys
import time

from PIL import Image

from ninaivu_lite import db, media

from conftest import make_jpeg
from test_import import noisy_jpeg, rows, run, sha


def _edit_in_place(path):
    """What a photo editor does when it saves: new bytes, a new time."""
    time.sleep(0.05)
    with Image.open(path) as img:
        turned = img.rotate(90, expand=True)
    turned.save(path, "JPEG", quality=90)
    later = time.time() + 5
    os.utime(path, (later, later))


def test_an_archived_photo_edited_after_import_is_never_written_over(tmp_path):
    data, card, dest = tmp_path / "data", tmp_path / "Card", tmp_path / "Archive"
    source = noisy_jpeg(card / "IMG_0001.jpg", "2020:01:01 10:00:00", seed=1)
    run(data, [card], dest)
    archived = dest / "2020" / "01" / "01" / "IMG_0001.jpg"
    _edit_in_place(archived)                     # cropped or turned in an editor
    edited = sha(archived)
    run(data, [card], dest, "verify")
    assert rows(data)["IMG_0001.jpg"]["status"] == "error"
    again = run(data, [card], dest)
    assert again.job["phase"] == "done"
    assert sha(archived) == edited               # the family's edit is kept
    fresh = archived.parent / "IMG_0001_1.jpg"   # the original comes in beside it
    assert fresh.read_bytes() == source.read_bytes()
    assert rows(data)["IMG_0001.jpg"]["destination"] == str(fresh)


def test_a_copy_damaged_where_it_lies_is_still_replaced(tmp_path):
    """Bit rot or a bad sector: the bytes change, the file's size and time do not."""
    data, card, dest = tmp_path / "data", tmp_path / "Card", tmp_path / "Archive"
    source = noisy_jpeg(card / "IMG_0001.jpg", "2020:01:01 10:00:00", seed=1)
    run(data, [card], dest)
    archived = dest / "2020" / "01" / "01" / "IMG_0001.jpg"
    st = archived.stat()
    archived.write_bytes(archived.read_bytes()[:-10] + b"\0" * 10)
    os.utime(archived, ns=(st.st_atime_ns, st.st_mtime_ns))
    run(data, [card], dest, "verify")
    run(data, [card], dest)
    assert archived.read_bytes() == source.read_bytes()
    assert not (archived.parent / "IMG_0001_1.jpg").exists()


def test_the_faces_pass_never_overrides_a_turn_set_by_hand_meanwhile(app, library, monkeypatch):
    """Someone turns (and flips) a photograph while the quiet pass after a
    scan is still looking at its faces: the person's turn stands."""
    root, _ = library
    make_jpeg(root / "scan.jpg", size=(800, 500))
    scanner = app.config["SCANNER"]
    data_dir = app.config["LITE"].data_dir
    monkeypatch.setattr(media, "FACES", True)

    def detect(path):
        if path.endswith("scan.jpg"):
            c = db.connect(data_dir)
            asset = c.execute("SELECT id FROM assets WHERE name = 'scan.jpg'").fetchone()[0]
            scanner.set_rotation(c, asset, 180, "manual", remake=False, mirror=True)
            c.close()
            return 90
        return 0

    monkeypatch.setattr(media, "detect_rotation", detect)
    scanner.scan_once(db.connect(data_dir))
    row = db.connect(data_dir).execute("SELECT * FROM assets WHERE name = 'scan.jpg'").fetchone()
    assert (row["rotation"], row["mirror"], row["rot_source"]) == (180, 1, "manual")
    assert (row["width"], row["height"]) == (800, 500)


def test_turns_and_flips_set_by_hand_move_up_to_ninaivu(app, admin):
    """They live only in the index, so the export is their one way across."""
    import json
    from conftest import ids
    beach, clip = ids(app)["beach.jpg"], ids(app)["clip.mp4"]
    assert admin.post(f"/api/asset/{beach}/rotate",
                      json={"rotation": 90, "mirror": True}).status_code == 200
    assert admin.post(f"/api/asset/{clip}/rotate", json={"rotation": 180}).status_code == 200
    out = json.loads(admin.get("/admin/export").data)
    turns = {t["path"].rsplit("/", 1)[-1]: (t["rotation"], t["mirror"]) for t in out["turns"]}
    assert turns == {"beach.jpg": (90, True), "clip.mp4": (180, False)}
    # Reset to original: nothing left to carry.
    admin.post(f"/api/asset/{beach}/rotate", json={"rotation": 0, "mirror": False})
    out = json.loads(admin.get("/admin/export").data)
    assert [t["path"].rsplit("/", 1)[-1] for t in out["turns"]] == ["clip.mp4"]


def test_two_copies_before_a_change_in_the_same_second_both_stay(tmp_path, monkeypatch):
    from datetime import datetime as real

    from ninaivu_lite import backups

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 10, 14, 30, 42)

    data = tmp_path / "data"
    db.connect(data).close()
    monkeypatch.setattr(backups, "datetime", Frozen)
    first = backups.before_change(data, "deleting-person")
    second = backups.before_change(data, "deleting-person")
    assert first != second and first.is_file() and second.is_file()
    assert second.name == "before-deleting-person-2026-10-10-143042-2.zip"
    assert backups._taken_at(second) > backups._taken_at(first)
    # Pruning keeps the five taken last, whatever each was taken before, and
    # a name pruning frees is never given to a later copy (which would then
    # sort as one of the oldest and could be the next to go).
    made = [first, second] + [backups.before_change(data, "removing-folder") for _ in range(5)]
    assert len({p.name for p in made}) == len(made)
    left = sorted(p.name for p in (data / "backups").glob("before-*.zip"))
    assert left == sorted(p.name for p in made[-backups.KEEP_BEFORE:])


def test_fat_and_exfat_are_known_from_the_mount_table(tmp_path):
    import pytest

    from ninaivu_lite import drives
    if not sys.platform.startswith("linux"):
        # Linux's mount table and Linux paths; elsewhere the check is off.
        pytest.skip("reads the mount table the way Linux writes it")
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n"
                      "/dev/sdb1 /media/me/MY\\040STICK vfat rw 0 0\n"
                      "/dev/sdc1 /media/me/CARD exfat rw 0 0\n"
                      "/dev/sdd1 /media/me/DISK ntfs3 rw 0 0\n")
    assert drives.fat_like("/media/me/MY STICK/Ninaivu Lite", str(mounts))
    assert drives.fat_like("/media/me/CARD", str(mounts))
    assert not drives.fat_like("/media/me/DISK", str(mounts))
    assert not drives.fat_like("/home/me", str(mounts))
    assert not drives.fat_like("/x", str(tmp_path / "none"))


def test_names_a_fat_drive_refuses_are_exported_under_safe_ones(tmp_path, monkeypatch):
    from ninaivu_lite import drives
    lib = tmp_path / "Photos"
    try:
        noisy_jpeg(lib / "Trip: Goa" / "sunset?.jpg", seed=1)
    except OSError:
        import pytest
        pytest.skip("this filesystem cannot hold such names")
    noisy_jpeg(lib / "plain.jpg", seed=2)
    root = tmp_path / "USB"
    root.mkdir()
    monkeypatch.setattr(drives, "fat_like", lambda path: True)
    for _ in range(2):          # the second export finds them there
        ex = drives.Exporter()
        ex.start(drives.Drive("x", str(root), "USB", 64 << 30, 60 << 30), [str(lib)],
                 str(tmp_path / "data"))
        ex.wait(30)
    p = ex.progress()
    assert p["phase"] == "done" and p["errors"] == 0 and p["skipped"] == 2
    out = root / "Ninaivu Lite" / "Photos"
    assert (out / "Trip_ Goa" / "sunset_.jpg").read_bytes() == \
        (lib / "Trip: Goa" / "sunset?.jpg").read_bytes()
    assert (out / "plain.jpg").is_file()
