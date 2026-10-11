"""Phone and touch-screen fixes from the 2026-10-11 audit: the gallery, its
viewer and the console on 320-430 px phones, phones on their side, the Pi's
800x480 touch screen and iPhones with a notch, in English and Tamil.

Checks on the text of the stylesheets, scripts, templates and locale files.
Each layout was checked in Playwright with phone emulation (touch, 2-3x
pixels, the notch's safe-area insets) before and after the fix; the ones a
browser can show cheaply are also in test_audit_2026_10_11_uiphone_browser.py."""

from __future__ import annotations

import json
import re
from pathlib import Path

from test_audit_2026_10_10b_screens import blocks, read, without_comments

PACKAGE = Path(__file__).resolve().parent.parent / "ninaivu_lite"
STATIC = PACKAGE / "static"
STYLE = read(STATIC / "css" / "style.css")
ADMIN_CSS = read(STATIC / "css" / "admin.css")
APP_JS = read(STATIC / "js" / "app.js")
GRID_JS = read(STATIC / "js" / "grid.js")
INDEX = read(PACKAGE / "templates" / "index.html")
TA = json.loads(read(STATIC / "i18n" / "ta.json"))
TOUCH = "@media (hover: none) and (pointer: coarse)"


def rules(css: str, selector: str, media: str | None = None) -> list[str]:
    """The bodies of every rule whose selector list names *selector*."""
    return [body for m, sel, body in blocks(css)
            if selector in [s.strip() for s in sel.split(",")]
            and (media is None or m == media)]


def function_body(js: str, start: str) -> str:
    at = js.index(start)
    depth, i = 0, js.index("{", at)
    for j in range(i, len(js)):
        depth += {"{": 1, "}": -1}.get(js[j], 0)
        if depth == 0:
            return js[at:j + 1]
    raise AssertionError(start)


# -- The viewer's bar on a 320-360 px phone ------------------------------------

def test_the_viewer_bar_buttons_keep_their_size_and_the_name_gives_way():
    # Favourite, Share and Details were squeezed to 20 px (25 px at 360).
    assert any("flex: none" in body for body in rules(STYLE, ".viewer-tools > .icon-btn")), \
        "the bar's buttons may shrink"
    # The bar itself does not shrink either, or Close runs off the screen.
    assert any("flex: none" in body for body in rules(STYLE, ".viewer-tools"))
    assert any("min-width: 0" in body for body in rules(STYLE, ".viewer-title"))


# -- On this day and the years on a short screen -------------------------------

def test_a_short_screen_keeps_most_of_its_height_for_the_photographs():
    short = "@media (max-height: 520px)"
    tile = rules(STYLE, ".otd-tile", short)
    assert tile and int(re.search(r"height:\s*(\d+)px", tile[0]).group(1)) <= 64
    # The heading sits beside the tiles, not over them.
    assert any("grid-template-columns" in body for body in rules(STYLE, ".on-this-day", short))
    # The years give their line to Jump to there.
    assert any("display: none" in body for body in rules(STYLE, ".year-strip:not([hidden])", short))


# -- A long press on a phone ---------------------------------------------------

def test_the_selection_bar_does_not_move_the_photograph_under_the_finger():
    handler = function_body(APP_JS, "grid.addEventListener('selection'")
    assert "getBoundingClientRect().top" in handler
    assert "scroller.scrollTop += moved" in handler


def test_the_click_after_a_long_press_is_swallowed_only_briefly_and_anywhere():
    # A flag that waited for the next click ate the next real tap on Chrome
    # for Android, which sends no click after a long press.
    assert "swallowClick" not in GRID_JS
    assert "this.swallowUntil = performance.now() + 400" in GRID_JS
    # The lift is heard on the window: the tile's node may have been redrawn.
    assert "window.addEventListener('pointerup', this.onLift)" in GRID_JS
    # The click is stopped on the whole page: the bar moves On this day under
    # the finger, and a tap there opened a different photograph.
    assert "window.addEventListener('click', this.onAnyClick, true)" in GRID_JS
    destroy = function_body(GRID_JS, "  destroy() {")
    for listener in ("'pointerup', this.onLift", "'click', this.onAnyClick, true"):
        assert f"window.removeEventListener({listener})" in destroy


def test_the_selection_actions_are_one_scrolling_line_on_a_phone():
    bar = INDEX[INDEX.index('id="selection-bar"'):INDEX.index('<section class="on-this-day"')]
    actions = bar[bar.index('class="sel-actions"'):]
    for button in ("sel-all", "sel-fav", "sel-download", "sel-album", 'class="sel-rotate',
                   'class="sel-visibility'):
        assert button in actions, button
    phone = rules(STYLE, ".sel-actions", "@media (max-width: 720px)")
    assert phone and "overflow-x: auto" in phone[0] and "wrap" not in phone[0]
    # Elsewhere the wrapper is not there at all for layout; it must come
    # before the phone rule or it would win over it.
    css = without_comments(STYLE)
    assert css.index(".sel-actions { display: contents; }") < css.index("  .sel-actions {\n")
    # A finger has no date rail, so no gutter is kept for it.
    assert any("padding-right" in body for body in rules(STYLE, ".selection-bar", TOUCH))


# -- A day's header ------------------------------------------------------------

def test_a_narrow_day_shows_a_tick_instead_of_cutting_its_count():
    header = function_body(GRID_JS, "  createHeader() {")
    assert "ICON.check" in header and "class=\"label\"" in header
    assert "setAttribute('aria-label', i18n.t('Select all'))" in header
    css = without_comments(STYLE)
    assert ".section-head.packed { container-type: inline-size; }" in css
    container = css[css.index("@container (max-width: 260px)"):]
    container = container[:container.index("\n}\n")]
    assert ".section-head.packed button .label { display: none; }" in container
    # Only the label goes: the button's own display is left to the rules that
    # hide it on a phone until selecting starts.
    assert "button { padding" in container and "display: inline" not in container


def test_touch_screens_show_day_select_all_only_while_selecting():
    phone_rules = [m for m, sel, body in blocks(STYLE)
                   if sel == ".section-head button" and "display: none" in body]
    assert any("(hover: none) and (pointer: coarse)" in m and "max-width: 620px" in m
               for m in phone_rules), phone_rules


# -- The drawer, the search box ------------------------------------------------

def test_picking_a_view_in_the_phone_drawer_closes_it():
    assert "closest('.side-item, .album-card, #year-list .chip, #folder-list button')" in APP_JS


def test_a_phones_search_key_searches_and_clears_the_suggestions():
    keydown = function_body(APP_JS, "input.addEventListener('keydown'")
    enter = keydown[keydown.index("} else if (event.key === 'Enter') {"):]
    assert "list.hidden = true" in enter and "reload({ resetScroll: true })" in enter
    assert "input.blur()" in enter
    assert 'id="search" type="search"' in INDEX and 'enterkeyhint="search"' in INDEX


def test_suggestion_kinds_are_in_the_page_language():
    assert "kind.textContent = entry.type;" not in APP_JS
    kinds = re.search(r"const SUGGESTION_KINDS = \{(.*?)\};", APP_JS, re.S).group(1)
    for word in re.findall(r"i18n\.key\('([^']+)'\)", kinds):
        assert TA.get(word) and TA[word] != word, word


# -- The console on a phone ----------------------------------------------------

def test_the_backup_folder_path_wraps_instead_of_scrolling_the_page():
    assert any("overflow-wrap: anywhere" in body for body in rules(ADMIN_CSS, "#backup-kept"))


def test_first_day_buttons_stay_inside_the_card_on_a_phone():
    phone = "@media (max-width: 560px)"
    assert any("flex-wrap: wrap" in body for body in rules(ADMIN_CSS, "#first-day .modal-foot", phone))
    assert any("flex: 1 1 100%" in body for body in rules(ADMIN_CSS, "#fd-note", phone))
    assert any("min-height: 96px" in body for body in rules(ADMIN_CSS, ".fd-body", phone))


# -- Touch targets -------------------------------------------------------------

def tap_area_rules(css: str) -> list[tuple[str, str]]:
    return [(sel, body) for m, sel, body in blocks(css)
            if m.strip() == TOUCH and "min-width: 44px" in body and "min-height: 44px" in body]


def test_every_small_control_has_a_44px_target_on_touch():
    found = tap_area_rules(STYLE)
    assert len(found) == 1
    selector, body = found[0]
    assert selector.startswith(":where(") and selector.endswith("::after")
    # Buttons, button-like links, tabs, disclosure rows and tick-box labels.
    for kind in ("button", "a.btn", "a.icon-btn", "[role=button]", "[role=tab]",
                 "summary", "label:has(> input[type=checkbox]"):
        assert kind in selector, kind
    # Buttons that draw their own ::after are left alone.
    for own in (".otd-tile", ".needs-attention", ".fold-item"):
        assert own in selector, own
    # Centred, never smaller than the button, and drawn as nothing.
    for part in ('content: ""', "position: absolute", "left: 50%", "top: 50%",
                 "width: 100%", "height: 100%", "translate(-50%, -50%)"):
        assert part in body, part
    for word in ("background", "border", "box-shadow", "outline"):
        assert word not in body, word
    owner = selector.removesuffix("::after")
    owner_rules = [b for m, sel, b in blocks(STYLE) if m.strip() == TOUCH and sel == owner]
    assert owner_rules and "position: relative" in owner_rules[0]
    # A button placed absolutely (On this day's See all, LIVE) must stay so.
    assert owner.startswith(":where(") and ":where(:not(" in owner
    assert any("width: 44px" in body for body in rules(ADMIN_CSS, ".drive-notice-close::after", TOUCH))
    share = read(PACKAGE / "templates" / "share.html")
    assert ":where(button) { position: relative; }" in share
    assert "min-width: 44px; min-height: 44px;" in share


def test_the_page_can_be_pinch_zoomed():
    for name in ("index.html", "admin.html", "share.html"):
        page = read(PACKAGE / "templates" / name)
        viewport = re.search(r'<meta name="viewport" content="([^"]+)"', page).group(1)
        assert "maximum-scale" not in viewport and "user-scalable" not in viewport, name
        assert "width=device-width" in viewport


def test_fields_are_16px_on_touch_so_focusing_one_does_not_zoom():
    found = [(sel, body) for m, sel, body in blocks(STYLE)
             if m.strip() == TOUCH and "font-size: 16px !important" in body]
    assert len(found) == 1
    selector = found[0][0]
    for kind in ("input:not([type=checkbox])", "select", "textarea"):
        assert kind in selector, kind
    # The PIN field is drawn at 24 px and keeps it.
    assert ":not(.pin-input)" in selector


def test_a_double_tap_zooms_the_photograph_not_the_page():
    html_rules = rules(STYLE, "html", TOUCH)
    assert any("touch-action: manipulation" in body for body in html_rules)
    assert any("overflow-x: clip" in body for body in html_rules)
    everything = [body for m, sel, body in blocks(STYLE) if m.strip() == TOUCH and sel == ":where(body, body *)"]
    assert everything and "touch-action: manipulation" in everything[0]
    assert any("touch-action: none" in body for body in rules(STYLE, ".viewer-stage"))
    viewer = read(STATIC / "js" / "viewer.js")
    assert "'gesturestart', 'gesturechange'" in viewer
    assert "this.stage.addEventListener(type, (event) => event.preventDefault(), { passive: false })" in viewer
    assert "this.stage.addEventListener('dblclick'" in viewer


# -- The notch -----------------------------------------------------------------

def test_the_live_badge_sits_below_the_viewer_bar_under_a_notch():
    badge = rules(STYLE, ".viewer-stage > .live-badge")[0]
    assert "env(safe-area-inset-top" in badge and "top: 70px" not in badge


def test_rows_between_the_bars_keep_clear_of_a_notch_on_its_side():
    for selector in (".on-this-day", ".year-strip"):
        body = " ".join(rules(STYLE, selector))
        assert "env(safe-area-inset-left" in body and "env(safe-area-inset-right" in body, selector
    for selector in (".filter-bar", ".selection-bar"):
        assert "env(safe-area-inset-left" in " ".join(rules(STYLE, selector)), selector
    assert "env(safe-area-inset-right" in " ".join(rules(STYLE, ".selection-bar", TOUCH))
    info = " ".join(rules(STYLE, ".viewer-info"))
    for side in ("left", "right", "bottom"):
        assert f"env(safe-area-inset-{side}" in info, side


def test_a_text_box_in_a_row_gives_way_to_its_button():
    # The profile sheet's Save ran 21 px past the card on a 320 px phone.
    assert any("min-width: 0" in body for body in rules(STYLE, ".row .input"))
    assert any("flex: none" in body for body in rules(STYLE, ".row > .btn"))


def test_sheets_measure_the_visible_height_on_an_iphone():
    assert any("88dvh" in body for body in rules(STYLE, ".sheet-card"))
    assert any("70dvh" in body for body in rules(STYLE, ".palette-card"))
