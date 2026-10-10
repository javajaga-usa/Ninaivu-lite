"""Data safety and file writes: the fifth audit of 2026-10-10 (after the
Rotate bar, index version 10)."""
from __future__ import annotations

import os
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
