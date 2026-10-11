"""Desktop screen fixes (1024 to 1920 wide, English and Tamil, light and dark).

The layouts were checked in a real browser when the fixes were made (Playwright,
before and after screenshots); these tests hold the fixes in place through the
text of the scripts, stylesheets and pages, the colour tokens' contrast, a
little of the layout code run in Node, and the server's own answers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_audit_2026_10_10b_screens import blocks, contrast, mix, tokens

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ninaivu_lite"
STATIC = PACKAGE / "static"
CSS = STATIC / "css"
JS = STATIC / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


STYLE = read(CSS / "style.css")
LIGHT = tokens(STYLE, ":root")
DARK = tokens(STYLE, ':root[data-theme="dark"]')
APP_JS = read(JS / "app.js")
FILTERS_JS = read(JS / "gallery" / "filters.js")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")


def node_eval(script: str) -> str:
    done = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                          capture_output=True, text=True, cwd=ROOT, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def rule(css: str, selector: str) -> str:
    for _media, sel, body in blocks(css):
        if selector in [s.strip() for s in sel.split(",")]:
            return body
    raise AssertionError(f"no rule for {selector}")


# --- small days side by side: the Tamil count was cut to "1…" --------------------------------


@needs_node
def test_a_packed_day_is_as_wide_as_its_header_needs_in_the_language_on_screen():
    script = """
      import { computeLayout } from './ninaivu_lite/static/js/layout.js';
      const day = (d) => ({ key: `2025-12-${d}`, count: 1, items: [[d, 75, 0, 2, 0, 0]] });
      const segments = [day(17), day(3), day(1)];
      const widths = (packMin) => computeLayout(segments,
        { width: 960, mode: 'justified', zoom: 2, gap: 6, packMin })
        .headers.map((h) => h.w);
      console.log(JSON.stringify([widths(undefined), widths(228)]));
    """
    plain, tamil = json.loads(node_eval(script))
    assert plain[0] == 170 and tamil[0] == 228
    # Never narrower than before, whatever is asked.
    assert json.loads(node_eval(script.replace("widths(228)", "widths(40)")))[1][0] == 170


def test_the_grid_measures_the_packed_header_and_lays_out_again_for_a_new_language():
    grid = read(JS / "grid.js")
    assert "packMin: this.packedHeadWidth()" in grid
    assert "packedHeadWidth() {" in grid and "i18n.language()" in grid
    relabel = grid[grid.index("  relabel() {"):grid.index("  relayout(preserveAnchor")]
    assert "this.packedHeadWidth() !== before" in relabel and "this.relayout(true)" in relabel
    # A pooled header made in the language used before is relabelled when reused.
    mount = grid[grid.index("node = this.headPool.pop()"):]
    assert "textContent = i18n.t('Select all')" in mount[:800]


# --- Safari: every blur and every no-select carries its -webkit- twin -------------------------


@pytest.mark.parametrize("sheet", ["style.css", "admin.css", "sudar.css"])
def test_safari_reads_backdrop_filter_and_user_select_only_with_the_prefix(sheet):
    missing = []
    for media, selector, body in blocks(read(CSS / sheet)):
        for prop in ("backdrop-filter", "user-select"):
            if (re.search(r"(?<![-\w])" + prop + r"\s*:", body)
                    and not re.search(r"-webkit-" + prop + r"\s*:", body)):
                missing.append(f"{media} {selector.strip()} {prop}")
    assert not missing, missing


# --- On this day: its chip, its tiles, and a change of language ------------------------------


def test_the_on_this_day_chip_names_the_day_in_words():
    assert "state.filters.onThisDay.slice(5)" not in FILTERS_JS
    assert "dayOfYear(state.filters.onThisDay)" in FILTERS_JS
    helper = FILTERS_JS[FILTERS_JS.index("function dayOfYear(day)"):]
    assert "toLocaleDateString(i18n.locale()" in helper[:500] and "month: 'long'" in helper[:500]


def test_on_this_day_tiles_say_their_date_in_the_page_language():
    otd = read(JS / "on-this-day.js")
    assert "button.title = key;" not in otd and "`${label} · ${key}`" not in otd
    assert "const when = spelled(key);" in otd and "button.title = when;" in otd


def test_a_new_language_redraws_the_filter_chips_and_the_on_this_day_row():
    change = FILTERS_JS[FILTERS_JS.index("  i18n.onChange(() => {\n    setSearchHint();"):]
    change = change[:change.index("  });")]
    assert "renderFilterBar();" in change and "onThisDay?.refresh(true);" in change
    otd = read(JS / "on-this-day.js")
    assert "async function refresh(again = false)" in otd and "if (again) shownFor = null;" in otd


# --- an old scan's "116 added" on every visit -------------------------------------------------


def test_a_scan_finished_before_the_page_opened_is_not_announced_again():
    progress = read(JS / "gallery" / "progress.js")
    body = progress[progress.index("const phase = `${scan.status}:${scan.added}:${scan.removed}`;"):]
    guard = body[:body.index("toast(i18n.t('{added} added")]
    assert "if (lastPhase === null) {" in guard
    assert "if (scan.status === 'done' || scan.status === 'error') return;" in guard


# --- the date rail beside the grid, not over the bars above it --------------------------------


def test_the_scrubber_rail_starts_where_the_grid_does():
    scrubber = read(JS / "gallery" / "scrubber.js")
    wire = scrubber[scrubber.index("function wireScrubber() {"):]
    wire = wire[:wire.index("thumb.addEventListener")]
    assert "scroller.offsetTop" in wire and "scrubber.style.top = `${top + 8}px`" in wire
    assert "new ResizeObserver(fit).observe(scroller)" in wire and "buildScrubber()" in wire


# --- words left in English in the Tamil pages -------------------------------------------------


def test_search_suggestions_name_their_kind_in_the_page_language():
    assert "kind.textContent = entry.type;" not in FILTERS_JS
    assert "const SUGGESTION_KINDS = {" in FILTERS_JS
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    assert ta["Folder"] and ta["Camera"] and ta["Search"]


def test_ninaivus_own_name_is_written_in_the_page_language():
    share = read(JS / "share.js")
    assert "name === 'Ninaivu' ? i18n.t('Ninaivu') : name" in share
    # The profile sheet's "home name" placeholder: Ninaivu's own name is no name.
    assert "profileSheet.houseName = house === 'Ninaivu' ? '' : house;" in APP_JS
    assert "this.houseName || i18n.t('Ninaivu')" in read(JS / "accounts.js")


# --- keyboard and screen readers ----------------------------------------------------------------


def test_the_console_dialogs_make_the_page_behind_them_inert():
    admin = read(JS / "admin.js")
    assert "import { wireDialogs } from './dialogs.js';" in admin
    init = admin[admin.index("async function init() {"):]
    assert "wireDialogs();" in init[:init.index("gate = new Gate")]
    page = re.search(r"const PAGE = '([^']+)'", read(JS / "dialogs.js")).group(1)
    assert ".admin-bar" in page and ".admin-main" in page
    html = read(PACKAGE / "templates" / "admin.html")
    assert '<header class="admin-bar"' in html and 'class="admin-main"' in html
    # The first-day steps are one of the overlays it watches, at the top level.
    assert re.search(r'<div class="modal" id="first-day"', html)


def test_the_search_box_says_which_suggestion_is_picked():
    html = read(PACKAGE / "templates" / "index.html")
    search = html[html.index('<input id="search"'):]
    search = search[:search.index(">")]
    assert 'role="combobox"' in search and 'aria-autocomplete="list"' in search
    assert "li.id = `suggestion-${list.childElementCount}`;" in FILTERS_JS
    assert "input.setAttribute('aria-activedescendant', options[next].id);" in FILTERS_JS
    assert "input.setAttribute('aria-expanded', String(!list.hidden));" in FILTERS_JS


# --- contrast -----------------------------------------------------------------------------------


def test_show_to_on_the_selection_bar_is_readable():
    assert "var(--text-2)" in rule(STYLE, ".selection-bar .hint")
    wash = mix("#0969b0", LIGHT["bg-elev"], 0.10)
    assert contrast(LIGHT["text-2"], wash) >= 4.5
    assert contrast(LIGHT["text-3"], wash) < 4.5          # what it was


def test_the_details_labels_are_readable_on_the_panel_over_the_black_viewer():
    for selector in (".viewer-info dt",):
        assert "var(--text-2)" in rule(STYLE, selector)
    for selector in (".viewer-info h3", ".viewer-info h4"):
        assert "color: var(--text-2)" in rule(STYLE, selector)
    # 92% of --bg-elev over the viewer's near-black.
    panel = mix(LIGHT["bg-elev"], "#0b0d11", 0.92)
    assert contrast(LIGHT["text-2"], panel) >= 4.5
    assert contrast(DARK["text-2"], mix(DARK["bg-elev"], "#0b0d11", 0.92)) >= 4.5


def test_the_family_member_badge_is_readable_on_its_green_tint():
    body = rule(STYLE, ".role-family")
    colour = re.search(r"color:\s*(#[0-9a-fA-F]{6})", body).group(1)
    for under in ("#ffffff", LIGHT["bg"], LIGHT["surface-2"]):
        assert contrast(colour, mix("#2fbf71", under, 0.16)) >= 4.5, under


def test_the_jump_panels_year_counts_are_readable_on_the_current_years_wash():
    assert "var(--text-2)" in rule(STYLE, ".jump-year-btn small")
    assert contrast(LIGHT["text-2"], mix("#0969b0", "#ffffff", 0.10)) >= 4.5


# --- a mistyped address -------------------------------------------------------------------------


def test_a_page_that_is_not_there_says_so_in_both_languages_with_a_way_back(app):
    client = app.test_client()
    answer = client.get("/photos/an-old-bookmark")
    assert answer.status_code == 404 and answer.mimetype == "text/html"
    page = answer.get_data(as_text=True)
    assert "That page isn't here." in page and "அந்தப் பக்கம் இங்கே இல்லை." in page
    assert "<a href='/'>" in page and "color-scheme" in page
    # The API still answers in JSON.
    api = client.get("/api/no-such-thing")
    assert api.status_code == 404 and api.is_json


# --- follow-ups: the Control Panel's addresses from the keyboard; the share line in Tamil -------


class _FakeLabel:
    """Enough of tkinter.Label to build an address link without a display."""

    def __init__(self, parent, **options):
        self.options = options
        self.bindings = {}

    def bind(self, sequence, handler):
        self.bindings[sequence] = handler

    def configure(self, **options):
        self.options.update(options)


class _FakeTk:
    Label = _FakeLabel


def test_the_control_panels_addresses_open_from_the_keyboard():
    from ninaivu_lite import panel

    opened = []
    plain, underlined = object(), object()
    link = panel.address_link(_FakeTk, None, "http://192.168.1.20:8080/", (plain, underlined),
                              opener=opened.append)
    # In the Tab order, with a ring in the accent colour while it has the focus.
    assert link.options["takefocus"] == 1
    assert link.options["highlightthickness"] >= 1
    assert link.options["highlightcolor"] == panel.ACCENT
    # Enter, the keypad's Enter, Space and a click each open it once.
    for key in ("<Return>", "<KP_Enter>", "<space>", "<Button-1>"):
        assert link.bindings[key](None) == "break"
    assert opened == ["http://192.168.1.20:8080/"] * 4
    # Underlined while focused (or hovered), plain again after.
    link.bindings["<FocusIn>"](None)
    assert link.options["font"] is underlined
    link.bindings["<Enter>"](None)
    link.bindings["<FocusOut>"](None)
    assert link.options["font"] is underlined          # the mouse is still on it
    link.bindings["<Leave>"](None)
    assert link.options["font"] is plain
    source = read(PACKAGE / "panel.py")
    assert "link = address_link(tk, box, value, self.link_fonts)" in source


def test_the_control_panels_address_takes_the_tab_and_enter_in_real_tk():
    """The same, in a real window, where Tk and a display exist."""
    from test_panel import _tk_root

    from ninaivu_lite import panel

    root = _tk_root()
    try:
        import tkinter as tk
        import tkinter.font as tkfont
        opened = []
        fonts = (tkfont.Font(root=root, size=10), tkfont.Font(root=root, size=10, underline=True))
        first = panel.address_link(tk, root, "http://a/", fonts,
                                   opener=opened.append)
        first.pack()
        root.update()
        root.focus_force()
        first.focus_set()
        root.update()
        assert root.focus_get() is first and first.cget("font") == str(fonts[1])
        first.event_generate("<Return>")
        first.event_generate("<space>")
        root.update()
        assert opened == ["http://a/", "http://a/"]
        # Tab reaches it from another widget.
        before = tk.Button(root, text="x")
        before.pack(before=first)
        root.update()
        assert before.tk_focusNext() is first
    finally:
        root.destroy()


def test_the_share_pages_line_reads_as_tamil():
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    line = ta["Shared from {name}"]
    assert line == "{name} பகிர்ந்தவை"
    assert "இல் இருந்து" not in line
