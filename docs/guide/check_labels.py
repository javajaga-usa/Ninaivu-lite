"""Every <span class="ui"> label in en.html / ta.html must be a string of the app's
en.json / ta.json (python check_labels.py <app checkout>)."""
import json
import re
import sys
from html import unescape
from pathlib import Path
here = Path(__file__).resolve().parent
app = Path(sys.argv[1] if len(sys.argv) > 1 else here / "../..")
bad = 0
for lang in ("en", "ta"):
    values = set(json.load(open(app / f"ninaivu_lite/static/i18n/{lang}.json", encoding="utf-8")).values())
    values |= {"English", "தமிழ்"}
    html = (here / f"{lang}.html").read_text(encoding="utf-8")
    for label in re.findall(r'<span class="ui[^"]*">(.*?)</span>', html):
        text = unescape(re.sub(r"<[^>]+>", "", label))
        if text not in values:
            bad += 1
            print(f"{lang}: not an app string: {text!r}")
print("labels ok" if not bad else f"{bad} label(s) to fix")
sys.exit(1 if bad else 0)
