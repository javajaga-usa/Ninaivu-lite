"""Desktop screens in a real browser (1280x800 and the Raspberry Pi's 800x480,
English and Tamil): what a seventh audit found on them after the gallery and
the console were split into modules. Each test failed on the code before its
fix. Uses the smoke tests' Chromium and server helper, and skips the same way
they do when Playwright is not installed."""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from conftest import make_jpeg, make_mp4
from test_smoke_browser import REQUIRED, browser, serve, settle  # noqa: F401

from ninaivu_lite import auth, db

pytest.importorskip("playwright.sync_api")

TAMIL = json.loads((Path(__file__).resolve().parent.parent / "ninaivu_lite" / "static" / "i18n"
                    / "ta.json").read_text(encoding="utf-8"))

#: One photograph a day, so the small days share rows: the top row of a wide
#: window starts on 20 September and ends in July.
DAYS = ["2019:09:20", "2019:08:17", "2019:08:14", "2019:07:26", "2019:06:24", "2019:06:02",
        "2019:05:09"]


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("desk")
    photos, data = tmp / "Photos", tmp / "data"
    for n, day in enumerate(DAYS):
        make_jpeg(photos / "2019" / f"day-{n}.jpg", f"{day} 10:00:00",
                  size=(480, 640) if n % 2 else (640, 480), color=(40 * n, 120, 200 - 20 * n))
    make_mp4(photos / "2019" / "clip.mp4", datetime(2019, 4, 2, 9, 0, 0))
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase", name="Appa",
                             role="admin")
    auth.create_user(conn, "amma", name="Amma", role="family")
    token = auth.start_session(conn, admin.id)
    conn.close()
    with serve(tmp, photos, data, len(DAYS) + 1) as (base, _ids):
        yield {"base": base, "token": token, "data": data}


@contextlib.contextmanager
def opened(browser, desk, lang="en", size=(1280, 800), path="/"):  # noqa: F811
    """A signed-in page in *lang* at *size*, its script errors kept."""
    conn = db.connect(desk["data"])
    # The language follows the person (a test that switches it saves it).
    conn.execute("UPDATE users SET language = NULL")
    conn.commit()
    conn.close()
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  locale="ta-IN" if lang == "ta" else "en-GB",
                                  service_workers="block")
    context.add_init_script(f"try {{ localStorage.setItem('ninaivu.lang', '{lang}'); }} catch {{}}")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": desk["token"],
                          "url": desk["base"]}])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        if path is not None:
            page.goto(desk["base"] + path)
            if path == "/":
                page.wait_for_selector("#grid .cell img.ready")
            settle(page)
        yield page, errors
        assert not errors, errors
    finally:
        context.close()


def pick(page, n=0):
    """Tick a tile the way a mouse does: over it, then on its tick."""
    box = page.locator("#grid .cell").nth(n).bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    page.wait_for_timeout(100)
    tick = page.locator("#grid .cell").nth(n).locator(".pick").bounding_box()
    page.mouse.click(tick["x"] + tick["width"] / 2, tick["y"] + tick["height"] / 2)
    page.wait_for_timeout(300)


def toasts(page):
    page.evaluate("""() => { window.__toasts = [];
      new MutationObserver((records) => { for (const r of records) for (const n of r.addedNodes)
        if (n.nodeType === 1) window.__toasts.push(n.textContent); })
      .observe(document.querySelector('#toasts'), { childList: true }); }""")


# DESK1 -----------------------------------------------------------------------

def test_jump_to_names_the_month_the_top_row_starts_with(browser, desk):  # noqa: F811
    with opened(browser, desk) as (page, _errors):
        heads = page.evaluate("""() => window.__mv.grid.layout.headers.slice(0, 4)
            .map((h) => [h.key, h.y])""")
        # The first row holds several small days, September first.
        assert heads[0][0].startswith("2019-09") and heads[1][1] == heads[0][1], heads
        label = page.text_content("#jump-label")
        assert label.startswith("Sep"), label          # was "Jul 2019", the row's last day


# DESK2 -----------------------------------------------------------------------

def test_a_narrow_day_shows_its_whole_count_and_a_tick_in_tamil(browser, desk):  # noqa: F811
    def narrow_heads(page):
        return page.evaluate("""() => [...document.querySelectorAll('.section-head.packed')]
          .filter((h) => h.getBoundingClientRect().width <= 260).map((h) => {
            const sub = h.querySelector('.sub'), button = h.querySelector('[data-select-section]');
            return { sub: sub.textContent, cut: sub.scrollWidth > sub.clientWidth,
                     tick: !!button.querySelector('svg') && getComputedStyle(button.querySelector('svg')).display !== 'none',
                     label: button.querySelector('.label')?.textContent || null,
                     name: button.getAttribute('aria-label') };
          })""")

    with opened(browser, desk, "ta") as (page, _errors):
        heads = narrow_heads(page)
        assert heads, "no narrow day on the screen"
        for head in heads:
            # The count whole ("1 உருப்படி", was "1 …") beside the tick alone.
            assert not head["cut"], head
            assert head["tick"] and head["label"] == TAMIL["Select all"], head
            assert head["name"] == TAMIL["Select all"], head
        # A change of language names the button again without taking its tick.
        page.click("#lang-btn")
        page.wait_for_timeout(800)
        for head in narrow_heads(page):
            assert head["tick"] and head["label"] == "Select all" and not head["cut"], head


# DESK3 -----------------------------------------------------------------------

def test_a_new_language_redraws_the_selection_count_and_the_profile_button(browser, desk):  # noqa: F811
    with opened(browser, desk, "en") as (page, _errors):
        pick(page)
        assert page.text_content("#sel-count") == "1 selected"
        page.click("#lang-btn")
        page.wait_for_timeout(800)
        assert page.text_content("#sel-count") == TAMIL["{count} selected"].replace("{count}", "1")
        title = page.get_attribute("#profile-btn", "title")
        assert title == f"Appa · {TAMIL['Administrator']}", title
        label = page.get_attribute("#profile-btn", "aria-label")
        assert label.startswith(TAMIL["Profile and settings"]), label
        assert page.get_attribute("#grid .cell .pick", "aria-label") == TAMIL["Select"]


# DESK4 -----------------------------------------------------------------------

def test_the_theme_key_says_which_theme_in_the_page_language(browser, desk):  # noqa: F811
    with opened(browser, desk, "ta") as (page, _errors):
        toasts(page)
        page.evaluate("() => document.activeElement.blur()")
        for _ in range(3):
            page.keyboard.press("t")
            page.wait_for_timeout(150)
        said = page.evaluate("window.__toasts")
        # Round the three, each named in Tamil (was "தோற்றம்: dark").
        assert sorted(said) == sorted([TAMIL["Dark theme"], TAMIL["Light theme"],
                                       TAMIL["The theme follows this device"]]), said


# DESK5 -----------------------------------------------------------------------

def test_the_selection_bar_is_one_line_on_the_pi_screen(browser, desk):  # noqa: F811
    for lang in ("en", "ta"):
        with opened(browser, desk, lang, size=(800, 480)) as (page, _errors):
            pick(page)
            bar = page.evaluate("""() => { const b = document.querySelector('#selection-bar');
                const a = b.querySelector('.sel-actions'), c = b.querySelector('#sel-count');
                return { height: b.getBoundingClientRect().height, count: c.getBoundingClientRect().height,
                         grid: document.querySelector('#scroller').getBoundingClientRect().height,
                         overflows: a.scrollWidth > a.clientWidth }; }""")
            # One line (was three: 119 px, leaving the photographs 231 px).
            assert bar["height"] < 64 and bar["count"] < 30, (lang, bar)
            # What does not fit scrolls sideways, and the mouse wheel does it.
            assert bar["overflows"], (lang, bar)
            box = page.locator(".sel-actions").bounding_box()
            page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2)
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(200)
            assert page.evaluate("document.querySelector('.sel-actions').scrollLeft") > 0
    # A wide window keeps the bar as it was.
    with opened(browser, desk, "en") as (page, _errors):
        pick(page)
        assert page.evaluate("getComputedStyle(document.querySelector('.sel-actions')).display") \
            == "contents"


# DESK6 -----------------------------------------------------------------------

def test_the_folder_pickers_shortcuts_are_named_in_tamil(browser, desk):  # noqa: F811
    with opened(browser, desk, "ta", path="/admin") as (page, _errors):
        page.wait_for_selector(".panel.active[data-panel='overview']")
        page.evaluate("() => document.querySelector(`#tabs button[data-tab='library']`).click()")
        page.click("#change-root")
        page.wait_for_selector("#folder-modal:not([hidden]) .fm-shortcuts .chip")
        chips = page.evaluate("() => [...document.querySelectorAll('.fm-shortcuts .chip')]"
                              ".map((c) => c.textContent)")
        assert TAMIL["{name} (current)"].replace("{name}", "Photos") in chips, chips
        assert TAMIL["Home folder"] in chips, chips
        assert not [c for c in chips if re.search(r"\(current\)|^Home$|: drive$", c)], chips


# DESK7 -----------------------------------------------------------------------

def test_the_overview_counts_one_video_as_one_video(browser, desk):  # noqa: F811
    with opened(browser, desk, "en", path="/admin") as (page, _errors):
        page.wait_for_selector("#overview-cards .card-sub")
        assert page.text_content("#overview-cards .card-sub") == "7 photos · 1 video"


# DESK8 -----------------------------------------------------------------------

def test_the_viewer_shows_no_arrow_past_either_end(browser, desk):  # noqa: F811
    def arrows(page):
        return page.evaluate("""() => ['#v-prev', '#v-next'].map((id) =>
            getComputedStyle(document.querySelector(id)).display !== 'none')""")

    with opened(browser, desk, "en") as (page, _errors):
        page.locator("#grid .cell").first.click()
        page.wait_for_selector("#viewer:not([hidden]) #viewer-stage img")
        assert arrows(page) == [False, True]        # the left one did nothing here
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(300)
        assert arrows(page) == [True, True]
        page.keyboard.press("End")
        last = page.evaluate("window.__mv.grid.ids.length - 1")
        page.evaluate("(n) => window.__mv.viewer.goTo(n)", last)
        page.wait_for_timeout(300)
        assert arrows(page) == [True, False]


# DESK9 -----------------------------------------------------------------------

WALKING = json.dumps({
    "running": True, "uses": ["disk"],
    "jobs": [{"id": "indexing", "title": "Looking for files", "detail": "12,000", "percent": None,
              "eta": None, "paused": False, "uses": ["disk"], "page": "library"}],
    "scan": {"running": True, "status": "walking", "percent": None, "processed": 12000,
             "total": 0, "message": "", "folder": "", "eta": None, "added": 0, "removed": 0,
             "tagged": 0, "tag_total": 0, "unreachable": [], "unreadable": 0}})


def test_the_library_bar_does_not_count_against_nothing_while_folders_are_walked(browser, desk):  # noqa: F811
    with opened(browser, desk, "en", path=None) as (page, _errors):
        page.route("**/api/status/activity", lambda route: route.fulfill(
            status=200, content_type="application/json", body=WALKING))
        page.goto(desk["base"] + "/admin")
        page.wait_for_selector(".panel.active[data-panel='overview']")
        page.evaluate("() => document.querySelector(`#tabs button[data-tab='library']`).click()")
        page.wait_for_selector("#scan-strip:not([hidden])")
        text = page.text_content("#scan-text")
        assert "/ 0" not in text and "12,000" in text, text    # was "Indexing 12,000 / 0"
        assert text.startswith("Looking for files"), text
