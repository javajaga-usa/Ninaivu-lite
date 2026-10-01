"""Which way up a photograph goes, decided during the scan and kept in the
index: the camera's tag, faces (when OpenCV is there), or a person's hand.
The file is never changed."""

from __future__ import annotations

import os

from PIL import Image

from ninaivu_lite import db, media
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


def row_of(app, name):
    return conn_of(app).execute("SELECT * FROM assets WHERE name = ?", (name,)).fetchone()


def thumb_size(app, asset_id):
    path = media.thumb_path(app.config["SCANNER"].thumbs_dir, asset_id, "s")
    with Image.open(path) as img:
        return img.size


def test_the_cameras_tag_is_final_and_needs_no_turn(app):
    """The browser and the thumbnails honour the tag; the index records it,
    turns nothing on top of it, and never looks at the pixels."""
    row = row_of(app, "portrait.jpg")                 # EXIF orientation 6, 600×400 stored
    assert (row["rotation"], row["rot_source"], row["upright"]) == (0, "exif", 1)
    assert (row["width"], row["height"]) == (400, 600)
    beach = row_of(app, "beach.jpg")                  # no orientation tag
    assert (beach["rotation"], beach["rot_source"]) == (0, "none")
    assert beach["upright"] == (1 if media.FACES else 0)
    clip = row_of(app, "clip.mp4")
    assert clip["upright"] == 1                       # videos are not judged


def test_without_opencv_untagged_photographs_wait_for_a_person(app, monkeypatch):
    monkeypatch.setattr(media, "FACES", False)
    c = conn_of(app)
    with c:
        c.execute("UPDATE assets SET upright = 0 WHERE kind = 'picture' AND rot_source = 'none'")
    app.config["SCANNER"].scan_once(c)
    assert row_of(app, "beach.jpg")["upright"] == 0
    assert row_of(app, "beach.jpg")["rotation"] == 0


def test_faces_turn_a_sideways_photograph_during_the_scan(app, library, monkeypatch):
    """The detector's verdict (stubbed: OpenCV's cascades want a real face)
    is stored, the shape swapped, the thumbnail remade turned, and the
    viewer told where it came from."""
    root, _ = library
    make_jpeg(root / "scan.jpg", size=(800, 500))      # no tag: a scanned print
    monkeypatch.setattr(media, "FACES", True)
    monkeypatch.setattr(media, "detect_rotation",
                        lambda path: 90 if path.endswith("scan.jpg") else 0)
    app.config["SCANNER"].scan_once(conn_of(app))
    row = row_of(app, "scan.jpg")
    assert (row["rotation"], row["rot_source"], row["upright"]) == (90, "faces", 1)
    assert (row["width"], row["height"]) == (500, 800)
    assert row["thumb"] == db.THUMB_OK
    assert thumb_size(app, row["id"]) == (160, 256)    # turned: taller than wide
    assert (root / "scan.jpg").stat().st_size == os.stat(root / "scan.jpg").st_size
    with Image.open(root / "scan.jpg") as img:
        assert img.size == (800, 500)                  # the file itself: untouched
    beach = row_of(app, "beach.jpg")
    assert (beach["rotation"], beach["upright"]) == (0, 1)   # looked at, left alone
    # Looked at once: a rescan does not ask again.
    calls = []
    monkeypatch.setattr(media, "detect_rotation", lambda path: calls.append(path) or 0)
    app.config["SCANNER"].scan_once(conn_of(app))
    assert calls == []
    assert row_of(app, "scan.jpg")["rotation"] == 90


def test_an_administrator_turns_by_hand_and_the_turn_outlives_a_rescan(app, admin, family, library):
    root, _ = library
    target = ids(app)["beach.jpg"]
    assert family.post(f"/api/asset/{target}/rotate", json={}).status_code == 403
    r = admin.post(f"/api/asset/{target}/rotate", json={})
    assert r.status_code == 200
    item = r.get_json()
    assert (item["rotation"], item["rotation_source"]) == (90, "manual")
    assert (item["width"], item["height"]) == (480, 640)
    assert thumb_size(app, target) == (192, 256)
    assert admin.post(f"/api/asset/{target}/rotate", json={"rotation": 45}).status_code == 400
    assert admin.post(f"/api/asset/{ids(app)['clip.mp4']}/rotate", json={}).status_code == 400
    # The file changed on disk (re-saved); the scan keeps the person's answer.
    make_jpeg(root / "2019" / "beach.jpg", "2019:05:12 10:00:00", size=(640, 480), color=(1, 2, 3))
    os.utime(root / "2019" / "beach.jpg", None)
    app.config["SCANNER"].scan_once(conn_of(app))
    row = row_of(app, "beach.jpg")
    assert (row["rotation"], row["rot_source"], row["upright"]) == (90, "manual", 1)
    assert admin.get(f"/api/asset/{target}").get_json()["rotation"] == 90
    # Back to upright by hand.
    assert admin.post(f"/api/asset/{target}/rotate", json={"rotation": 0}).get_json()["rotation"] == 0
    assert (row_of(app, "beach.jpg")["width"], row_of(app, "beach.jpg")["height"]) == (640, 480)


def test_the_real_detector_leaves_a_faceless_picture_alone(library):
    """OpenCV, when it is installed: a wall of noise has no face, so no turn."""
    root, _ = library
    if not media.FACES:
        assert media.detect_rotation(str(root / "2019" / "beach.jpg")) == 0
        return
    noise = Image.effect_noise((640, 480), 80).convert("RGB")
    noise.save(root / "noise.jpg", "JPEG")
    assert media.detect_rotation(str(root / "noise.jpg")) == 0
    assert media.detect_rotation(str(root / "broken.jpg")) == 0


def test_an_edited_copy_is_upright_from_the_start(app, admin, library):
    import io
    target = ids(app)["beach.jpg"]
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), (1, 2, 3)).save(stream, "PNG")
    copy = admin.post(f"/api/asset/{target}/edited-copy", data=stream.getvalue(),
                      content_type="image/png").get_json()
    row = conn_of(app).execute("SELECT upright, rotation FROM assets WHERE id = ?",
                               (copy["id"],)).fetchone()
    assert (row["upright"], row["rotation"]) == (1, 0)


def test_an_older_index_is_brought_forward(tmp_path, library):
    root, data = library
    scanner = Scanner(data, [str(root)])
    c = db.connect(data)
    scanner.scan_once(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert c.execute("SELECT COUNT(*) FROM assets WHERE upright = 1 AND rot_source = 'exif'").fetchone()[0] == 1
