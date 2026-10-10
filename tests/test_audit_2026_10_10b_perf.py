"""Turning many photographs at once (Rotate on a selection) writes the index
once, not once a photograph; the thumbnails made again are written down in
batches; a video's poster sent while it was turned is not kept the old way up."""

from __future__ import annotations

import io
import sqlite3
import threading
import time

from conftest import ids
from PIL import Image

from ninaivu_lite import api_gallery, db, media
from ninaivu_lite import scanner as scanner_module


def wait_for(check, seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def test_turning_a_selection_is_one_commit(app, admin, monkeypatch):
    statements: list[str] = []
    real_conn = api_gallery.conn

    def traced():
        c: sqlite3.Connection = real_conn()
        c.set_trace_callback(statements.append)
        return c

    monkeypatch.setattr(api_gallery, "conn", traced)
    monkeypatch.setattr(app.config["SCANNER"], "remake_later", lambda found: None)
    chosen = [i for name, i in ids(app).items() if name.endswith(".jpg")]
    r = admin.post("/api/assets/rotate", json={"ids": chosen, "turn": 180})
    assert r.status_code == 200 and r.json["updated"] == len(chosen)
    assert sum(1 for s in statements if s.strip().upper() == "COMMIT") == 1
    conn = db.connect(app.config["LITE"].data_dir)
    turned = conn.execute("SELECT COUNT(*) FROM assets WHERE rotation = 180 AND thumb = 0 "
                          "AND rot_source = 'manual'").fetchone()[0]
    assert turned == len(chosen)
    # The old thumbnails are gone, as for one turned in the viewer.
    s = app.config["SCANNER"]
    assert not media.thumb_path(s.thumbs_dir, ids(app)["beach.jpg"], "s").exists()


def test_a_selection_turn_keeps_a_turn_set_by_hand_from_the_face_check(app):
    s = app.config["SCANNER"]
    conn = db.connect(app.config["LITE"].data_dir)
    target = ids(app)["beach.jpg"]
    s.set_rotation(conn, target, 90, "manual", remake=False)
    assert s.set_rotations(conn, [(target, 270)], "faces") == []
    row = conn.execute("SELECT rotation, rot_source FROM assets WHERE id = ?", (target,)).fetchone()
    assert (row["rotation"], row["rot_source"]) == (90, "manual")


def test_remade_thumbnails_are_written_down_in_batches(app, monkeypatch):
    s = app.config["SCANNER"]
    conn = db.connect(app.config["LITE"].data_dir)
    chosen = [i for name, i in ids(app).items() if name.endswith(".jpg") and name != "broken.jpg"]
    monkeypatch.setattr(scanner_module, "RECORD_EVERY_SECONDS", 60.0)
    writes: list[int] = []
    real = scanner_module.Scanner._record_many
    monkeypatch.setattr(scanner_module.Scanner, "_record_many", staticmethod(
        lambda c, made: (writes.append(len(made)), real(c, made))[1]))
    assert sorted(s.set_rotations(conn, [(i, 90) for i in chosen], "manual")) == sorted(chosen)
    s.remake_later(chosen)
    for worker in [t for t in threading.enumerate() if t.name == "remake-thumbnails"]:
        worker.join(20)
    wait_for(lambda: s._remaker is False)
    assert writes == [len(chosen)]
    assert not conn.execute(
        f"SELECT 1 FROM assets WHERE thumb = 0 AND id IN ({','.join('?' * len(chosen))})",
        chosen).fetchone()


def test_a_poster_sent_while_the_video_is_turned_is_not_kept(app, admin, monkeypatch):
    target = ids(app)["clip.mp4"]
    s = app.config["SCANNER"]
    monkeypatch.setattr(media, "FFMPEG", None)
    real = media.save_thumbnails

    def turned_meanwhile(img, thumbs_dir, asset_id, sizes=("s", "l")):
        colour = real(img, thumbs_dir, asset_id, sizes)
        # Turned in the viewer while this browser's poster was being saved.
        s.set_rotation(db.connect(app.config["LITE"].data_dir), target, 90, "manual",
                       remake=False)
        return colour

    monkeypatch.setattr(api_gallery.media, "save_thumbnails", turned_meanwhile)
    sent = io.BytesIO()
    Image.new("RGB", (320, 180), (90, 90, 90)).save(sent, "JPEG")
    admin.post(f"/api/asset/{target}/poster", data=sent.getvalue(), content_type="image/jpeg")
    row = db.connect(app.config["LITE"].data_dir).execute(
        "SELECT thumb, rotation FROM assets WHERE id = ?", (target,)).fetchone()
    assert (row["thumb"], row["rotation"]) == (0, 90)
