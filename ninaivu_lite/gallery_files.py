"""The family gallery's files: thumbnails, the original, the viewing copy
made for a browser or a guest, a video without its metadata, and downloads,
one at a time or as a zip.

Every item is looked up through :func:`common.visible_asset`, so a file is
served only to someone who may see it; an item they may not is a 404.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import sqlite3
import threading
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, request, send_file

from . import auth, db, media, parallel
from .api_gallery import IN_CHUNK, as_id, visible_ids
from .common import (
    ApiError,
    asset_path,
    cfg,
    conn,
    fail,
    motion_of,
    require_family,
    scanner,
    snap_thumb,
    user,
    visible_asset,
)
from .dates import long_path

# Logged under the gallery's name, as these lines always were.
log = logging.getLogger("ninaivu_lite.api_gallery")

bp = Blueprint("gallery_files", __name__)

MAX_ZIP_FILES = 2000

#: Types a browser may be handed to show; anything else is a download.
INLINE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "image/tiff", "image/heic", "image/heif", "image/avif",
    "video/mp4", "video/quicktime", "video/x-m4v", "video/webm",
})
#: Picture formats that cannot carry where they were taken.
NO_LOCATION_EXTS = frozenset({"gif", "bmp"})

#: Videos copied without their metadata at once. ffmpeg runs inside the
#: request, for guests and links too, and the server has sixteen threads in all.
FFMPEG_SLOTS = threading.BoundedSemaphore(2)
#: How long a second request for the same video waits for the first one's copy.
STRIP_WAIT = 30.0
#: The most the copies in views/ may take; past it the oldest go first.
VIEWS_MAX_BYTES = 2 * 1024 ** 3
VIEWS_CHECK_EVERY = 60.0
#: In every viewing copy's name: an older copy carried its source's JPEG
#: comment (A145), so those are never served again, only made anew (the old
#: file goes when its replacement is written, or when views/ is trimmed).
VIEWS_FORMAT = "v2"
_strip_locks: dict[int, threading.Lock] = {}
#: Thumbnails made while a phone waits for them, and viewing copies: a few at
#: a time (see :class:`parallel.Slots`), so they never hold every one of the
#: server's threads. A tile past the waiting room is told to ask again (the
#: grid does, once, a moment later); a photograph opened in the viewer waits.
TILES = parallel.Slots(parallel.REQUEST_WORK, parallel.REQUEST_WAITING)
VIEWS = parallel.Slots(parallel.REQUEST_WORK, parallel.SERVER_THREADS // 2, wait_for=60.0)
#: Seconds a tile turned away is asked for again after (said in Retry-After).
TILE_RETRY = 2
_views_lock = threading.Lock()
_views_checked_at = 0.0

for _ext, _mime in ((".heic", "image/heic"), (".heif", "image/heif"), (".avif", "image/avif"),
                    (".webp", "image/webp"), (".m4v", "video/x-m4v"), (".mov", "video/quicktime"),
                    (".webm", "video/webm"), (".jfif", "image/jpeg")):
    mimetypes.add_type(_mime, _ext)


# --- thumbnails and files ------------------------------------------------------------------------


def thumb_response(row: sqlite3.Row, requested: str | None, *, cache: str) -> Response:
    """The thumbnail for *row* at the configured size nearest *requested*,
    made now if it is still missing; 404 when there is none."""
    if row["thumb"] == db.THUMB_NONE:
        fail(404, "This item has no thumbnail.")
    size = snap_thumb(requested)
    thumbs = scanner().thumbs_dir
    # A file on disk is served only when the index says it is this row's:
    # ids are handed out again, and a removed photograph's thumbnail (a
    # Hidden one, say) stays behind until the new one is made over it.
    try:
        made = scanner().thumbnail_now(conn(), row["id"], size, slots=TILES)
        if not made and size != "s":
            size = "s"
            made = scanner().thumbnail_now(conn(), row["id"], size, slots=TILES)
    except parallel.Busy:
        response = ApiError(503, "Busy making other thumbnails. Try again in a moment.") \
            .get_response()
        response.headers["Retry-After"] = str(TILE_RETRY)
        return response
    path = media.thumb_path(thumbs, row["id"], size)
    if not made or not path.is_file():
        fail(404, "This item has no thumbnail.")
    stamp = path.stat().st_mtime_ns
    response = send_file(path, mimetype="image/webp", conditional=True,
                         etag=f"{stamp}-{path.stem}", max_age=3600)
    response.headers["Cache-Control"] = cache
    return response


@bp.get("/api/thumb/<int:asset_id>")
def thumb(asset_id: int):
    return thumb_response(visible_asset(asset_id), request.args.get("s"),
                          cache="private, max-age=31536000, immutable")


def may_carry_location(row: sqlite3.Row) -> bool:
    return row["kind"] == "picture" and (row["ext"] or "").lower() not in NO_LOCATION_EXTS


def original_path(row: sqlite3.Row) -> str:
    path = asset_path(row)
    if not os.path.isfile(long_path(path)):
        fail(404, "This file is not available right now.")
    return path


def views_dir() -> Path:
    return Path(cfg().data_dir) / "views"


def trim_views(keep: Path) -> None:
    """Keep views/ under :data:`VIEWS_MAX_BYTES`, oldest copies out first
    (never *keep*, the one just made). Looked at once a minute at most."""
    global _views_checked_at
    with _views_lock:
        now = time.monotonic()
        if _views_checked_at and now - _views_checked_at < VIEWS_CHECK_EVERY:
            return
        _views_checked_at = now
    found, total = [], 0
    for item in views_dir().glob("*/*"):
        try:
            st = item.stat()
        except OSError:
            continue
        found.append((st.st_mtime, st.st_size, item))
        total += st.st_size
    if total <= VIEWS_MAX_BYTES:
        return
    for _mtime, size, item in sorted(found):
        if total <= VIEWS_MAX_BYTES * 0.8:
            break
        if item == keep:
            continue
        try:
            item.unlink()
        except OSError:      # being sent right now (Windows): next time
            continue
        total -= size


def viewing_response(row: sqlite3.Row, path: str, max_age: int, turned: bool = False) -> Response:
    """The picture re-encoded for viewing: upright, at most 2560 px, and with
    no metadata at all. Kept on disk in the data folder, keyed by the file's
    size and time, so a second look costs nothing. *turned* bakes in the
    index's own quarter turn (faces, or a hand): for a copy that leaves the
    viewer, which otherwise turns the picture itself as it shows it."""
    try:
        st = os.stat(long_path(path))
    except OSError:
        fail(404, "This file is not available right now.")
    rotation = (row["rotation"] or 0) if turned else 0
    mirror = bool(row["mirror"]) and turned
    stem = f"{row['id']}-{st.st_size}-{int(st.st_mtime)}"
    cache = views_dir() / f"{row['id'] % 256:02x}" / \
        f"{stem}{f'-t{rotation}' if rotation else ''}{'-m' if mirror else ''}-{VIEWS_FORMAT}.jpg"
    # The copy as the file is (the family's viewer turns it itself) and the
    # turned one (guests, share links) are both kept: each used to remove
    # the other, so a turned HEIC was converted again on every other look.
    now = row["rotation"] or 0
    keep = {cache.with_name(f"{stem}-{VIEWS_FORMAT}.jpg"),
            cache.with_name(f"{stem}{f'-t{now}' if now else ''}"
                            f"{'-m' if row['mirror'] else ''}-{VIEWS_FORMAT}.jpg")}
    if not cache.is_file():
        try:
            # A full photograph decoded and shrunk: a core's worth, so taken
            # in turn with the thumbnails being made for others.
            with VIEWS.slot():
                data = media.viewing_copy(path, rotation=rotation, mirror=mirror)
        except parallel.Busy:
            fail(503, "Busy preparing other photographs. Try again in a moment.")
        except Exception as exc:  # noqa: BLE001 — damaged or unsupported: say so
            log.debug("no viewing copy for %s: %s", path, exc)
            fail(415, "This photograph could not be converted for the browser.")
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            for old in cache.parent.glob(f"{row['id']}-*.jpg"):
                if old not in keep:
                    old.unlink(missing_ok=True)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}-{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, cache)
            trim_views(cache)
        except OSError:
            response = Response(data, mimetype="image/jpeg")
            response.headers["Cache-Control"] = f"private, max-age={max_age}"
            return response
    response = send_file(cache, mimetype="image/jpeg", conditional=True,
                         etag=cache.stem, max_age=max_age)
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def original_response(row: sqlite3.Row, path: str, max_age: int = 3600) -> Response:
    """The file as it is, with Range for seeking; inline only for types a
    browser shows, otherwise a download."""
    mime, _ = mimetypes.guess_type(row["name"])
    inline = mime in INLINE_TYPES
    response = send_file(long_path(path), mimetype=mime if inline else "application/octet-stream",
                         conditional=True, max_age=max_age, etag=True)
    if not inline:
        response.headers["Content-Disposition"] = attachment_header(row["name"])
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def stripped_video_response(row: sqlite3.Row, path: str, max_age: int) -> Response:
    """A video with its metadata removed (a phone writes where it was shot
    into the file), kept in the data folder like the viewing copies. Without
    ffmpeg, or for a file it cannot remux, the video is refused: the person
    asking is a guest or a stranger with a link, and the original may say
    where it was shot. Only an administrator's choice (``video_originals``)
    sends such a video as it is, marked so in a header."""
    try:
        st = os.stat(long_path(path))
    except OSError:
        fail(404, "This file is not available right now.")
    ext = os.path.splitext(row["name"])[1].lower() or ".mp4"
    cache = views_dir() / f"{row['id'] % 256:02x}" / f"{row['id']}-{st.st_size}-{int(st.st_mtime)}{ext}"
    if not cache.is_file():
        # One copy of a video at a time (a player asks several times over),
        # and only a few videos at once, so guests cannot use up the threads.
        lock = _strip_locks.setdefault(row["id"], threading.Lock())
        if not lock.acquire(timeout=STRIP_WAIT):
            fail(503, "Videos are being prepared for others. Try again in a moment.")
        try:
            if not cache.is_file():
                if not FFMPEG_SLOTS.acquire(blocking=False):
                    fail(503, "Videos are being prepared for others. Try again in a moment.")
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    for old in cache.parent.glob(f"{row['id']}-*{ext}"):
                        try:
                            old.unlink(missing_ok=True)
                        except OSError:      # still being sent (Windows)
                            pass
                    stripped = media.strip_video(path, str(cache))
                finally:
                    FFMPEG_SLOTS.release()
                if not stripped:
                    if not cfg().video_originals:
                        fail(415, "This video cannot be shared without its location data.")
                    response = original_response(row, path, max_age)
                    response.headers["X-Ninaivu-Metadata"] = "original"
                    return response
                trim_views(cache)
        finally:
            lock.release()
    mime, _ = mimetypes.guess_type(row["name"])
    response = send_file(cache, mimetype=mime or "video/mp4", conditional=True,
                         etag=cache.stem, max_age=max_age)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    return response


def guarded_file(row: sqlite3.Row, who: auth.User) -> Response:
    path = original_path(row)
    if who.is_guest and may_carry_location(row):
        # A guest gets the gallery's view of a photograph, never its EXIF:
        # the original's bytes say where it was taken.
        return viewing_response(row, path, 3600, turned=True)
    if who.is_guest and row["kind"] == "video":
        return stripped_video_response(row, path, 3600)
    return original_response(row, path)


@bp.get("/api/file/<int:asset_id>")
def file(asset_id: int):
    # A Live Photo's short video is not in the grid, but is played from its
    # still: sent to whoever may see that still, and nobody else.
    return guarded_file(visible_asset(asset_id, motion=True), user())


@bp.get("/api/preview/<int:asset_id>")
def preview(asset_id: int):
    row = visible_asset(asset_id)
    if row["kind"] != "picture":
        fail(404, "Not found.")
    if media.browser_native(row["name"]):
        return guarded_file(row, user())
    # A guest is told rotation 0 (asset_public), so the turn is baked in here.
    return viewing_response(row, original_path(row), 86400, turned=user().is_guest)


def attachment_header(name: str, fallback: str = "photo") -> str:
    """``Content-Disposition: attachment`` naming *name*, whatever script it
    is in: ``filename*`` (RFC 5987) carries it, ``filename`` an ASCII stand-in."""
    name = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in name)
    try:
        name.encode("ascii")
        simple = name
    except UnicodeEncodeError:
        simple = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        stem, dot, ext = simple.rpartition(".")
        if not (stem if dot else simple).strip(" ._-"):
            simple = f"{fallback}.{ext}" if dot and ext else fallback
    simple = simple.replace("\\", "_").replace('"', "'")
    value = f'attachment; filename="{simple}"'
    if simple != name:
        value += "; filename*=UTF-8''" + quote(name, safe="")
    return value


@bp.get("/api/download/<int:asset_id>")
def download(asset_id: int):
    require_family()
    row = visible_asset(asset_id)
    path = original_path(row)
    mime, _ = mimetypes.guess_type(row["name"])
    response = send_file(long_path(path), mimetype=mime or "application/octet-stream",
                         conditional=True, max_age=0, etag=True)
    response.headers["Content-Disposition"] = attachment_header(row["name"])
    response.headers["Cache-Control"] = "private, no-cache"
    return response


class _Pipe(io.RawIOBase):
    """A write-only, unseekable stream whose bytes are taken as they come."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[bytes] = []
        self._pos = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, data) -> int:  # type: ignore[override]
        chunk = bytes(data)
        self._chunks.append(chunk)
        self._pos += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self._pos

    def take(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


#: Characters a name in a ZIP must not carry: a file named "..\\..\\x.jpg" (one
#: name on Linux or a NAS) is a path on Windows, and some unzippers there
#: wrote it outside the folder it was unzipped into.
_NOT_IN_ZIP = str.maketrans({c: "_" for c in '<>:"\\|?*/' + "".join(map(chr, range(32)))})


def zip_name(name: str) -> str:
    """*name* as a ZIP entry: one file, never a folder or another drive."""
    name = name.translate(_NOT_IN_ZIP)
    return "_" if not name.strip(" .") else name


def unique_name(taken: set[str], name: str) -> str:
    """*name*, or "name (2).ext" and so on when the archive already has it."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    candidate, n = name, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
    taken.add(candidate.lower())
    return candidate


def zip_stream(entries: list[tuple[str, str]], missing: list[str],
               taken: set[str]) -> Iterator[bytes]:
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, path in entries:
            try:
                handle = open(long_path(path), "rb")  # noqa: SIM115
            except OSError:
                missing.append(name)
                continue
            with handle:
                st = os.fstat(handle.fileno())
                when = datetime.fromtimestamp(max(st.st_mtime, 315576000)).timetuple()[:6]
                info = zipfile.ZipInfo(name, date_time=when)
                info.compress_type = zipfile.ZIP_STORED
                try:
                    with archive.open(info, "w", force_zip64=True) as out:
                        while chunk := handle.read(1 << 20):
                            out.write(chunk)
                            yield pipe.take()
                except OSError as exc:
                    log.warning("zip: could not read %s: %s", path, exc)
                    missing.append(name)
            yield pipe.take()
        if missing:
            note = ("These files could not be read when the download was made, "
                    "so they are not in it:\n\n" + "\n".join(missing) + "\n")
            archive.writestr(unique_name(taken, "NOT INCLUDED.txt"), note.encode("utf-8"))
    yield pipe.take()


@bp.get("/api/download/zip")
def download_zip():
    require_family()
    wanted: dict[int, None] = {}
    for part in (request.args.get("ids") or "").split(","):
        number = as_id(part)
        if number is not None:
            wanted[number] = None
    ids = list(wanted)[:MAX_ZIP_FILES]
    if not ids:
        fail(400, "Choose some photographs first.")
    seen = visible_ids(ids)
    if not seen:
        fail(404, "None of those are yours to download.")
    rows = {}
    for start in range(0, len(seen), IN_CHUNK):
        piece = seen[start:start + IN_CHUNK]
        for row in conn().execute(
                f"SELECT a.id, a.folder_id, a.dir, a.name, a.live_id, f.path AS root FROM assets a "
                f"JOIN folders f ON f.id = a.folder_id WHERE a.id IN "
                f"({','.join('?' * len(piece))})", piece):
            rows[row["id"]] = row
    # Every path is settled before the first byte goes out: after that the
    # status is sent, and a problem can only be written into the archive.
    taken: set[str] = set()
    entries: list[tuple[str, str]] = []
    missing: list[str] = []
    for asset_id in seen:
        # A Live Photo goes with its short video, as the phone made it.
        motion = motion_of(rows[asset_id]) if rows[asset_id]["live_id"] else None
        for row in (rows[asset_id], motion):
            if row is None:
                continue
            name = unique_name(taken, zip_name(row["name"]))
            try:
                path = asset_path(row)
            except ApiError:
                missing.append(name)
                continue
            if os.path.isfile(long_path(path)):
                entries.append((name, path))
            else:
                missing.append(name)
    if not entries:
        fail(404, "None of those files can be read right now.")
    if len(seen) == 1:
        filename = f"{entries[0][0].rpartition('.')[0] or entries[0][0]}.zip"
    else:
        filename = f"ninaivu-{date.today().isoformat()}.zip"
    response = Response(zip_stream(entries, missing, taken), mimetype="application/zip",
                        direct_passthrough=True)
    response.headers["Content-Disposition"] = attachment_header(filename, "ninaivu")
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-store"
    return response
