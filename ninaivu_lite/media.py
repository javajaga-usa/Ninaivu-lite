"""What a file is, what it says about itself, and its thumbnails.

Photos are read with Pillow; HEIC/HEIF only when ``pillow-heif`` is installed.
Videos are played by the browser as they are; a poster frame is made only when
``ffmpeg`` is on the PATH, and the gallery shows a plain video tile otherwise.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import dates

log = logging.getLogger(__name__)

#: Generous, so real panoramas still index, but a bomb is still refused.
Image.MAX_IMAGE_PIXELS = 300_000_000

try:  # optional: iPhone photos
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIF = True
except Exception:  # noqa: BLE001 — absent or broken, either way not available
    HEIF = False

try:  # optional: sideways photographs without a camera tag, judged by their faces
    import cv2  # type: ignore
    import numpy  # type: ignore

    # OpenCV 5 moved the Haar cascades out of the main package: 4.x is wanted.
    FACES = hasattr(cv2, "CascadeClassifier") and os.path.isfile(
        os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
except Exception:  # noqa: BLE001
    cv2 = numpy = None  # type: ignore
    FACES = False

PHOTO_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
              ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv", ".3gp", ".mts", ".m2ts",
              ".mpg", ".mpeg", ".wmv"}
#: What a current browser shows without help. Anything else is viewed through
#: its large thumbnail, or offered as a download.
BROWSER_PHOTO = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".avif"}
BROWSER_VIDEO = {".mp4", ".m4v", ".mov", ".webm"}

#: The two sizes Ninaivu's screens ask for: 256 for the grid, 640 for big
#: tiles on sharp screens and the viewer's stand-in.
THUMB_SIZES = {"s": 256, "l": 640}
THUMB_QUALITY = 80

FFMPEG = shutil.which("ffmpeg")

_ORIENTATION = 0x0112
_QUARTER_TURNS = {5, 6, 7, 8}
_TAGS = {0x010F: "Make", 0x0110: "Model", 0x0132: "DateTime",
         0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
         0x829A: "ExposureTime", 0x829D: "FNumber", 0x8827: "ISOSpeedRatings",
         0x920A: "FocalLength", 0xA434: "LensModel"}


def _number(value: Any) -> float | None:
    try:
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def kind_of(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    if ext in PHOTO_EXTS:
        return "picture"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def browser_native(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in BROWSER_PHOTO or ext in BROWSER_VIDEO


def _gps(value: Any) -> float | None:
    try:
        d, m, s = (float(v) for v in value)
        return d + m / 60.0 + s / 3600.0
    except Exception:  # noqa: BLE001
        return None


def read_photo(path: str) -> dict[str, Any]:
    """Size, camera, capture time and place from a photo's header.

    Only the header is read here, not the pixels, so this is quick even on a
    slow disk. Anything unreadable simply leaves its field out.
    """
    info: dict[str, Any] = {}
    with Image.open(dates.long_path(path)) as img:
        width, height = img.size
        try:
            exif = img.getexif()
        except Exception:  # noqa: BLE001
            exif = None
        if exif:
            raw = {_TAGS.get(tag, tag): val for tag, val in exif.items()}
            try:
                sub = exif.get_ifd(0x8769)
            except Exception:  # noqa: BLE001
                sub = {}
            for tag, val in (sub or {}).items():
                raw[_TAGS.get(tag, tag)] = val
            make = str(raw.get("Make") or "").strip("\x00 ")
            model = str(raw.get("Model") or "").strip("\x00 ")
            camera = model if make and model.startswith(make) else f"{make} {model}".strip()
            if camera:
                info["camera"] = camera[:80]
            taken = (dates.parse_exif_datetime(raw.get("DateTimeOriginal"))
                     or dates.parse_exif_datetime(raw.get("DateTimeDigitized"))
                     or dates.parse_exif_datetime(raw.get("DateTime")))
            if taken:
                info["taken_at"], info["taken_source"] = taken, "exif"
            lens = str(raw.get("LensModel") or "").strip("\x00 ")
            if lens:
                info["lens"] = lens[:80]
            iso = raw.get("ISOSpeedRatings")
            if isinstance(iso, (list, tuple)):
                iso = iso[0] if iso else None
            try:
                if iso:
                    info["iso"] = int(iso)
            except (TypeError, ValueError):
                pass
            if (fnum := _number(raw.get("FNumber"))) is not None:
                info["f_number"] = round(fnum, 1)
            if (focal := _number(raw.get("FocalLength"))) is not None:
                info["focal_length"] = round(focal, 1)
            if exposure := _number(raw.get("ExposureTime")):
                info["exposure"] = (f"1/{round(1 / exposure)}" if exposure < 1
                                    else f"{round(exposure, 1)}")
            orientation = exif.get(_ORIENTATION)
            if orientation in _QUARTER_TURNS:
                width, height = height, width
            if isinstance(orientation, int) and 1 <= orientation <= 8:
                info["orientation"] = orientation
            try:
                gps = exif.get_ifd(0x8825)
                lat, lon = _gps(gps.get(2)), _gps(gps.get(4))
                if lat is not None and lon is not None and (lat or lon):
                    if str(gps.get(1, "N")).upper().startswith("S"):
                        lat = -lat
                    if str(gps.get(3, "E")).upper().startswith("W"):
                        lon = -lon
                    info["lat"], info["lon"] = round(lat, 6), round(lon, 6)
            except Exception:  # noqa: BLE001
                pass
    info["width"], info["height"] = width, height
    return info


def read_video(path: str) -> dict[str, Any]:
    when = dates.container_date(path)
    return {"taken_at": dates.to_timestamp(when), "taken_source": "container"} if when else {}


def describe(path: str, name: str, kind: str, st: os.stat_result) -> dict[str, Any]:
    """Everything the index keeps about one file. Never raises for a bad file."""
    info: dict[str, Any] = {}
    try:
        info = read_photo(path) if kind == "picture" else read_video(path)
    except Exception as exc:  # noqa: BLE001 — a damaged file still gets a tile
        log.debug("could not read %s: %s", path, exc)
        info = {"error": "unreadable"} if kind == "picture" else {}
    if not info.get("taken_at"):
        # The same chain the importer files by: the video's header, a Google
        # Takeout sidecar, the file name, a dated folder, then the file's clock.
        when, source = dates.fallback_date(path, st)
        if when is None:
            when, source = dates.from_timestamp(dates.file_time(st)), "mtime"
        info["taken_at"] = dates.to_timestamp(when)
        info["taken_source"] = {"filesystem": "mtime", "folder": "path"}.get(source, source)
    if info.get("lat") is None and kind == "picture":
        # An export from Google Photos often keeps the place only in the sidecar.
        extras = dates.takeout_extras(path)
        if "lat" in extras:
            info["lat"], info["lon"] = extras["lat"], extras["lon"]
    return info


# --- which way up (taken from Ninaivu's media/upright.py, the faces part) ------------

#: A clockwise quarter turn, 0, 90, 180 or 270: never anything finer.
ROTATIONS = (0, 90, 180, 270)
_WORK_SIZE = 640
#: A detected box must be at least this much skin to count as a person: a
#: cascade fires on brickwork and car grilles too, and those measure near zero.
_SKIN_FRACTION = 0.25
#: How much of the frame the faces must take up before they are evidence, and
#: how clearly the best turn must beat the runner-up.
_MIN_FACE_EVIDENCE = 0.0035
_MIN_FACE_MARGIN = 1.6
_CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")
_cascades: list[Any] | None = None


def turn(img: Image.Image, rotation: int) -> Image.Image:
    """*img* turned clockwise by a quarter-turn multiple."""
    rotation %= 360
    return img if not rotation else img.rotate(-rotation, expand=True)


def _face_cascades() -> list[Any]:
    global _cascades
    if _cascades is None:
        found = []
        if FACES:
            try:
                cv2.setNumThreads(1)
            except Exception:  # noqa: BLE001
                pass
            for name in _CASCADES:
                classifier = cv2.CascadeClassifier(cv2.data.haarcascades + name)
                if not classifier.empty():
                    found.append(classifier)
        _cascades = found
    return _cascades


def _face_evidence(img: Image.Image) -> float:
    """How much face there is in *img*, weighted towards the top of the frame:
    heads are near the top of a photograph far more often than the bottom,
    which is what tells a photograph from the same one upside down."""
    rgb = numpy.array(img)
    grey = cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    width, height = img.size
    total = 0.0
    for classifier in _face_cascades():
        try:
            hits = classifier.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5,
                                               minSize=(40, 40))
        except Exception:  # noqa: BLE001
            continue
        for x, y, w, h in hits:
            crop = ycrcb[y:y + h, x:x + w]
            cr, cb = crop[:, :, 1], crop[:, :, 2]
            skin = ((cr >= 133) & (cr <= 180) & (cb >= 77) & (cb <= 130)).mean()
            if skin < _SKIN_FRACTION:
                continue
            centre = (y + h / 2) / height
            weight = 1.0 if centre <= 0.5 else max(0.4, 1.0 - (centre - 0.5) * 1.2)
            total += (w * h) / float(width * height) * weight
    return total


def detect_rotation(path: str) -> int:
    """The clockwise quarter turn that puts the people in this photograph the
    right way up, or 0 when there is no clear answer. Only for a photograph
    without a camera tag; turning one that was fine is the worse mistake, so
    every turn needs enough face, clearly ahead of the other three."""
    if not FACES or not _face_cascades():
        return 0
    try:
        with Image.open(dates.long_path(path)) as img:
            img.draft("RGB", (_WORK_SIZE, _WORK_SIZE))
            work = img.convert("RGB")
            work.thumbnail((_WORK_SIZE, _WORK_SIZE), Image.Resampling.BILINEAR)
    except Exception:  # noqa: BLE001
        return 0
    scores = {rotation: _face_evidence(turn(work, rotation)) for rotation in ROTATIONS}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < _MIN_FACE_EVIDENCE or (second > 0 and top / second < _MIN_FACE_MARGIN):
        return 0
    return best


# --- thumbnails ------------------------------------------------------------------

def thumb_path(thumbs_dir: Path, asset_id: int, size: str) -> Path:
    return thumbs_dir / f"{asset_id % 256:02x}" / f"{asset_id}_{size}.webp"


def _open_photo(path: str, edge: int) -> Image.Image:
    img = Image.open(dates.long_path(path))
    # A JPEG can be decoded at 1/2, 1/4 or 1/8 size directly: on a 24 MP photo
    # that is most of the work of making a thumbnail, skipped.
    img.draft("RGB", (edge, edge))
    img.load()
    img = ImageOps.exif_transpose(img) or img
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    return img


def video_frame(path: str) -> Image.Image | None:
    if not FFMPEG:
        return None
    for args in (["-ss", "1"], []):
        try:
            proc = subprocess.run(
                [FFMPEG, "-v", "quiet", *args, "-i", dates.long_path(path), "-frames:v", "1",
                 "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=45, check=False,
                # Started without a console (at sign-in, by pythonw), Windows
                # would otherwise flash a black window for every video.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode == 0 and proc.stdout:
                return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
        except (subprocess.SubprocessError, OSError, ValueError):
            return None
    return None


def make_thumbnails(path: str, kind: str, thumbs_dir: Path, asset_id: int,
                    sizes: tuple[str, ...] = ("s", "l"), rotation: int = 0) -> tuple[bool, str | None]:
    """Write the thumbnails named in *sizes* ("s" 256 px, "l" 640 px), turned
    by *rotation* (the index's answer for a photograph without a camera tag).

    Returns (made, colour): colour is the picture's average, '#rrggbb', which
    the grid paints while the thumbnail loads. ``made`` is False when no
    picture can be made (a video without ffmpeg, a damaged file); the gallery
    then shows a plain tile.
    """
    edge = max(THUMB_SIZES[s] for s in sizes)
    try:
        img = _open_photo(path, edge) if kind == "picture" else video_frame(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("no thumbnail for %s: %s", path, exc)
        return False, None
    if img is None:
        return False, None
    try:
        colour = save_thumbnails(turn(img, rotation) if kind == "picture" else img,
                                 thumbs_dir, asset_id, sizes)
        return True, colour
    except Exception as exc:  # noqa: BLE001
        log.debug("thumbnail failed for %s: %s", path, exc)
        return False, None
    finally:
        img.close()


def save_thumbnails(img: Image.Image, thumbs_dir: Path, asset_id: int,
                    sizes: tuple[str, ...] = ("s", "l")) -> str:
    """Write *img* as the thumbnails named in *sizes*; returns its average
    colour, '#rrggbb'. Also what a poster sent by a browser goes through."""
    edges = sorted(((s, THUMB_SIZES[s]) for s in sizes), key=lambda kv: -kv[1])
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    current = img
    for size, edge in edges:
        copy = current.copy()
        copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        out = thumb_path(thumbs_dir, asset_id, size)
        out.parent.mkdir(parents=True, exist_ok=True)
        # A name of this thread's own: the scanner and a request for a tile
        # on screen can be making the same thumbnail at the same moment, and
        # two writers of one temporary file tear it (and on Windows, fail).
        tmp = out.with_name(f"{out.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        # method 2: a third of the encoding time of the default, and no
        # difference anyone can see at these sizes.
        copy.save(tmp, "WEBP", quality=THUMB_QUALITY, method=2)
        os.replace(tmp, out)
        current = copy
    r, g, b = current.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return f"#{r:02x}{g:02x}{b:02x}"


def profile_picture(path: str, rotation: int = 0, size: int = 256) -> bytes:
    """The middle of a photograph as a small square JPEG, upright, no metadata:
    a profile picture for the sign-in screen."""
    with _open_photo(path, size * 2) as img:
        img = turn(img.convert("RGB"), rotation)
        img = ImageOps.fit(img, (size, size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue()


def viewing_copy(path: str, max_edge: int = 2560, rotation: int = 0) -> bytes:
    """A JPEG a browser can show, upright and without metadata: for HEIC and
    TIFF, which browsers cannot open, and for guests, who get no EXIF.
    *rotation* is the index's own quarter turn, on top of the camera's tag."""
    with _open_photo(path, max_edge) as img:
        img = turn(img.convert("RGB"), rotation)
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=86, optimize=True)
        return out.getvalue()
