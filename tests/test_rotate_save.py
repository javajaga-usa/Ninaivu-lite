"""Rotate and Save in the photo viewer: the turn is kept in the index (never in
the file), and every place the photograph is drawn shows it the new way up
without the browser's cached copy standing in for it."""

from __future__ import annotations

import io
import re
from pathlib import Path

from conftest import ids
from PIL import Image

from ninaivu_lite import db
from test_gallery_api import set_vis
from test_share_api import share

STATIC = Path(__file__).resolve().parent.parent / "ninaivu_lite"


def size_of(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_saved_turn_leaves_the_file_alone_and_turns_the_thumbnails(app, admin, library):
    root, _ = library
    target = ids(app)["beach.jpg"]
    before = (root / "2019" / "beach.jpg").read_bytes()
    first = admin.get(f"/api/asset/{target}").json
    assert size_of(admin.get(f"/api/thumb/{target}?s=256").data) == (256, 192)

    r = admin.post(f"/api/asset/{target}/rotate", json={"rotation": 270})
    assert r.status_code == 200
    item = r.json
    assert (item["rotation"], item["rotation_source"]) == (270, "manual")
    assert (item["width"], item["height"]) == (480, 640)
    # A new thumbnail version, so the year-long cached one is not used again.
    assert item["thumb_v"] and item["thumb_v"] != first["thumb_v"]
    assert size_of(admin.get(f"/api/thumb/{target}?s=256&v={item['thumb_v']}").data) == (192, 256)
    assert (root / "2019" / "beach.jpg").read_bytes() == before

    # Turning back to where it started is a save too.
    back = admin.post(f"/api/asset/{target}/rotate", json={"rotation": 0}).json
    assert (back["rotation"], back["width"], back["height"]) == (0, 640, 480)
    assert size_of(admin.get(f"/api/thumb/{target}?s=256").data) == (256, 192)


def test_a_guest_and_a_share_link_get_new_addresses_for_a_turned_photo(app, admin, guest, family):
    i = ids(app)
    target = i["beach.jpg"]
    set_vis(app, ["beach.jpg"], db.VIS_PUBLIC)
    plain = guest.get(f"/api/asset/{target}").json
    assert plain["src"] == f"/api/file/{target}" and plain["rotation"] == 0
    token = share(family, "asset", target)
    stranger = app.test_client()
    old = stranger.get(f"/api/share/{token}").json["item"]

    assert admin.post(f"/api/asset/{target}/rotate", json={"rotation": 90}).status_code == 200

    # The guest is sent a copy with the turn baked in, at an address of its own.
    turned = guest.get(f"/api/asset/{target}").json
    assert turned["rotation"] == 0
    assert turned["src"] == f"/api/file/{target}?r=90" == turned["view"]
    assert size_of(guest.get(turned["src"]).data) == (480, 640)

    new = stranger.get(f"/api/share/{token}").json["item"]
    assert new["thumb"] != old["thumb"] and new["src"] != old["src"]
    assert new["src"].endswith("?r=90") and new["rotation"] == 0
    assert size_of(stranger.get(new["src"]).data) == (480, 640)
    assert size_of(stranger.get(new["thumb"]).data)[1] > size_of(stranger.get(new["thumb"]).data)[0]


def test_the_viewer_turns_first_and_keeps_the_turn_only_on_save():
    html = (STATIC / "templates" / "index.html").read_text(encoding="utf-8")
    for element in ("rotate-bar", "v-rotate-left", "v-rotate-right", "rotate-note",
                    "v-rotate-cancel", "v-rotate-save"):
        assert f'id="{element}"' in html
    viewer = (STATIC / "static" / "js" / "viewer.js").read_text(encoding="utf-8")
    rotate = viewer[viewer.index("  rotate(direction = 1) {"):viewer.index("  renderRotateBar() {")]
    assert "api.rotate" not in rotate                       # turning alone saves nothing
    save = viewer[viewer.index("  async saveRotation() {"):viewer.index("  cancelRotation() {")]
    assert "api.rotate(item.id, rotation)" in save
    assert "this.renderFilmstrip()" in save                 # the strip shows the new thumbnail
    assert "event.shiftKey ? -1 : 1" in viewer              # R one way, Shift+R the other
    assert re.search(r"case 'enter':\s+if \(!this\.rotating\) return false;", viewer)
    # Leaving with a turn not saved says so.
    assert "this.endRotation();" in viewer[viewer.index("  close() {"):]
    grid = (STATIC / "static" / "js" / "grid.js").read_text(encoding="utf-8")
    assert "versions.set(cell.id, cell.v || 0)" in grid


def test_the_rotate_bar_makes_room_on_phones():
    css = (STATIC / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert ".viewer.rotating .viewer-stage" in css
    phone = css[css.index("/* Rotate: turn left or right"):]
    assert "@media (max-width: 620px)" in phone and "@media (max-height: 440px)" in phone
