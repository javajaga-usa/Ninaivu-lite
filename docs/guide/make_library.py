"""Make the sample photo library the user-guide screenshots are taken from.

    python make_library.py <out-dir>

Every picture is drawn here with Pillow (no real people's photographs): hills,
sunsets, the sea, flowers, a kolam, a city at night, a temple tower, a birthday
cake. Each has EXIF dates and a camera, some a GPS position, across 2012-2025,
filed in family-style folders. A few short videos are made with ffmpeg when it
is installed. Also writes <out-dir>/../old-drive (photos for the Import page).
The same seed always makes the same library.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

R = random.Random(20261006)
W, H = 1600, 1200

CAMERAS = [("Canon", "Canon EOS 600D"), ("NIKON CORPORATION", "NIKON D5300"),
           ("samsung", "Galaxy S10"), ("Apple", "iPhone 12"), ("Google", "Pixel 7"),
           ("SONY", "DSC-W800"), ("Xiaomi", "Redmi Note 9")]
PLACES = {"Ooty": (11.41, 76.70), "Madurai": (9.925, 78.12), "Chennai": (13.08, 80.27),
          "Kanyakumari": (8.08, 77.54), "Kodaikanal": (10.24, 77.49), "Mahabalipuram": (12.62, 80.19)}


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def sky(d, top, bottom, h=H, w=W):
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bottom, y / h))


def hills(d, base, colours, w=W, h=H):
    for i, col in enumerate(colours):
        amp = R.randint(40, 120)
        freq = R.uniform(0.002, 0.006)
        ph = R.uniform(0, 6)
        y0 = base + i * (h - base) // (len(colours) + 1)
        pts = [(x, y0 + amp * math.sin(x * freq + ph) + 30 * math.sin(x * freq * 3.1 + ph)) for x in range(0, w + 20, 20)]
        d.polygon(pts + [(w, h), (0, h)], fill=col)


def scene_hills(img, d):
    sky(d, (120, 170, 230), (225, 238, 250))
    hills(d, 520, [(110, 150, 120), (70, 125, 80), (45, 100, 55), (30, 80, 40)])
    for _ in range(R.randint(4, 9)):  # trees
        x, y = R.randint(0, W), R.randint(850, 1100)
        d.polygon([(x, y - 140), (x - 45, y), (x + 45, y)], fill=(25, 70, 35))


def scene_sunset(img, d):
    sky(d, (60, 40, 110), (250, 150, 70))
    sx, sy = R.randint(400, 1200), R.randint(520, 700)
    for r in range(220, 0, -10):
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=lerp((250, 150, 70), (255, 235, 160), 1 - r / 220))
    hills(d, 760, [(90, 50, 80), (60, 35, 60), (35, 20, 40)])


def scene_sea(img, d):
    sky(d, (90, 160, 225), (200, 230, 250), h=600)
    for y in range(600, H):
        d.line([(0, y), (W, y)], fill=lerp((40, 120, 170), (20, 70, 120), (y - 600) / 600))
    for _ in range(60):
        x, y = R.randint(0, W), R.randint(620, 1000)
        d.line([(x, y), (x + R.randint(30, 90), y)], fill=(180, 220, 240), width=3)
    d.polygon([(0, 1000), (W, 1080), (W, H), (0, H)], fill=(225, 205, 160))
    r = 70
    d.ellipse([1200 - r, 160 - r, 1200 + r, 160 + r], fill=(255, 245, 200))


def scene_flowers(img, d):
    sky(d, (150, 200, 120), (60, 120, 60))
    palette = [(230, 60, 90), (250, 200, 40), (250, 120, 30), (200, 80, 200), (255, 255, 255)]
    for _ in range(70):
        x, y, s = R.randint(0, W), R.randint(0, H), R.randint(25, 70)
        c = R.choice(palette)
        for k in range(6):
            a = k * math.pi / 3
            px, py = x + s * math.cos(a), y + s * math.sin(a)
            d.ellipse([px - s * .7, py - s * .7, px + s * .7, py + s * .7], fill=c)
        d.ellipse([x - s * .5, y - s * .5, x + s * .5, y + s * .5], fill=(250, 220, 80))


def scene_kolam(img, d):
    sky(d, (120, 90, 70), (95, 70, 55))
    cx, cy = W // 2, H // 2
    n = R.choice([5, 7, 9])
    step = 90
    col = (250, 248, 240)
    for i in range(n):
        for j in range(n):
            x, y = cx + (i - n // 2) * step, cy + (j - n // 2) * step
            if abs(i - n // 2) + abs(j - n // 2) <= n // 2:
                d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=col)
                d.arc([x - 45, y - 45, x + 45, y + 45], R.randint(0, 90), R.randint(200, 330), fill=col, width=5)
    for k, c in enumerate([(230, 40, 60), (250, 190, 30), (40, 160, 90)]):
        r = 560 - k * 25
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=10)


def scene_city(img, d):
    sky(d, (10, 15, 45), (60, 40, 90))
    x = 0
    while x < W:
        bw, bh = R.randint(80, 200), R.randint(300, 900)
        d.rectangle([x, H - bh, x + bw, H], fill=(25, 25, 40))
        for wy in range(H - bh + 20, H - 20, 40):
            for wx in range(x + 12, x + bw - 20, 30):
                if R.random() < 0.5:
                    d.rectangle([wx, wy, wx + 14, wy + 20], fill=(250, 210, 110))
        x += bw + R.randint(5, 30)
    d.ellipse([1300, 100, 1400, 200], fill=(240, 240, 220))


def scene_temple(img, d):
    sky(d, (250, 190, 120), (250, 230, 190))
    cx = R.randint(600, 1000)
    base = 1050
    for k in range(9):
        w = 520 - k * 50
        h = 75
        y = base - k * h
        c = lerp((200, 120, 70), (230, 170, 110), k / 9)
        d.rectangle([cx - w // 2, y - h, cx + w // 2, y], fill=c, outline=(150, 80, 50), width=3)
        for t in range(cx - w // 2 + 15, cx + w // 2 - 15, 40):
            d.rectangle([t, y - h + 15, t + 18, y - 15], fill=(160, 90, 55))
    d.ellipse([cx - 60, base - 9 * 75 - 90, cx + 60, base - 9 * 75 + 10], fill=(220, 160, 90))
    d.rectangle([0, base, W, H], fill=(200, 180, 150))


def scene_cake(img, d):
    sky(d, (250, 220, 230), (240, 190, 210))
    cx, cy = W // 2, 800
    d.ellipse([cx - 420, cy + 150, cx + 420, cy + 260], fill=(240, 240, 245))
    for k, (w, h, c) in enumerate([(360, 220, (130, 80, 50)), (260, 170, (240, 200, 220))]):
        top = cy - k * 200
        d.rectangle([cx - w, top - h + 200, cx + w, top + 200], fill=c)
        d.ellipse([cx - w, top - h + 160, cx + w, top - h + 240], fill=lerp(c, (255, 255, 255), .3))
    for i in range(R.randint(3, 8)):
        x = cx - 200 + i * 60
        d.rectangle([x, 380, x + 14, 470], fill=R.choice([(80, 150, 230), (250, 120, 150), (250, 210, 60)]))
        d.ellipse([x - 4, 345, x + 18, 385], fill=(255, 190, 40))


SCENES = {"hills": scene_hills, "sunset": scene_sunset, "sea": scene_sea, "flowers": scene_flowers,
          "kolam": scene_kolam, "city": scene_city, "temple": scene_temple, "cake": scene_cake}

# folder, how many, year range, scenes to choose from, place
PLAN = [
    ("Family/2012", 12, (2012, 2012), ["hills", "flowers", "cake"], None),
    ("Family/2015", 8, (2015, 2015), ["flowers", "cake", "kolam"], "Chennai"),
    ("Family/2018", 8, (2018, 2018), ["hills", "cake", "sunset"], "Chennai"),
    ("Family/2021", 8, (2021, 2021), ["flowers", "kolam", "city"], "Chennai"),
    ("Family/2024", 10, (2024, 2024), ["cake", "sunset", "flowers"], "Chennai"),
    ("Trips/Ooty 2016", 10, (2016, 2016), ["hills", "sunset"], "Ooty"),
    ("Trips/Kanyakumari 2019", 10, (2019, 2019), ["sea", "sunset"], "Kanyakumari"),
    ("Trips/Kodaikanal 2022", 10, (2022, 2022), ["hills", "flowers"], "Kodaikanal"),
    ("Trips/Mahabalipuram 2025", 8, (2025, 2025), ["sea", "temple"], "Mahabalipuram"),
    ("Festivals/Pongal", 10, (2013, 2025), ["kolam", "flowers"], "Madurai"),
    ("Festivals/Deepavali", 10, (2014, 2025), ["city", "kolam"], "Madurai"),
    ("Temples", 8, (2017, 2023), ["temple"], "Madurai"),
    ("Birthdays", 8, (2013, 2024), ["cake"], "Chennai"),
]


def exif_for(when: datetime, camera, place):
    ex = Image.Exif()
    ex[0x010F], ex[0x0110] = camera
    ex[0x0132] = when.strftime("%Y:%m:%d %H:%M:%S")
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
    sub[0x9004] = sub[0x9003]
    if place:
        lat, lon = PLACES[place]
        lat += R.uniform(-.02, .02)
        lon += R.uniform(-.02, .02)

        def dms(v):
            v = abs(v)
            dd = int(v)
            mm = int((v - dd) * 60)
            ss = round(((v - dd) * 60 - mm) * 60, 2)
            return (float(dd), float(mm), ss)
        gps = ex.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", dms(lat), "E", dms(lon)
    return ex


def draw(scene: str, portrait: bool) -> Image.Image:
    img = Image.new("RGB", (W, H))
    SCENES[scene](img, ImageDraw.Draw(img))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    if portrait:
        img = img.crop(((W - 900) // 2, 0, (W + 900) // 2, H)).resize((900, 1200))
    return img


def make(out: Path, folder: str, count: int, years, scenes, place, start_index=1):
    target = out / folder
    target.mkdir(parents=True, exist_ok=True)
    made = []
    # Photos come in days, as a family takes them: a few outings per folder,
    # several pictures each, a few minutes apart.
    days = []
    for _ in range(max(2, count // 3)):
        year = R.randint(*years)
        day = datetime(year, 1, 14, 9) if "Pongal" in folder else \
            datetime(year, 1, 1, 9) + timedelta(days=R.randint(0, 360))
        days.append([day, R.choice(CAMERAS)])
    if folder.startswith("Trips/"):  # one trip: consecutive days
        first = days[0][0]
        days = [[first + timedelta(days=k), days[0][1]] for k in range(len(days))]
    for i in range(count):
        slot = days[i % len(days)] if i < len(days) else R.choice(days)
        slot[0] += timedelta(minutes=R.randint(4, 50))
        when = slot[0]
        camera = slot[1]
        scene = R.choice(scenes)
        img = draw(scene, portrait=R.random() < 0.2)
        name = f"IMG_{when:%Y%m%d}_{when:%H%M%S}.jpg" if "Apple" not in camera[0] else f"IMG_{start_index + i:04d}.JPG"
        img.save(target / name, "JPEG", quality=84, exif=exif_for(when, camera, place if R.random() < 0.7 else None))
        os.utime(target / name, (when.timestamp(), when.timestamp()))
        made.append(target / name)
    return made


def videos(out: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    clips = [("Trips/Kanyakumari 2019/VID_20190512_071500.mp4", "2019-05-12T07:15:00Z", "0x2a7fbf"),
             ("Family/2024/VID_20240824_183000.mp4", "2024-08-24T18:30:00Z", "0xcf6a3a"),
             ("Festivals/Pongal/VID_20230114_093000.mp4", "2023-01-14T09:30:00Z", "0x3a8f4a")]
    for rel, when, colour in clips:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"color=c={colour}:s=960x540:d=4,format=yuv420p",
                        "-vf", "drawbox=x='mod(t*200,960)':y=200:w=120:h=120:color=white@0.8:t=fill",
                        "-metadata", f"creation_time={when}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(p)], check=True)


def main():
    out = Path(sys.argv[1]).resolve()
    if out.exists():
        shutil.rmtree(out)
    n = 0
    for folder, count, years, scenes, place in PLAN:
        n += len(make(out, folder, count, years, scenes, place, start_index=n + 1))
    videos(out)
    old = out.parent / "old-drive"
    if old.exists():
        shutil.rmtree(old)
    make(old, "DCIM/100CANON", 6, (2010, 2011), ["hills", "flowers", "sea"], None, 500)
    make(old, "Backup 2011/Wedding", 4, (2011, 2011), ["temple", "kolam"], "Madurai", 600)
    print(f"{n} photos in {out}; old drive in {old}")


if __name__ == "__main__":
    main()
