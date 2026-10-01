"""Sudar (ninaivu_lite/api_sudar.py and static/js/sudar): the edited copy is
written beside its original, which is never changed, and the studio's own
arithmetic keeps its promises (tests/sudar.mjs, under Node)."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ninaivu_lite import db

from conftest import ids, make_jpeg

ROOT = Path(__file__).resolve().parents[1]


def png(size=(64, 48), mode="RGB", colour=(180, 120, 90)) -> bytes:
    stream = io.BytesIO()
    Image.new(mode, size, colour).save(stream, "PNG")
    return stream.getvalue()


def save(client, target: int, data: bytes | None = None, mimetype="image/png"):
    return client.post(f"/api/asset/{target}/edited-copy", data=data or png(),
                       content_type=mimetype)


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


def test_only_an_administrator_saves(app, admin, family, guest):
    target = ids(app)["beach.jpg"]
    assert save(app.test_client(), target).status_code == 401
    assert save(guest, target).status_code == 403
    assert save(family, target).status_code == 403
    assert admin.get("/api/auth/state").get_json()["user"]["can"]["save_edits"] is True
    assert family.get("/api/auth/state").get_json()["user"]["can"]["save_edits"] is False


def test_the_copy_sits_beside_an_unchanged_original(app, admin, library):
    root, _ = library
    target = ids(app)["beach.jpg"]
    original = root / "2019" / "beach.jpg"
    before = original.read_bytes()
    first, second = save(admin, target), save(admin, target)
    assert first.status_code == 201 and second.status_code == 201, (first.get_json(), second.get_json())
    assert first.get_json()["id"] != second.get_json()["id"] != target
    assert original.read_bytes() == before
    copy = first.get_json()
    assert copy["folder"] == "2019" and copy["name"].startswith("beach-edited-")
    assert copy["name"].endswith(".png") and copy["playable"]
    path = root / "2019" / copy["name"]
    with Image.open(path) as img:
        assert img.size == (64, 48)
        exif = img.getexif()
        assert exif[0x0112] == 1 and exif[0x0131] == "Ninaivu Lite Sudar"
        assert exif[0x010F] == "Canon" and exif[0x0110] == "Canon EOS 80D"
        assert exif.get_ifd(0x8769)[0x9003] == "2019:05:12 10:00:00"
    # In the index at once, on the same day as its original, with a thumbnail.
    assert copy["date"] == "2019-05-12" and copy["camera"] == "Canon EOS 80D"
    assert admin.get(f"/api/thumb/{copy['id']}").status_code == 200
    assert copy["id"] in [item[0] for seg in admin.get("/api/segments").get_json()["segments"]
                          for item in seg["items"]]
    assert not list((root / "2019").glob(".*.tmp"))


def test_the_copy_keeps_its_originals_visibility(app, admin, family, library):
    target = ids(app)["beach.jpg"]
    assert admin.post("/api/visibility", json={"ids": [target], "visibility": "hidden"}).status_code == 200
    copy = save(admin, target).get_json()
    assert copy["visibility"] == "hidden"
    assert family.get(f"/api/asset/{copy['id']}").status_code == 404
    # A folder-ruled original: the copy follows the folder's rule too.
    other = ids(app)["clip.mp4"]
    assert save(admin, other).status_code == 400          # videos are not edited
    assert admin.post("/api/visibility/folder", json={"folder": "family", "visibility": "public",
                                                     "confirm": True}).status_code == 200
    pongal = ids(app)["IMG_20230115_091500.jpg"]
    assert save(admin, pongal).get_json()["visibility"] == "public"


def test_png_keeps_transparency_and_jpeg_or_webp_are_accepted(app, admin, library):
    root, _ = library
    target = ids(app)["beach.jpg"]
    copy = save(admin, target, png(mode="RGBA", colour=(180, 120, 90, 37))).get_json()
    with Image.open(root / "2019" / copy["name"]) as img:
        assert img.convert("RGBA").getpixel((0, 0)) == (180, 120, 90, 37)
    for fmt, mimetype, ext in (("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp")):
        stream = io.BytesIO()
        Image.new("RGB", (40, 30), (1, 2, 3)).save(stream, fmt)
        r = save(admin, target, stream.getvalue(), mimetype)
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["name"].endswith("." + ext)


def test_bad_uploads_are_refused(app, admin, library):
    target = ids(app)["beach.jpg"]
    url = f"/api/asset/{target}/edited-copy"
    assert admin.post(url, data=b"bad", content_type="image/png").status_code == 400
    assert admin.post(url, data=png(), content_type="image/jpeg").status_code == 400
    assert admin.post(url, data=png(), content_type="image/gif").status_code == 400
    big = io.BytesIO()
    Image.new("RGB", (6000, 4001), (1, 2, 3)).save(big, "PNG")    # over 24 megapixels
    assert admin.post(url, data=big.getvalue(), content_type="image/png").status_code == 400
    assert save(admin, 999999).status_code == 404


def test_a_source_without_exif_or_unreadable_still_saves(app, admin, library):
    root, _ = library
    make_jpeg(root / "plain.jpg")                    # no EXIF at all
    app.config["SCANNER"].scan_once(conn_of(app))
    plain, broken = ids(app)["plain.jpg"], ids(app)["broken.jpg"]
    for target in (plain, broken):
        r = save(admin, target)
        assert r.status_code == 201, r.get_json()
        with Image.open(root / r.get_json()["name"]) as img:
            assert img.getexif()[0x0131] == "Ninaivu Lite Sudar"


def test_a_folder_that_cannot_be_written_says_so(app, admin, library, monkeypatch):
    import ninaivu_lite.api_sudar as api_sudar
    target = ids(app)["beach.jpg"]

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(api_sudar, "open", refuse, raising=False)
    r = save(admin, target)
    assert r.status_code == 500 and "written" in r.get_json()["error"]


def test_the_gallery_offers_sudar(app, admin):
    page = admin.get("/").get_data(as_text=True)
    assert 'id="v-sudar"' in page and "Edit with Sudar (AI)" in page
    assert admin.get("/static/js/sudar/sudar.js").status_code == 200
    assert admin.get("/static/css/sudar.css").status_code == 200


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_studio_keeps_its_promises_under_node():
    done = subprocess.run([shutil.which("node"), "--test", str(ROOT / "tests" / "sudar.mjs")],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
