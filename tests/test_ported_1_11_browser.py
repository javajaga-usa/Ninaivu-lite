"""Fixes from Ninaivu 1.11.0, in a real browser: the date rail dragged fetches
pictures for where it is let go, not for every month it passes; the rail spans
the whole library as soon as the gallery opens; the sidebar fills in at sign-in
even when its first answers fail; and hiding the photograph open in the viewer
shows on its tile. Uses the smoke tests' server and Chromium, and skips the
same way they do."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pytest

pytest.importorskip("playwright.sync_api")
from test_smoke_browser import Watched, browser, call, long_gallery, settle  # noqa: E402,F401
from ninaivu_lite import auth  # noqa: E402


def _context(browser, long_gallery, size=(1280, 800)):  # noqa: F811
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  service_workers="block")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    return context


def _open(context, long_gallery):  # noqa: F811
    watched = Watched(context, long_gallery["base"])
    watched.page.goto(long_gallery["base"] + "/")
    watched.page.evaluate("try { localStorage.clear(); } catch {}")
    watched.page.reload()
    watched.page.wait_for_selector("#grid .cell img.ready")
    return watched


def test_dragging_the_rail_fetches_pictures_where_it_is_let_go(browser, long_gallery):  # noqa: F811
    """While the handle moves the grid shows each tile's colour and starts no
    picture; let go, the screen it stopped on gets its pictures."""
    context = _context(browser, long_gallery)
    watched = _open(context, long_gallery)
    page = watched.page
    try:
        settle(page)
        # Every picture a tile is pointed at while the handle moves, and every
        # tile left waiting for its picture.
        page.evaluate("""() => { const { grid } = window.__mv;
          window.__moving = { started: 0, waited: 0 };
          new MutationObserver((records) => { if (!grid.deferPictures()) return;
            for (const r of records) if (r.target.closest('#grid') && r.target.getAttribute('src')) window.__moving.started += 1; })
            .observe(document.querySelector('#grid'), { subtree: true, attributes: true, attributeFilter: ['src'] });
          const look = () => { if (grid.deferPictures()) {
              for (const node of grid.mounted.values()) if (node.deferred) window.__moving.waited += 1; }
            requestAnimationFrame(look); };
          requestAnimationFrame(look); }""")
        page.hover("#scroller")
        thumb = page.locator("#scrubber-thumb").bounding_box()
        rail = page.locator("#scrubber").bounding_box()
        x, y = thumb["x"] + thumb["width"] - 6, thumb["y"] + thumb["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        steps = 24
        for i in range(1, steps + 1):
            page.mouse.move(x, y + (rail["height"] * 0.85) * i / steps)
            page.wait_for_timeout(16)
        during = page.evaluate("""() => ({ scrubbing: window.__mv.grid.scrubbing,
          top: window.__mv.grid.scroller.scrollTop, ...window.__moving })""")
        page.mouse.up()
        assert during["scrubbing"] and during["top"] > 1000, during
        assert during["waited"] > 0, during
        assert during["started"] == 0, during
        # The screen it stopped on: every tile there gets its picture.
        page.wait_for_function("""() => { const s = document.querySelector('#scroller').getBoundingClientRect();
          const on = [...document.querySelectorAll('#grid .cell')].filter((c) => {
            const r = c.getBoundingClientRect(); return r.bottom > s.top && r.top < s.bottom; });
          return on.length && on.every((c) => c.querySelector('img.ready')); }""", timeout=15000)
        assert not page.evaluate("window.__mv.grid.scrubbing")
        assert not watched.problems, watched.problems
    finally:
        context.close()


def test_the_rail_covers_the_whole_library_without_scrolling(browser, long_gallery):  # noqa: F811
    """The gallery arrives in pieces; the rest is asked for as soon as the first
    is on screen, so the rail spans every month from the start."""
    context = _context(browser, long_gallery)
    pieces: list[int] = []

    def small_first_piece(route):
        url = urlparse(route.request.url)
        query = parse_qs(url.query)
        offset = int(query.get("offset", ["0"])[0])
        pieces.append(offset)
        if not offset:
            query["limit"] = ["100"]
            url = url._replace(query=urlencode(query, doseq=True))
        route.continue_(url=urlunparse(url))

    context.route("**/api/segments?*", small_first_piece)
    watched = _open(context, long_gallery)
    page = watched.page
    try:
        page.wait_for_function("() => window.__mv.grid.layout.cells.length === 360", timeout=15000)
        assert any(offset > 0 for offset in pieces), pieces
        assert page.evaluate("document.querySelector('#scroller').scrollTop") < 50
        # The oldest month (January) is on the rail.
        months = page.evaluate("""() => [...document.querySelectorAll('#scrubber-track span')]
          .map((s) => s.textContent)""")
        assert any("Jan" in m or "2020" in m for m in months[-2:]), months
        assert not watched.problems, watched.problems
    finally:
        context.close()


def test_the_sidebar_fills_in_when_its_first_answers_fail(browser, long_gallery):  # noqa: F811
    """At sign-in the first answer for the year/folder lists and for albums
    fails, as a busy or restarting server answers; both are asked again."""
    call(long_gallery["base"], "/api/albums", long_gallery["token"], {"name": "Pongal"})
    context = _context(browser, long_gallery)
    failed: set[str] = set()
    asked: dict[str, int] = {}

    def first_fails(route):
        path = urlparse(route.request.url).path
        asked[path] = asked.get(path, 0) + 1
        if route.request.method == "GET" and path not in failed:
            failed.add(path)
            route.fulfill(status=503, content_type="application/json",
                          body=json.dumps({"error": "busy"}))
            return
        route.continue_()

    context.route("**/api/facets*", first_fails)
    context.route("**/api/albums", first_fails)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(long_gallery["base"] + "/")
        page.wait_for_selector("#grid .cell")
        page.wait_for_function("() => document.querySelectorAll('#album-list .album-card, "
                               "#album-list button').length > 0", timeout=15000)
        page.wait_for_function("() => (window.__mv.state.facets?.folders || []).length > 0",
                               timeout=15000)
        assert failed == {"/api/facets", "/api/albums"}, failed
        assert all(2 <= n <= 3 for n in asked.values()), asked
        assert not errors, errors
    finally:
        context.close()


def test_hiding_the_photograph_in_the_viewer_shows_on_its_tile(browser, long_gallery):  # noqa: F811
    """Choosing Hidden in the viewer marks its tile at once, and closing the
    viewer brings the Hidden view up to date; the same choice made for a
    selection always did."""
    context = _context(browser, long_gallery)
    watched = _open(context, long_gallery)
    page = watched.page
    try:
        settle(page)
        cell = page.locator("#grid .cell").first
        asset = cell.get_attribute("data-id")
        badge = f'#grid .cell[data-id="{asset}"] .badge.vis.is-hidden'
        assert page.locator(badge).count() == 0
        cell.click()
        page.wait_for_selector("#viewer:not([hidden])", timeout=15000)
        page.evaluate("document.querySelector('#v-visibility button[data-vis=\"hidden\"]').click()")
        page.wait_for_selector(badge, state="attached", timeout=15000)
        page.keyboard.press("Escape")
        page.wait_for_selector("#viewer", state="hidden")
        page.click('[data-view="hidden"]')
        page.wait_for_selector(f'#grid .cell[data-id="{asset}"]', timeout=20000)
        # Back to the family from the Hidden view: it leaves when the viewer closes.
        page.locator(f'#grid .cell[data-id="{asset}"]').click()
        page.wait_for_selector("#viewer:not([hidden])", timeout=15000)
        page.evaluate("document.querySelector('#v-visibility button[data-vis=\"family\"]').click()")
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_selector(f'#grid .cell[data-id="{asset}"]', state="detached", timeout=20000)
        assert not watched.problems, watched.problems
    finally:
        context.close()
