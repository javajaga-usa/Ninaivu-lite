"""Screen fixes from the second 2026-10-10 audit: phones, Tamil, the Rotate bar.

Checks on the text of the stylesheets, scripts and locale files, plus the
contrast of the colour tokens worked out here. The layouts themselves were
checked in a browser (Playwright, 360x640 to 1280x800, Tamil and English) when
the fixes were made; the suite has no browser."""

from __future__ import annotations

import json
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "ninaivu_lite"
STATIC = PACKAGE / "static"
CSS = STATIC / "css"
JS = STATIC / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def blocks(css: str):
    """(media condition or "", selector, body) for every rule, one level of @media deep."""
    css = without_comments(css)
    found = []
    i = 0
    while True:
        open_at = css.find("{", i)
        if open_at < 0:
            return found
        head = css[i:open_at].strip()
        if head.startswith("@"):
            depth, j = 1, open_at + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(css[j], 0)
                j += 1
            if head.startswith(("@media", "@supports")):
                found += [(head, sel, body) for _, sel, body in blocks(css[open_at + 1:j - 1])]
            i = j
        else:
            close = css.find("}", open_at)
            found.append(("", head, css[open_at + 1:close]))
            i = close + 1


def tokens(css: str, scope: str) -> dict[str, str]:
    match = re.search(re.escape(scope) + r"\s*\{([^}]*)\}", without_comments(css))
    assert match, scope
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", match.group(1)))


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(top: str, bottom: str, share: float) -> str:
    """`top` laid over `bottom` at `share` opacity, as a hex colour."""
    parts = [round(int(top[i:i + 2], 16) * share + int(bottom[i:i + 2], 16) * (1 - share))
             for i in (1, 3, 5)]
    return "#" + "".join(f"{p:02x}" for p in parts)


STYLE = read(CSS / "style.css")
ADMIN_CSS = read(CSS / "admin.css")
LIGHT = tokens(STYLE, ":root")
DARK = tokens(STYLE, ':root[data-theme="dark"]')


# -- The Rotate bar with Details open on a phone ------------------------------

def test_the_rotate_bar_moves_aside_for_details_only_where_details_is_a_side_panel():
    rules = [(media, body) for media, sel, body in blocks(STYLE)
             if sel == ".viewer.info-open .rotate-bar"]
    assert rules, "no rule for the bar beside Details"
    for media, body in rules:
        if "left:" in body and "320px" in body:
            # Below 901px Details is the full width: the shift put the bar,
            # Save included, half off the left of a phone.
            assert "min-width: 901px" in media, media or "applies at every width"
    full_width = [media for media, sel, body in blocks(STYLE)
                  if sel == ".viewer-info" and "width: 100%" in body]
    assert full_width == ["@media (max-width: 900px)"]


# -- A video's play bar, and the line that says why it cannot play -----------

def test_the_turned_video_bar_goes_when_there_is_nothing_to_play():
    viewer = read(JS / "viewer.js")
    handler = viewer[viewer.index("video.onerror"):viewer.index("this.media = video;")]
    assert ".video-turned-controls')?.remove()" in handler
    # No " / " before the length is known.
    assert "time.textContent = length" in viewer


def test_the_viewer_line_over_the_stage_is_readable():
    body = next(body for _, sel, body in blocks(STYLE) if sel == ".viewer-stage > .badge")
    assert "color: #fff" in body and "background: rgba(8, 10, 14" in body


def test_a_shared_turned_video_has_controls_the_right_way_up():
    share = read(JS / "share.js")
    turned = share[share.index("if (rotation || item.mirror)"):share.index("function turnedControls")]
    assert "node.controls = false" in turned and "turnedControls(node)" in turned
    assert "'Position in the video'" in share
    page = read(PACKAGE / "templates" / "share.html")
    assert ".turned-controls {" in page and ".turned-controls[hidden] { display: none; }" in page


# -- The Details button --------------------------------------------------------

def test_only_the_heart_is_filled_when_lit():
    selectors = {sel for _, sel, _ in blocks(STYLE)}
    assert ".viewer-tools .icon-btn.on svg" not in selectors
    assert ".viewer-tools #v-fav.on svg" in selectors


# -- Tamil ---------------------------------------------------------------------

def test_the_preview_names_a_role_in_the_page_language():
    admin = read(JS / "admin.js")
    assert "i18n.t(data.role_label || data.as)" in admin
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    for role in ("Guest", "Family member"):
        assert ta[role] != role


def test_keys_chosen_by_a_condition_are_translated():
    # t(cond ? 'A' : 'B') is not seen by the locale guard (its first argument
    # is not a literal): both sides must still be in both files.
    en = json.loads(read(STATIC / "i18n" / "en.json"))
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    pattern = re.compile(r"""\bt\(\s*[\w.!]+\s*\?\s*'([^']+)'\s*:\s*'([^']+)'\s*\)""")
    seen = []
    for path in sorted(JS.rglob("*.js")):
        for match in pattern.finditer(read(path)):
            for key in match.groups():
                seen.append(key)
                assert key in en and key in ta, f"{path.name}: {key!r}"
    assert "Play" in seen and "Pause" in seen


# -- Contrast ------------------------------------------------------------------

def test_helper_text_meets_four_and_a_half_to_one():
    for ground in ("bg", "bg-elev", "surface", "surface-2"):
        assert contrast(LIGHT["text-3"], LIGHT[ground]) >= 4.5, ground
    for ground in ("bg", "bg-elev", "surface"):
        assert contrast(DARK["text-3"], DARK[ground]) >= 4.5, ground


def test_an_empty_note_is_not_gold_on_gold():
    body = next(body for _, sel, body in blocks(ADMIN_CSS) if sel == ".empty-note")
    colour = re.search(r"color:\s*var\(--([\w-]+)\)", body).group(1)
    wash = mix(LIGHT["gold"], LIGHT["bg-elev"], 0.10)
    assert contrast(LIGHT[colour], wash) >= 4.5


def test_year_counts_are_not_faded_below_the_helper_text():
    body = next(body for _, sel, body in blocks(STYLE) if sel == ".chip .n")
    assert "opacity" not in body and "var(--text-3)" in body


# -- Dialogs and the keyboard --------------------------------------------------

def test_escape_in_the_console_closes_the_dialog_on_top():
    admin = read(JS / "admin.js")
    handler = admin[admin.index("if (event.key !== 'Escape') return;"):]
    handler = handler[:handler.index("});")]
    assert ".reverse().find((m) => !m.hidden)" in handler


def test_escape_closes_the_sidebar_drawer():
    app = read(JS / "app.js")
    keys = app[app.index("function wireKeyboard()"):]
    drawer = keys[keys.index("event.key === 'Escape' && document.querySelector('.shell.mobile-open')"):]
    drawer = drawer[:drawer.index("return;")]
    assert "classList.remove('mobile-open')" in drawer and "'aria-expanded', 'false'" in drawer


def test_dialog_buttons_stay_on_a_phone_width_card():
    phone = [(media, sel, body) for media, sel, body in blocks(ADMIN_CSS) if "max-width: 560px" in media]
    assert any(sel == "#folder-modal .modal-foot" and "flex-wrap: wrap" in body for _, sel, body in phone)
    assert any(sel == "#fm-manual" and "min-width: 0" in body for _, sel, body in phone)
    album = next(body for _, sel, body in blocks(STYLE) if sel == ".album-create-box .input")
    assert "min-width: 0" in album


# -- The accent blue and the danger red as text, or under white text ----------

SHARE_PAGE = read(PACKAGE / "templates" / "share.html")


def test_the_light_accent_and_danger_read_at_four_and_a_half_to_one():
    for palette in (LIGHT, tokens(SHARE_PAGE, ":root")):
        accent, danger = palette["accent"], palette["danger"]
        ground = palette.get("bg-elev", palette.get("panel"))
        page = palette.get("bg", palette.get("ground"))
        # Links and labels on the page, white on a primary button, and blue
        # lettering on its own wash (the drive notice, the selection bar).
        assert contrast(accent, ground) >= 4.5 and contrast(accent, page) >= 4.5
        assert contrast("#ffffff", accent) >= 4.5
        assert contrast(accent, mix(accent, page, 0.10)) >= 4.5
        # Error lines on their pink wash, and Delete on a grey button.
        assert contrast(danger, mix(danger, ground, 0.10)) >= 4.5
        assert contrast(danger, palette.get("surface-2", ground)) >= 4.5
    assert "rgba(9, 105, 176, 0.10)" in STYLE  # the wash follows the blue


def test_the_dark_accent_and_danger_still_read():
    for ground in ("bg", "bg-elev", "surface", "surface-2"):
        assert contrast(DARK["accent"], DARK[ground]) >= 4.5, ground
        assert contrast(DARK["danger"], DARK[ground]) >= 4.5, ground
    assert contrast(DARK["accent-fg"], DARK["accent"]) >= 4.5


# -- The share page: a single video ------------------------------------------

def test_a_shared_video_is_called_a_video():
    share = read(JS / "share.js")
    assert "i18n.t(isVideo ? 'Shared video' : 'Shared photograph')" in share
    en = json.loads(read(STATIC / "i18n" / "en.json"))
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    assert en["Shared video"] == "Shared video" and "காணொளி" in ta["Shared video"]


def test_a_quarter_turned_shared_video_fills_a_box_of_its_turned_shape():
    share = read(JS / "share.js")
    fit = share[share.index("const box = el('div', 'turned-box');"):share.index("function turnedControls")]
    # The box takes the turned shape; the video, the unturned one, centred in it.
    assert "box.style.width = `${width}px`; box.style.height = `${height}px`;" in fit
    assert "node.style.width = `${height}px`; node.style.height = `${width}px`;" in fit
    assert "translate(-50%, -50%)" in fit
    # Room is left under it for the play bar.
    assert "window.innerHeight - stage.getBoundingClientRect().top" in fit
    assert ".turned-box.quarter video { position: absolute;" in SHARE_PAGE
    assert "max-width: none; max-height: none;" in SHARE_PAGE


# -- Toasts and Jump to on a phone ---------------------------------------------

def test_a_toast_rises_above_jump_to_on_a_phone():
    rules = [(media, body) for media, sel, body in blocks(STYLE)
             if "#jump-btn:not([hidden])" in sel and sel.endswith(".toasts")]
    assert rules, "no rule lifting the toasts over Jump to"
    media, body = rules[0]
    assert media == "@media (max-width: 620px)"
    lift = int(re.search(r"bottom:\s*calc\((\d+)px", body).group(1))
    jump = next(b for m, s, b in blocks(STYLE) if s == ".jump-btn" and not m)
    pill_top = int(re.search(r"bottom:\s*calc\((\d+)px", jump).group(1)) + 44
    assert lift > pill_top
