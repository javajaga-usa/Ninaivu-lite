"""Smaller answers: gzip for text, and a long cache for files whose address
changes with their content.

The gallery's page, scripts, styles and the Tamil strings are a megabyte of
text; gzipped they are a fifth of it, and a phone on the far side of the
house feels the difference on every first load. Static files are compressed
once per version and kept in memory (there are a dozen, under a megabyte
each); API answers are compressed as they go, only when they are big enough
to be worth it. Pictures and videos are left alone: they are compressed
already.

Standard library only.
"""

from __future__ import annotations

import gzip
import threading

from flask import Flask, Response, request

#: What is worth compressing: text, and the JSON and SVG the gallery reads.
TEXT_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml",
              "application/manifest+json")
MIN_BYTES = 1024
#: Nothing larger than this is compressed on the fly: an original photograph
#: served as "text" by mistake would otherwise be read whole into memory.
MAX_BYTES = 4 * 1024 * 1024
STATIC_MAX_AGE = 365 * 24 * 60 * 60
_CACHE_LIMIT = 48

_static_cache: dict[str, tuple[str, bytes]] = {}
_lock = threading.Lock()


def _compressible(response: Response) -> bool:
    if response.status_code != 200 or response.is_streamed and not response.direct_passthrough:
        return False
    if "Content-Encoding" in response.headers or "gzip" not in request.headers.get(
            "Accept-Encoding", "").lower():
        return False
    mimetype = response.mimetype or ""
    if not any(mimetype.startswith(t) for t in TEXT_TYPES):
        return False
    length = response.content_length
    return length is None or MIN_BYTES <= length <= MAX_BYTES


def _gzip(data: bytes, level: int) -> bytes:
    return gzip.compress(data, compresslevel=level, mtime=0)


def _static_gzipped(key: str, etag: str, data: bytes) -> bytes:
    """A static file's gzip, made once per content (the ETag says which)."""
    with _lock:
        hit = _static_cache.get(key)
        if hit and hit[0] == etag:
            return hit[1]
    packed = _gzip(data, 9)
    with _lock:
        if len(_static_cache) >= _CACHE_LIMIT:
            _static_cache.pop(next(iter(_static_cache)))
        _static_cache[key] = (etag, packed)
    return packed


def shrink(response: Response) -> Response:
    """The after-request hook: gzip what is worth it, cache what is versioned."""
    static = request.path.startswith("/static/")
    if static and request.args.get("v"):
        # The address carries the file's hash (pages.asset), so the file at
        # that address never changes: it can be kept for a year.
        response.headers["Cache-Control"] = f"public, max-age={STATIC_MAX_AGE}, immutable"
    if not _compressible(response):
        return response
    response.direct_passthrough = False
    data = response.get_data()
    if not MIN_BYTES <= len(data) <= MAX_BYTES:
        return response
    etag = response.get_etag()[0] or ""
    packed = _static_gzipped(request.path, etag, data) if static else _gzip(data, 5)
    if len(packed) >= len(data):
        return response
    response.set_data(packed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers.add("Vary", "Accept-Encoding")
    if etag:
        response.set_etag(f"{etag}-gz")
    return response


def install(app: Flask) -> None:
    app.after_request(shrink)
