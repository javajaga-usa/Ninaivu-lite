"""The console's flows, read from the server's answers and the page's own
files: what the folder picker says, and what the Duplicates page tells an
administrator to do."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ninaivu_lite" / "static"
EN = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
TA = json.loads((STATIC / "i18n" / "ta.json").read_text(encoding="utf-8"))


def test_flow4_the_pickers_shortcuts_are_said_in_the_readers_language(admin, library):
    """A Tamil folder picker listed "Home" and "Photos (current)" in English:
    the server sent finished English labels."""
    root, _ = library
    shortcuts = admin.get("/api/library/browse").get_json()["shortcuts"]
    current = next(s for s in shortcuts if s["path"] == str(root))
    assert current["key"] == "{name} (current)" and current["vars"] == {"name": "Photos"}
    home = [s for s in shortcuts
            if os.path.normcase(s["path"]) == os.path.normcase(os.path.expanduser("~"))]
    if home:
        assert home[0]["key"] == "Home folder"
    for key in ("{name} (current)", "Home folder", "{letter}: drive"):
        assert key in EN and TA[key] != key
    picker = (STATIC / "js" / "console" / "folder-picker.js").read_text(encoding="utf-8")
    assert "i18n.t(shortcut.key, shortcut.vars)" in picker


def test_flow5_duplicates_never_suggest_removing_a_copy_in_the_app():
    """The Duplicates page and the guide said to remove unwanted copies "in
    the folders themselves (or in Ninaivu)", but nothing in Ninaivu Lite
    removes a file (SECURITY.md): an administrator looked for a button that
    is not there."""
    texts = [(ROOT / "ninaivu_lite" / "templates" / "admin.html").read_text(encoding="utf-8"),
             (ROOT / "docs" / "USER-GUIDE.md").read_text(encoding="utf-8"),
             (ROOT / "docs" / "USER-GUIDE.ta.md").read_text(encoding="utf-8"),
             *EN, *EN.values(), *TA.values()]
    for text in texts:
        flat = " ".join(text.split())
        assert "(or in Ninaivu)" not in flat and "(அல்லது நினைவில்)" not in flat
