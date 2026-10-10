"""The guides keep up with the screens: every labelled UI string in the printed
guides is one the app shows, and the Markdown guides no longer describe the
plugged-in drive pop-up that 1.11.0 replaced with a notice."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Buttons of the drive pop-up before 1.11.0, in English and Tamil. None is
#: in the app any more, so a guide that names one describes a box that no
#: longer opens.
REMOVED_DRIVE_LABELS = (
    "Import photos from this drive",
    "Export media to this drive",
    "Import photos from this phone",
    "Not now",
    "இந்த டிரைவிலிருந்து படங்களை இறக்குமதி செய்",
    "இந்த டிரைவுக்குப் படங்களை ஏற்றுமதி செய்",
    "இந்தத் தொலைபேசியிலிருந்து படங்களை இறக்குமதி செய்",
    "இப்போது வேண்டாம்",
)


def test_the_printed_guides_use_only_the_apps_own_labels():
    done = subprocess.run([sys.executable, str(DOCS / "guide" / "check_labels.py"), str(ROOT)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("name", ["USER-GUIDE.md", "USER-GUIDE.ta.md"])
def test_the_markdown_guides_do_not_describe_the_old_drive_popup(name):
    text = (DOCS / name).read_text(encoding="utf-8")
    named = [label for label in REMOVED_DRIVE_LABELS if label in text]
    assert not named, f"{name} still names the old drive pop-up's buttons: {named}"


def test_the_removed_labels_are_really_gone_from_the_app():
    """So the list above stays a list of what was removed, not of what is there."""
    locale = (ROOT / "ninaivu_lite" / "static" / "i18n" / "ta.json").read_text(encoding="utf-8")
    script = (ROOT / "ninaivu_lite" / "static" / "js" / "drives.js").read_text(encoding="utf-8")
    for label in REMOVED_DRIVE_LABELS:
        assert f'"{label}"' not in locale and f"'{label}'" not in script, label
