"""PNG screenshots (shots/<lang>/) -> JPEG for the PDF (img/<lang>/). Keeps the PDF small."""
from pathlib import Path
from PIL import Image

here = Path(__file__).resolve().parent
for png in sorted((here / "shots").rglob("*.png")):
    rel = png.relative_to(here / "shots").with_suffix(".jpg")
    out = here / "img" / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(png).convert("RGB")
    if im.width > 1700:
        im = im.resize((1700, round(im.height * 1700 / im.width)), Image.LANCZOS)
    im.save(out, "JPEG", quality=86, optimize=True, progressive=True)
print("ok")
