"""Sudar's one call to the server: saving the edited copy.

The studio itself runs in the browser (static/js/sudar). When an
administrator presses Save, the finished picture arrives here and is written
beside its original as a new file; the original is never changed, and the
copy keeps the camera, exposure and place from the original's EXIF, so it
sits in the timeline where its original does. Taken from Ninaivu's
``save_edited_copy``, without the review queue: in Lite only an
administrator saves into the photo folders, and everyone else downloads.
"""

from __future__ import annotations

import io
import logging
import os
import secrets
from typing import Any

from flask import Blueprint, Response, jsonify, request
from PIL import Image, ImageOps

from . import dates
from .common import asset_path, asset_public, conn, fail, require_admin, scanner, visible_asset
from .dates import long_path

log = logging.getLogger(__name__)

bp = Blueprint("api_sudar", __name__)

#: mimetype -> (Pillow format, extension)
FORMATS = {"image/png": ("PNG", "png"), "image/jpeg": ("JPEG", "jpg"), "image/webp": ("WEBP", "webp")}
MAX_BYTES = 100 * 1024 * 1024
MAX_PIXELS = 24_000_000

#: IFD0 tags the copy keeps: who took it, with what, and who owns it.
_CARRIED_IFD0 = (0x010F, 0x0110, 0x013B, 0x8298, 0x0132)
#: Exif IFD tags describing the moment and the exposure; never the maker's
#: private notes, pixel sizes or serial numbers.
_CARRIED_SHOT = frozenset((
    0x9003, 0x9004, 0x9010, 0x9011, 0x9012, 0x9290, 0x9291, 0x9292,
    0x829A, 0x829D, 0x8822, 0x8827, 0x8830, 0x8832,
    0x9201, 0x9202, 0x9204, 0x9205, 0x9206, 0x9207, 0x9208, 0x9209,
    0x920A, 0xA405, 0xA402, 0xA403, 0xA404, 0xA406, 0xA432, 0xA433, 0xA434,
))
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825


def carried_exif(source_path: str, captured_at: float | None) -> Image.Exif:
    """The EXIF the copy is saved with: its original's, minus the pixels.
    Anything wrong with the original leaves the copy with just its date."""
    exif = Image.Exif()
    exif[0x0112] = 1                          # Orientation: already upright
    exif[0x0131] = "Ninaivu Lite Sudar"       # Software
    shot: dict[int, Any] = {}
    try:
        with Image.open(long_path(source_path)) as img:
            found = img.getexif()
            carried = {tag: found[tag] for tag in _CARRIED_IFD0 if tag in found}
            shot = {tag: value for tag, value in found.get_ifd(_EXIF_IFD).items()
                    if tag in _CARRIED_SHOT}
            gps = dict(found.get_ifd(_GPS_IFD))
        candidate = Image.Exif()
        candidate.update(exif)
        candidate.update(carried)
        candidate.get_ifd(_EXIF_IFD).update(shot)
        candidate.get_ifd(_GPS_IFD).update(gps)
        candidate.tobytes()                   # a tag Pillow cannot write fails here, not later
        exif = candidate
    except Exception:  # noqa: BLE001
        log.debug("not carrying EXIF from %s", source_path, exc_info=True)
        shot = {}
    if captured_at:
        when = dates.from_timestamp(captured_at).strftime("%Y:%m:%d %H:%M:%S")
        ifd = exif.get_ifd(_EXIF_IFD)
        if shot.get(0x9003) != when:
            ifd.pop(0x9011, None)
            ifd.pop(0x9291, None)
        ifd[0x9003] = when
    return exif


@bp.post("/api/asset/<int:asset_id>/edited-copy")
def save_edited_copy(asset_id: int):
    """Write the edited picture beside its original, as a new file."""
    require_admin()
    row = visible_asset(asset_id)
    if row["kind"] != "picture":
        fail(400, "Choose a photograph from the library.")
    source = asset_path(row)
    if not os.path.isfile(long_path(source)):
        fail(404, "This file is not available right now.")
    if request.mimetype not in FORMATS:
        fail(400, "The edited copy must be a JPEG, PNG or WebP image.")
    fmt, ext = FORMATS[request.mimetype]
    if request.content_length and request.content_length > MAX_BYTES:
        fail(413, "That is too large.")
    payload = request.stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        fail(413, "That is too large.")
    try:
        with Image.open(io.BytesIO(payload)) as uploaded:
            if uploaded.format != fmt or uploaded.width * uploaded.height > MAX_PIXELS:
                raise ValueError()
            uploaded.verify()
        with Image.open(io.BytesIO(payload)) as uploaded:
            has_alpha = "A" in uploaded.getbands() or "transparency" in uploaded.info
            edited = uploaded.convert("RGBA" if has_alpha and fmt != "JPEG" else "RGB")
    except (ValueError, OSError, Image.DecompressionBombError):
        fail(400, f"Use a valid {fmt.title() if fmt == 'WEBP' else fmt} image of up to 24 megapixels.")
    save_args = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}

    stem = os.path.splitext(row["name"])[0][:100]
    name = f"{stem}-edited-{secrets.token_hex(6)}.{ext}"
    folder = os.path.dirname(source)
    output = os.path.join(folder, name)
    temporary = os.path.join(folder, f".{name}.tmp")
    exif = carried_exif(source, row["captured_at"])
    try:
        with open(long_path(temporary), "xb") as stream:
            edited.save(stream, fmt, exif=exif, **save_args)
        # A hard link publishes atomically and refuses an existing name.
        try:
            os.link(long_path(temporary), long_path(output))
            os.unlink(long_path(temporary))
        except OSError:
            os.replace(long_path(temporary), long_path(output))
    except OSError as exc:
        try:
            os.unlink(long_path(temporary))
        except OSError:
            pass
        log.warning("could not save the edited copy of %s: %s", source, exc)
        fail(500, "Could not save the copy. Check that the photo folder can be written "
                  "and has room.")
    c = conn()
    new_id = scanner().add_file(c, row["folder_id"], row["root"], row["dir"], name,
                                row["visibility"] if row["vis_source"] == "item" else None)
    saved = c.execute("SELECT a.*, f.path AS root FROM assets a JOIN folders f ON f.id = a.folder_id "
                      "WHERE a.id = ?", (new_id,)).fetchone()
    return jsonify(asset_public(saved)), 201


@bp.get("/api/asset/<int:asset_id>/edit-source")
def edit_source(asset_id: int):
    """A HEIC or TIFF at full size, as a JPEG the browser can open, for Sudar.

    The viewer's copy of such a photograph is at most 2560 px, and a copy saved
    from it was smaller than its original. Upright from the camera's tag, like
    the viewing copy; the index's own turn is Sudar's to apply. No metadata:
    the saved copy gets the original's from ``carried_exif``."""
    require_admin()
    row = visible_asset(asset_id)
    if row["kind"] != "picture":
        fail(400, "Choose a photograph from the library.")
    source = asset_path(row)
    if not os.path.isfile(long_path(source)):
        fail(404, "This file is not available right now.")
    try:
        with Image.open(long_path(source)) as opened:
            img = ImageOps.exif_transpose(opened) or opened
            if img.width * img.height > MAX_PIXELS:
                scale = (MAX_PIXELS / (img.width * img.height)) ** 0.5
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                                 Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.convert("RGB").save(out, "JPEG", quality=95)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log.debug("no full-size source for %s: %s", source, exc)
        fail(415, "This photograph could not be converted for the browser.")
    response = Response(out.getvalue(), mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, no-store"
    return response
