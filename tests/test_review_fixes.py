"""What a review of the server found, kept fixed: a scan that never spun, a
drive that may come back, a dry run that agrees with itself, a lockout no
flood can wash away, and a shared photograph the right way up."""

from __future__ import annotations

import io
import os
import threading

from PIL import Image

from ninaivu_lite import auth, db
from ninaivu_lite.importer import Importer
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg


def test_the_finishing_pass_ends_when_a_file_is_away(tmp_path):
    root, data = tmp_path / "Photos", tmp_path / "data"
    make_jpeg(root / "a.jpg", "2019:05:12 10:00:00")
    scanner = Scanner(data, [str(root)])
    conn = db.connect(data)
    scanner.scan_once(conn)
    conn.execute("UPDATE assets SET large = 0")
    conn.commit()
    os.unlink(root / "a.jpg")                            # the drive went away
    thread = threading.Thread(target=scanner._make_thumbnails, args=(conn,), daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()                         # it used to spin here at 100% CPU
    row = conn.execute("SELECT thumb, large FROM assets").fetchone()
    assert (row["thumb"], row["large"]) == (db.THUMB_OK, 0)   # still wanted, not written off


def test_a_photograph_whose_drive_was_asleep_gets_its_thumbnail_when_it_returns(tmp_path):
    root, data = tmp_path / "Photos", tmp_path / "data"
    make_jpeg(root / "a.jpg", "2019:05:12 10:00:00")
    scanner = Scanner(data, [str(root)])
    conn = db.connect(data)
    scanner.scan_once(conn)
    saved = (root / "a.jpg").read_bytes()
    conn.execute("UPDATE assets SET thumb = 0, large = 0")
    conn.commit()
    os.unlink(root / "a.jpg")                            # away during the thumbnail pass
    scanner._make_thumbnails(conn)
    assert conn.execute("SELECT thumb FROM assets").fetchone()[0] == 0   # not THUMB_NONE
    (root / "a.jpg").write_bytes(saved)                  # back
    scanner._make_thumbnails(conn)
    assert conn.execute("SELECT thumb FROM assets").fetchone()[0] == db.THUMB_OK


def test_a_file_that_truly_cannot_be_read_is_final_until_it_changes(tmp_path):
    root, data = tmp_path / "Photos", tmp_path / "data"
    (root).mkdir()
    (root / "bad.jpg").write_bytes(b"\xff\xd8\xff not a picture")
    scanner = Scanner(data, [str(root)])
    conn = db.connect(data)
    scanner.scan_once(conn)
    assert conn.execute("SELECT thumb FROM assets").fetchone()[0] == db.THUMB_NONE


def test_a_second_dry_run_plans_the_same_names(tmp_path):
    src, dest, data = tmp_path / "src", tmp_path / "dest", tmp_path / "data"
    make_jpeg(src / "a.jpg", "2019:05:12 10:00:00", size=(2000, 1500))
    conn = db.connect(data)
    importer = Importer(data)
    plans = []
    for _ in range(2):
        importer.start([str(src)], str(dest), ["image", "video"], "dry-run")
        importer.wait()
        plans.append(conn.execute("SELECT destination FROM import_files").fetchone()[0])
    assert plans[0] == plans[1]
    assert plans[0].endswith("a.jpg")                    # not a_1.jpg


def test_a_flood_of_failures_does_not_wash_a_lockout_away():
    throttle = auth.Throttle(limit=3, window=300)
    for _ in range(3):
        throttle.fail("admin")
    assert throttle.blocked("admin")
    for n in range(10_050):
        throttle.fail(f"decoy-{n}")
    assert throttle.blocked("admin")                     # used to be forgotten here
    # And the stale decoys do go: the table is bounded.
    assert len(throttle._fails) <= 10_051


def test_a_shared_photograph_comes_out_the_way_up_the_index_says(app, admin, library):
    target = ids(app)["beach.jpg"]                       # 640×480, no tag
    assert admin.post(f"/api/asset/{target}/rotate", json={"rotation": 90}).status_code == 200
    r = admin.post("/api/shares", json={"scope": "asset", "target_id": target, "expires_in": 3600})
    assert r.status_code in (200, 201), r.get_json()
    share = r.get_json()
    token = share.get("token") or share.get("share", {}).get("token")
    assert token, share
    anyone = app.test_client()
    served = anyone.get(f"/api/share/{token}/file/{target}")
    assert served.status_code == 200
    with Image.open(io.BytesIO(served.data)) as img:
        assert img.size == (480, 640)                    # turned, as the share page is told
    preview = anyone.get(f"/api/share/{token}/preview/{target}")
    with Image.open(io.BytesIO(preview.data)) as img:
        assert img.size == (480, 640)
