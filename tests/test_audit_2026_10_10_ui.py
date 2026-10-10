"""Phone, Tamil and keyboard fixes from the 2026-10-10 UI audit (M22-M25, A175-A179).

These are checks on the text of the stylesheets, scripts and locale files: the
behaviour itself was checked in a browser (Playwright) when the fixes were made,
and the suite has no browser."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "ninaivu_lite"
STATIC = PACKAGE / "static"
CSS_FILES = sorted(STATIC.rglob("*.css"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def without_comments(css: str) -> str:
    """The stylesheet with each well-formed comment blanked out, lines kept."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)


def rule(css: str, selector: str) -> str:
    """The body of the first rule whose selector is exactly `selector`."""
    match = re.search(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", without_comments(css))
    assert match, f"no rule for {selector}"
    return match.group(1)


# -- M22: a comment that lost its opening "/*" dropped the Jump button's rule --

def test_there_are_stylesheets_to_check():
    assert any(p.name == "style.css" for p in CSS_FILES)


@pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
def test_every_comment_in_a_stylesheet_opens_and_closes(path):
    # A stray "*/" (its "/*" lost in a merge) turns the prose before it into a
    # selector, and the browser silently drops the next rule with it.
    stripped = without_comments(read(path))
    for number, line in enumerate(stripped.splitlines(), 1):
        assert "*/" not in line, f"{path.name}:{number}: '*/' with no '/*' before it"
        assert "/*" not in line, f"{path.name}:{number}: '/*' that is never closed"
    assert stripped.count("{") == stripped.count("}"), f"{path.name}: unbalanced braces"


def test_the_jump_button_has_its_own_rule():
    body = rule(read(STATIC / "css" / "style.css"), ".jump-btn")
    assert "position: absolute" in body and "border-radius: 999px" in body


# -- A175: Sudar (a modal <dialog>) owns the keyboard ----------------------------

def test_the_gallery_keys_stand_aside_for_an_open_dialog():
    app = read(STATIC / "js" / "app.js")
    start = app.index("function wireKeyboard()")
    handler = app[start:start + 3000]
    guard = handler.index("document.querySelector('dialog[open]')")
    # Before the theme key, the overlay check and the viewer's keys.
    assert guard < handler.index("cycleTheme()")
    assert guard < handler.index("viewer.handleKey(event)")


# -- M23: long names on the profile picker ---------------------------------------

def test_a_long_profile_name_wraps_and_keeps_the_lock_beside_it():
    accounts = read(STATIC / "js" / "accounts.js")
    assert "el('span', 'picker-name-text', person.name)" in accounts
    assert "tile.title = person.name" in accounts
    css = read(STATIC / "css" / "style.css")
    text = rule(css, ".picker-name-text")
    assert "-webkit-line-clamp: 2" in text and "white-space: normal" in text
    assert "flex: none" in rule(css, ".picker-lock")


# -- A176 / A177: the viewer's Details panel -------------------------------------

def test_the_details_panel_names_the_kind_in_words():
    viewer = read(STATIC / "js" / "viewer.js")
    assert "`${item.kind} ·" not in viewer
    for word in ("Photo", "Video", "Audio"):
        assert f"i18n.key('{word}')" in viewer
        for code in ("en", "ta"):
            assert word in json.loads(read(STATIC / "i18n" / f"{code}.json"))


def test_escape_closes_the_details_panel_before_the_viewer():
    viewer = read(STATIC / "js" / "viewer.js")
    case = viewer[viewer.index("case 'escape':"):]
    case = case[:case.index("case 'arrowright'")]
    assert case.index("this.toggleInfo(false)") < case.index("this.close()")


# -- M24: the share page's password error on a phone held sideways ----------------

def test_the_share_password_error_sits_under_the_field_and_is_read_out():
    share = read(STATIC / "js" / "share.js")
    assert "form.append(input, error, button)" in share
    assert "error.setAttribute('role', 'alert')" in share
    page = read(PACKAGE / "templates" / "share.html")
    assert "@media (max-height: 480px)" in page


# -- M25: a long name on the console's People page --------------------------------

def test_a_long_person_name_breaks_inside_its_card():
    assert "overflow-wrap: anywhere" in rule(read(STATIC / "css" / "admin.css"), ".ap-name strong")


# -- A178: one Tamil word for video -------------------------------------------------

def test_tamil_says_video_one_way():
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    mixed = [key for key, value in ta.items() if "வீடியோ" in value]
    assert not mixed, f"use காணொளி: {mixed[:5]}"
    assert ta["Videos"] == "காணொளிகள்"


# -- A179: contrast of the search hint and the skip link ---------------------------

def test_small_hints_use_the_darker_secondary_text():
    css = without_comments(read(STATIC / "css" / "style.css"))
    for match in re.finditer(r"([^{}]*\.search-kbd[^{}]*)\{([^}]*)\}", css):
        if "color:" in match.group(2):
            assert "var(--text-3)" not in match.group(2), match.group(1).strip()
    skip = rule(read(STATIC / "css" / "style.css"), ".skip-link")
    assert "var(--accent-fg)" not in skip
