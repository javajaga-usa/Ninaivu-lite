"""Smaller answers over the wire: gzip where it pays, a year's cache for
static files whose address carries their hash, pictures left alone."""

from __future__ import annotations

import gzip
from pathlib import Path



def test_text_is_gzipped_when_asked_and_not_otherwise(app):
    c = app.test_client()
    plain = c.get("/static/js/app.js")
    assert plain.status_code == 200 and "Content-Encoding" not in plain.headers
    packed = c.get("/static/js/app.js", headers={"Accept-Encoding": "gzip, br"})
    assert packed.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in packed.headers.get("Vary", "")
    assert gzip.decompress(packed.data) == plain.data
    assert len(packed.data) < len(plain.data) // 2
    assert packed.headers["ETag"] != plain.headers["ETag"]
    # The page too, and the Tamil strings.
    page = c.get("/", headers={"Accept-Encoding": "gzip"})
    assert page.headers.get("Content-Encoding") == "gzip"
    assert b"<html" in gzip.decompress(page.data)
    ta = c.get("/static/i18n/ta.json", headers={"Accept-Encoding": "gzip"})
    assert ta.headers.get("Content-Encoding") == "gzip" and len(ta.data) < 200_000


def test_versioned_static_files_are_cached_for_a_year(app):
    c = app.test_client()
    versioned = c.get("/static/css/style.css?v=abc123")
    assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    bare = c.get("/static/css/style.css")
    assert "immutable" not in bare.headers.get("Cache-Control", "")


def test_small_answers_and_pictures_are_left_alone(app, admin):
    from conftest import ids
    small = admin.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in small.headers
    thumb = admin.get(f"/api/thumb/{ids(app)['beach.jpg']}", headers={"Accept-Encoding": "gzip"})
    assert thumb.status_code == 200 and "Content-Encoding" not in thumb.headers
    assert thumb.mimetype == "image/webp"


def test_the_english_locale_is_an_identity_map():
    """So the browser need not fetch it (i18n.js skips 'en')."""
    import json
    en = json.loads((Path("ninaivu_lite/static/i18n/en.json")).read_text(encoding="utf-8"))
    assert en and all(key == value for key, value in en.items())


def test_oversized_bodies_are_refused_before_they_are_read(app, admin):
    """A JSON body past a megabyte, and any body past the app's ceiling."""
    from ninaivu_lite.app import MAX_REQUEST_BYTES
    assert app.config["MAX_CONTENT_LENGTH"] == MAX_REQUEST_BYTES
    big = '{"name": "' + "x" * (1024 * 1024 + 10) + '"}'
    r = admin.post("/api/me", data=big, content_type="application/json")
    assert r.status_code == 413
    assert r.get_json()["error"] == "That is too large."
    # The ceiling, by the declared length alone: refused before reading
    # (the test client rewrites a Content-Length header, so the environ
    # carries the claim instead).
    r = admin.post("/api/me", data="{}", content_type="application/json",
                   environ_overrides={"CONTENT_LENGTH": str(MAX_REQUEST_BYTES + 1)})
    assert r.status_code == 413


def test_a_gzipped_answer_still_earns_a_304(app):
    c = app.test_client()
    first = c.get("/static/i18n/ta.json", headers={"Accept-Encoding": "gzip"})
    assert first.status_code == 200 and first.headers["ETag"].endswith('-gz"')
    again = c.get("/static/i18n/ta.json", headers={"Accept-Encoding": "gzip",
                                                   "If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
