"""Rotation and gallery hand-offs: what a turn or flip saved while thumbnails
were being made, a large selection, album covers, video posters and shared
albums do with it."""

from __future__ import annotations

import io
import threading
from pathlib import Path

from conftest import ids
from PIL import Image

from ninaivu_lite import db, media
from ninaivu_lite import scanner as scanner_module

STATIC = Path(__file__).resolve().parent.parent / "ninaivu_lite" / "static"


def size_of(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_a_turn_saved_while_a_thumbnail_is_made_is_not_lost(app, admin, monkeypatch):
    target = ids(app)["beach.jpg"]
    scanner = app.config["SCANNER"]
    data = app.config["LITE"].data_dir
    made = media.make_thumbnails
    turned = []

    def turned_meanwhile(*args, **kwargs):
        # Somebody saves a quarter turn while this one is still being made
        # the old way up.
        if not turned:
            turned.append(scanner.set_rotation(db.connect(data), target, 90, "manual",
                                               remake=False))
        return made(*args, **kwargs)

    monkeypatch.setattr(scanner_module.media, "make_thumbnails", turned_meanwhile)
    conn = db.connect(data)
    scanner._thumbnail(conn, scanner._now_row(conn, target), ("s", "l"))
    assert turned == [True]
    row = conn.execute("SELECT thumb, large FROM assets WHERE id = ?", (target,)).fetchone()
    assert (row["thumb"], row["large"]) == (db.THUMB_PENDING, 0)
    # The next request makes it the new way up (portrait).
    assert size_of(admin.get(f"/api/thumb/{target}?s=256").data) == (192, 256)


def test_background_remakes_share_one_worker_and_make_each_once(app, monkeypatch):
    i = ids(app)
    scanner = app.config["SCANNER"]
    conn = db.connect(app.config["LITE"].data_dir)
    chosen = [i["beach.jpg"], i["sunset.jpg"], i["IMG_20230115_091500.jpg"]]
    for asset_id in chosen:
        scanner.set_rotation(conn, asset_id, 90, "manual", remake=False)
    gate = threading.Event()
    rendered = []
    render = scanner._render

    def slow_render(row, sizes, threads=0):
        gate.wait(10)
        rendered.append(row["id"])
        return render(row, sizes, threads)

    monkeypatch.setattr(scanner, "_render", slow_render)
    before = {t.ident for t in threading.enumerate()}
    scanner.remake_later(chosen)
    scanner.remake_later(chosen)          # a second piece, or a second click
    workers = [t for t in threading.enumerate()
               if t.name == "remake-thumbnails" and t.ident not in before]
    assert len(workers) == 1
    gate.set()
    workers[0].join(10)
    assert sorted(rendered) == sorted(chosen)
    assert all(r["thumb"] == db.THUMB_OK for r in conn.execute(
        f"SELECT thumb FROM assets WHERE id IN ({','.join('?' * len(chosen))})", chosen))
    # Done, it lets go: the next turn starts a worker again.
    assert scanner._remaker is False


def test_a_large_selection_is_turned_in_pieces():
    api = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    line = next(x for x in api.splitlines() if "rotateMany:" in x)
    assert "inPieces(ids" in line


def test_album_covers_carry_their_thumbnail_version(app, admin):
    target = ids(app)["beach.jpg"]
    album = admin.post("/api/albums", json={"name": "Trip", "ids": [target]}).json
    album_id = album.get("id") or album.get("album", {}).get("id")
    listed = next(a for a in admin.get("/api/albums").json["albums"] if a["id"] == album_id)
    assert listed["cover_id"] == target
    turned = admin.post(f"/api/asset/{target}/rotate", json={"rotation": 90}).json
    listed = next(a for a in admin.get("/api/albums").json["albums"] if a["id"] == album_id)
    assert listed["cover_v"] == turned["thumb_v"]
    assert admin.get(f"/api/albums/{album_id}").json["album"]["cover_v"] == turned["thumb_v"]
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "thumbUrl(album.cover_id, 80, album.cover_v)" in app_js
    assert "thumbUrl(album.cover_id, 64, album.cover_v)" in app_js


def test_turning_a_video_keeps_the_poster_a_browser_made(app, admin, monkeypatch):
    target = ids(app)["clip.mp4"]
    monkeypatch.setattr(media, "FFMPEG", None)
    picture = Image.new("RGB", (320, 180), (240, 240, 240))
    picture.paste((0, 0, 0), (0, 0, 160, 180))        # dark on the left
    sent = io.BytesIO()
    picture.save(sent, "JPEG", quality=95)
    r = admin.post(f"/api/asset/{target}/poster", data=sent.getvalue(), content_type="image/jpeg")
    assert r.status_code == 201 and r.json["has_thumb"]

    item = admin.post(f"/api/asset/{target}/rotate", json={"rotation": 90}).json
    assert item["has_thumb"] and item["thumb_v"] != r.json["thumb_v"]
    with Image.open(io.BytesIO(admin.get(f"/api/thumb/{target}?s=256").data)) as thumb:
        assert thumb.size[1] > thumb.size[0]            # on its side
        grey = thumb.convert("L")
        assert grey.getpixel((thumb.width // 2, 10)) < 60    # dark half now on top
    # Flipped, then back to the file as it is: the poster as it was sent.
    admin.post(f"/api/asset/{target}/rotate", json={"rotation": 0, "mirror": True})
    item = admin.post(f"/api/asset/{target}/rotate", json={"rotation": 0, "mirror": False}).json
    assert item["has_thumb"]
    with Image.open(io.BytesIO(admin.get(f"/api/thumb/{target}?s=256").data)) as thumb:
        grey = thumb.convert("L")
        assert thumb.size[0] > thumb.size[1]
        assert grey.getpixel((10, thumb.height // 2)) < 60
        assert grey.getpixel((thumb.width - 10, thumb.height // 2)) > 200


def test_a_turned_video_in_a_shared_album_opens_on_the_share_page():
    share = (STATIC / "js" / "share.js").read_text(encoding="utf-8")
    album = share[share.index("function renderAlbum"):share.index("function renderOne")]
    assert "item.kind === 'video' && (item.rotation || item.mirror)" in album
    assert "#item=${item.id}" in album
    load = share[share.index("async function load"):]
    assert "data.items.find((item) => item.id === one)" in load


def test_leaving_while_a_save_is_on_its_way_does_not_say_not_saved():
    viewer = (STATIC / "js" / "viewer.js").read_text(encoding="utf-8")
    end = viewer[viewer.index("  endRotation() {"):]
    assert "this.turnChanged() && !this.rotatePending" in end[:400]


def test_a_turned_tiff_is_not_converted_again_on_every_other_look(app, admin, guest, library,
                                                                 monkeypatch):
    from ninaivu_lite import api_gallery
    from test_gallery_api import set_vis
    root, _ = library
    Image.new("RGB", (800, 600), (90, 60, 30)).save(root / "scan.tif")
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))
    target = ids(app)["scan.tif"]
    set_vis(app, ["scan.tif"], db.VIS_PUBLIC)
    admin.post(f"/api/asset/{target}/rotate", json={"rotation": 90})
    made = []
    convert = media.viewing_copy
    monkeypatch.setattr(api_gallery.media, "viewing_copy",
                        lambda *a, **k: made.append(1) or convert(*a, **k))
    for _ in range(3):
        # The family's viewer turns the copy itself; a guest's has the turn in it.
        assert size_of(admin.get(f"/api/preview/{target}").data) == (800, 600)
        view = guest.get(f"/api/asset/{target}").json["view"]
        assert size_of(guest.get(view).data) == (600, 800)
    assert len(made) == 2


def test_escape_leaves_a_turned_video_from_its_position_bar():
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    viewer_branch = app_js[app_js.index("    if (viewer.isOpen) {"):][:400]
    assert "event.key === 'Escape' && target.matches('input[type=\"range\"]')" in viewer_branch


def test_two_quick_selection_rotates_leave_the_thumbnail_the_new_way_up(app, admin,
                                                                        monkeypatch):
    target = ids(app)["beach.jpg"]
    scanner = app.config["SCANNER"]
    data = app.config["LITE"].data_dir
    render = scanner._render
    second = []

    def second_rotate_lands(row, sizes, threads=0):
        # The second Rotate right arrives while the first one's thumbnail
        # (on its side, 90) is being made.
        if not second:
            second.append(1)
            scanner.set_rotation(db.connect(data), target, 180, "manual", remake=False)
            scanner.remake_later([target])
        return render(row, sizes, threads)

    monkeypatch.setattr(scanner, "_render", second_rotate_lands)
    scanner.set_rotation(db.connect(data), target, 90, "manual", remake=False)
    scanner.remake_later([target])
    for worker in [t for t in threading.enumerate() if t.name == "remake-thumbnails"]:
        worker.join(10)
    row = db.connect(data).execute("SELECT thumb, rotation FROM assets WHERE id = ?",
                                   (target,)).fetchone()
    assert (row["thumb"], row["rotation"]) == (db.THUMB_OK, 180)
    assert size_of(admin.get(f"/api/thumb/{target}?s=256").data) == (256, 192)
