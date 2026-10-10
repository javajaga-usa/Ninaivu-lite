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
import sys
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import dates, parallel

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
                # A damaged tag can hold a number no camera uses (and the index
                # cannot store): left out.
                if iso and 0 < int(iso) < 10_000_000:
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
    when = dates.container_date(path, zone_near(path))
    return {"taken_at": dates.to_timestamp(when), "taken_source": "container"} if when else {}


# --- the time zone a video was taken in ---------------------------------------------------
#
# A photograph's EXIF time is the clock where it was taken; a video's header
# (and a Google Takeout sidecar) keeps UTC. Turned into this computer's local
# time, a video shot in Chennai at 8 in the morning lands on the evening
# before for a computer in New York, a day away from the photos taken with
# it. Phones write the offset beside the EXIF time (OffsetTimeOriginal): the
# photo in the same folder nearest in time says which clocks the video's
# moment is read on.

#: A photo further away in time than this says nothing about a video's zone.
ZONE_REACH = timedelta(days=2)


def _photo_moment(path: str) -> tuple[datetime, timedelta] | None:
    """(the moment in UTC, naive, and the offset) from a photo's EXIF, when
    it has both the time it was taken and the offset of that clock."""
    try:
        with Image.open(dates.long_path(path)) as img:
            sub = img.getexif().get_ifd(0x8769)
            wall = dates.exif_wall_clock(sub.get(0x9003))
            offset = dates.parse_exif_offset(sub.get(0x9011))
    except Exception:  # noqa: BLE001 — not a readable picture
        return None
    if wall is None or offset is None:
        return None
    return wall - offset, offset


_ZONES = threading.Lock()


@lru_cache(maxsize=4)
def _folder_zones(folder: str, stamp: int) -> tuple[tuple[datetime, timedelta], ...]:
    """Every (moment, offset) the photos in a folder carry, in time order.
    Read once per folder (keyed on its modified time), and only for a folder
    with a video or Takeout file whose time is kept in UTC."""
    found = []
    try:
        with os.scandir(dates.long_path(folder)) as entries:
            names = [e.name for e in entries if kind_of(e.name) == "picture"]
    except OSError:
        return ()
    for name in names:
        moment = _photo_moment(os.path.join(folder, name))
        if moment:
            found.append(moment)
    return tuple(sorted(found))


def zone_near(path: str):
    """For :func:`dates.wall_clock`: given a UTC moment, the offset of the
    photo beside *path* taken nearest to it (within two days), or None."""
    folder = os.path.dirname(os.path.abspath(path))

    def zone(utc: datetime) -> timedelta | None:
        try:
            stamp = os.stat(dates.long_path(folder)).st_mtime_ns
        except OSError:
            return None
        # One at a time: headers are read several at once on a slow disk, and
        # each of a folder's videos would otherwise read its photos again.
        with _ZONES:
            moments = _folder_zones(folder, stamp)
        if not moments:
            return None
        when = utc.astimezone(timezone.utc).replace(tzinfo=None)
        moment, offset = min(moments, key=lambda m: abs(m[0] - when))
        return offset if abs(moment - when) <= ZONE_REACH else None

    return zone


def describe(path: str, name: str, kind: str, st: os.stat_result) -> dict[str, Any]:
    """Everything the index keeps about one file. Never raises for a bad file."""
    try:
        return _describe(path, name, kind, st)
    except Exception as exc:  # noqa: BLE001 — a damaged sidecar or tag still gets a tile
        log.info("could not describe %s: %s", path, exc)
        return {"taken_at": dates.file_time(st), "taken_source": "mtime"}


def _describe(path: str, name: str, kind: str, st: os.stat_result) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        info = read_photo(path) if kind == "picture" else read_video(path)
    except Exception as exc:  # noqa: BLE001 — a damaged file still gets a tile
        log.debug("could not read %s: %s", path, exc)
        info = {"error": "unreadable"} if kind == "picture" else {}
    if not info.get("taken_at"):
        # The same chain the importer files by: the video's header, a Google
        # Takeout sidecar, the file name, a dated folder, then the file's clock.
        when, source = dates.fallback_date(path, st, zone_near(path))
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
#: Each thread's own classifiers: the face check runs several at a time
#: (``scanner.THUMB_WORKERS``), and one OpenCV classifier is not safe to use
#: from two threads at once.
_cascades = threading.local()


def turn(img: Image.Image, rotation: int) -> Image.Image:
    """*img* turned clockwise by a quarter-turn multiple."""
    rotation %= 360
    return img if not rotation else img.rotate(-rotation, expand=True)


def _face_cascades() -> list[Any]:
    found = getattr(_cascades, "found", None)
    if found is None:
        found = []
        if FACES:
            try:
                # One core each: the scan already looks at several pictures
                # at once, and OpenCV's own threads on top would leave no
                # core for the gallery.
                cv2.setNumThreads(1)
            except Exception:  # noqa: BLE001
                pass
            for name in _CASCADES:
                classifier = cv2.CascadeClassifier(cv2.data.haarcascades + name)
                if not classifier.empty():
                    found.append(classifier)
        _cascades.found = found
    return found


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


#: A picture this many pixels or more, once opened at the size it will be
#: read at, is made one at a time. A JPEG is read at a quarter or an eighth of
#: its size and never comes near it, but a 50 MP PNG or TIFF is read whole
#: (150 MB), and three of those at once would not fit in a small computer.
HEAVY_PIXELS = 16_000_000
_HEAVY = threading.Lock()


def _draft(path: str, edge: int) -> Image.Image:
    """The photograph opened (its header only) to be read at about *edge*."""
    img = Image.open(dates.long_path(path))
    # A JPEG can be decoded at 1/2, 1/4 or 1/8 size directly: on a 24 MP photo
    # that is most of the work of making a thumbnail, skipped.
    img.draft("RGB", (edge, edge))
    return img


def _open_photo(path: str, edge: int) -> Image.Image:
    return _upright(_draft(path, edge))


def _upright(img: Image.Image) -> Image.Image:
    img.load()
    img = ImageOps.exif_transpose(img) or img
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    return img


def strip_video(path: str, out: str) -> bool:
    """*path* remuxed to *out* with every metadata atom dropped (the phone's
    GPS location among them) and the streams copied, not re-encoded: a copy
    for guests and share links. False when ffmpeg is absent or refuses."""
    if not FFMPEG:
        return False
    # Beside *out*, under a name no "<id>-*" clean-up of that folder matches.
    folder, name = os.path.split(out)
    tmp = os.path.join(folder, f".{name}.{os.getpid()}-{threading.get_ident()}.tmp"
                               f"{os.path.splitext(out)[1]}")
    try:
        proc = subprocess.run(
            [FFMPEG, "-v", "quiet", "-y", "-i", dates.long_path(path), "-map_metadata", "-1",
             "-map_metadata:s", "-1", "-c", "copy", "-movflags", "+faststart", tmp],
            capture_output=True, timeout=300, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return False


#: The longest edge a video's frame is handed over at: twice the big tile, so
#: the thumbnail is still shrunk from more than it shows.
VIDEO_FRAME_EDGE = 2 * THUMB_SIZES["l"]


def video_frame(path: str, threads: int = 0) -> Image.Image | None:
    """One frame from about a second in, at most :data:`VIDEO_FRAME_EDGE`
    across. *threads* is how many cores ffmpeg may use (0: as many as it
    likes); several videos at once each get a share of them."""
    if not FFMPEG:
        return None
    # Shrunk by ffmpeg and passed as plain pixels: a 4K frame as a PNG was
    # most of the time a video's thumbnail took.
    edge = VIDEO_FRAME_EDGE
    shrink = f"scale='min({edge},iw)':'min({edge},ih)':force_original_aspect_ratio=decrease"
    for args in (["-ss", "1"], []):
        try:
            proc = subprocess.run(
                [FFMPEG, "-v", "quiet", "-threads", str(max(0, threads)), *args,
                 "-i", dates.long_path(path), "-frames:v", "1", "-vf", shrink,
                 "-f", "image2pipe", "-vcodec", "bmp", "-"],
                capture_output=True, timeout=45, check=False,
                # Started without a console (at sign-in, by pythonw), Windows
                # would otherwise flash a black window for every video. For
                # a scan it runs below the gallery, as the scan does (Linux
                # and macOS pass that on by themselves).
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | (parallel.WIN_BELOW_NORMAL_CLASS
                   if sys.platform == "win32" and parallel.in_background() else 0))
            if proc.returncode == 0 and proc.stdout:
                return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
        except (subprocess.SubprocessError, OSError, ValueError):
            return None
    return None


def make_thumbnails(path: str, kind: str, thumbs_dir: Path, asset_id: int,
                    sizes: tuple[str, ...] = ("s", "l"), rotation: int = 0,
                    threads: int = 0) -> tuple[bool, str | None]:
    """Write the thumbnails named in *sizes* ("s" 256 px, "l" 640 px), turned
    by *rotation* (the index's answer for a photograph without a camera tag).

    Returns (made, colour): colour is the picture's average, '#rrggbb', which
    the grid paints while the thumbnail loads. ``made`` is False when no
    picture can be made (a video without ffmpeg, a damaged file); the gallery
    then shows a plain tile. *threads* goes to ffmpeg (:func:`video_frame`).
    """
    edge = max(THUMB_SIZES[s] for s in sizes)
    gate = nullcontext()
    try:
        if kind == "picture":
            img = _draft(path, edge)
            if img.width * img.height >= HEAVY_PIXELS:
                gate = _HEAVY
        else:
            img = video_frame(path, threads)
    except Exception as exc:  # noqa: BLE001
        log.debug("no thumbnail for %s: %s", path, exc)
        return False, None
    if img is None:
        return False, None
    with gate:
        try:
            if kind == "picture":
                img = _upright(img)
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


#: What a copy that leaves the family is saved with. Pillow writes the
#: source's JPEG comment (its COM segment) into a new JPEG unless told
#: otherwise, and it survives convert, thumbnail and turn: a caption such as
#: "Amma, hospital, Chennai" went out on share links, to guests and on the
#: sign-in screen's profile pictures (A145). An empty comment writes none.
NO_METADATA = {"comment": b""}


def profile_picture(path: str, rotation: int = 0, size: int = 256) -> bytes:
    """The middle of a photograph as a small square JPEG, upright, no metadata:
    a profile picture for the sign-in screen."""
    with _open_photo(path, size * 2) as img:
        img = turn(img.convert("RGB"), rotation)
        img = ImageOps.fit(img, (size, size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88, optimize=True, **NO_METADATA)
        return out.getvalue()


def viewing_copy(path: str, max_edge: int = 2560, rotation: int = 0) -> bytes:
    """A JPEG a browser can show, upright and without metadata: for HEIC and
    TIFF, which browsers cannot open, and for guests, who get no EXIF.
    *rotation* is the index's own quarter turn, on top of the camera's tag."""
    with _open_photo(path, max_edge) as img:
        img = turn(img.convert("RGB"), rotation)
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=86, optimize=True, **NO_METADATA)
        return out.getvalue()
