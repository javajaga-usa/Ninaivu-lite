"""tests/test_preload_native_feel.py, in a real browser: a gallery opened
has its next few hundred thumbnails ready, the date rail gets its place
ready, each view comes back to its place, and the viewer opens on the sharp
thumbnail. Uses the smoke tests' server and Chromium, and skips the same way."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
from test_smoke_browser import Watched, browser, long_gallery, settle  # noqa: E402,F401
from ninaivu_lite import auth  # noqa: E402

def _page(browser, long_gallery, size=(1280, 800)):  # noqa: F811
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  service_workers="block")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    watched = Watched(context, long_gallery["base"])
    watched.page.goto(long_gallery["base"] + "/")
    # Each test from the top, whatever an earlier one left in this browser.
    watched.page.evaluate("try { localStorage.clear(); } catch {}")
    watched.page.reload()
    watched.page.wait_for_selector("#grid .cell img.ready")
    watched.page.evaluate("performance.setResourceTimingBufferSize(5000)")
    return context, watched


def test_an_opened_gallery_has_its_next_few_hundred_ready(browser, long_gallery):  # noqa: F811
    context, watched = _page(browser, long_gallery)
    page = watched.page
    try:
        # The 300 below the tiles on the page (the gallery has 360), fetched
        # into the browser's cache without being scrolled to.
        page.wait_for_function("""() => { const { grid } = window.__mv;
            return grid.prefetcher.done.size >= Math.min(300, 360 - grid.mounted.size) - 5; }""",
                               timeout=15000)
        assert page.evaluate("() => window.__mv.grid.scroller.scrollTop") == 0
        assert not watched.problems, watched.problems
    finally:
        context.close()


def test_the_date_rail_gets_its_place_ready(browser, long_gallery):  # noqa: F811
    context, watched = _page(browser, long_gallery)
    page = watched.page
    try:
        settle(page)
        found = page.evaluate("""async () => {
          const { grid } = window.__mv;
          const asked = [];
          grid.prefetchAround = ((own) => function (y) { asked.push(y); return own.call(this, y); })(grid.prefetchAround);
          const rail = document.querySelector('#scrubber');
          const box = rail.getBoundingClientRect();
          const at = { clientX: box.right - 5, clientY: box.top + box.height * 0.9,
                       pointerType: 'mouse', bubbles: true };
          rail.dispatchEvent(new PointerEvent('pointermove', at));
          await new Promise((r) => setTimeout(r, 300));
          const p = grid.prefetcher;
          return { asked, waiting: p.queue.length + p.active.size + p.done.size,
                   far: asked.length && asked[0] > grid.layout.height * 0.6 };
        }""")
        assert found["asked"] and found["far"] and found["waiting"] > 0, found
        assert not watched.problems, watched.problems
    finally:
        context.close()


def test_each_view_comes_back_to_its_place(browser, long_gallery):  # noqa: F811
    context, watched = _page(browser, long_gallery)
    page = watched.page
    try:
        settle(page)
        page.evaluate("() => { window.__mv.grid.scroller.scrollTop = 1500; }")
        page.wait_for_timeout(800)
        left = page.evaluate("() => window.__mv.grid.anchor()")
        assert left["id"] is not None
        page.click('[data-view="favorites"]')
        page.wait_for_timeout(800)
        page.click('[data-view="all"]')
        page.wait_for_function("(id) => window.__mv.grid.anchor()?.id === id", arg=left["id"],
                               timeout=5000)
        assert page.evaluate("() => window.__mv.grid.anchor()") == left
        page.reload()
        page.wait_for_selector("#grid .cell img.ready")
        page.wait_for_function("(id) => window.__mv.grid.anchor()?.id === id", arg=left["id"],
                               timeout=5000)
        assert not watched.problems, watched.problems
    finally:
        context.close()


def test_the_viewer_opens_on_the_sharp_thumbnail_in_the_photographs_box(browser, long_gallery):  # noqa: F811
    context, watched = _page(browser, long_gallery)
    page = watched.page
    try:
        settle(page)

        def slow(route):
            page.wait_for_timeout(2000)
            route.continue_()
        page.route("**/api/file/**", slow)
        page.route("**/api/view/**", slow)
        page.locator("#grid .cell").nth(2).click()
        page.wait_for_selector("#viewer:not([hidden])")
        page.wait_for_timeout(300)
        found = page.evaluate("""() => { const g = document.querySelector('.viewer-stage .placeholder');
          if (!g) return null;
          const main = [...document.querySelectorAll('.viewer-stage img')].find((i) => !i.classList.contains('placeholder'));
          const a = g.getBoundingClientRect(), b = main.getBoundingClientRect();
          return { sharp: g.classList.contains('sharp'), filter: getComputedStyle(g).filter,
                   shown: g.complete && g.naturalWidth > 0, thumb: g.getAttribute('src').includes('/api/thumb/'),
                   sameBox: ['left', 'top', 'width', 'height'].every((k) => Math.abs(a[k] - b[k]) < 1) }; }""")
        assert found == {"sharp": True, "filter": "none", "shown": True, "thumb": True,
                         "sameBox": True}, found
        page.wait_for_function("() => !document.querySelector('.viewer-stage .placeholder')",
                               timeout=8000)
        assert not watched.problems, watched.problems
    finally:
        context.close()
