"""Phone fixes from the 2026-10-11 audit, in a real browser with phone
emulation (touch, 2x pixels): the viewer's bar on a 320 px phone and a long
press in the grid. Uses the smoke tests' server and Chromium, and skips the
same way they do when Playwright is not installed."""

from __future__ import annotations

import pytest
from test_smoke_browser import REQUIRED, browser, server, sign_in  # noqa: F401

pytest.importorskip("playwright.sync_api")


def phone(browser, server, width, height):  # noqa: F811
    context = browser.new_context(viewport={"width": width, "height": height},
                                  device_scale_factor=2, is_mobile=True, has_touch=True,
                                  locale="ta-IN", service_workers="block")
    context.add_init_script("try { localStorage.setItem('ninaivu.lang', 'ta'); } catch {}")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    class Look:
        pass
    look = Look()
    look.page, look.errors = page, errors
    sign_in(look, server)
    return context, page, errors


def settle(page, ms=400):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def test_the_viewer_bar_fits_a_320px_phone(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server, 320, 568)
    try:
        page.goto(server["base"] + "/")
        settle(page)
        page.locator(".cell").first.tap()
        page.wait_for_selector("#viewer:not([hidden])")
        page.wait_for_timeout(300)
        buttons = page.evaluate("""() => [...document.querySelectorAll('.viewer-tools > .icon-btn')]
            .filter((b) => b.offsetWidth).map((b) => {
              const r = b.getBoundingClientRect();
              return { id: b.id, width: r.width, right: r.right };
            })""")
        assert len(buttons) >= 5, buttons
        for button in buttons:
            assert button["width"] >= 30, button      # was 20 px for Favourite, Share, Details
            assert button["right"] <= 320, button     # and Close stays on the screen
        assert not errors
    finally:
        context.close()


def test_a_long_press_selects_without_moving_the_photograph(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server, 360, 740)
    try:
        page.goto(server["base"] + "/")
        settle(page)
        first = page.evaluate("""() => { const r = document.querySelectorAll('.cell')[0].getBoundingClientRect();
            return { x: r.left + 20, y: r.top + 20 }; }""")
        touch = context.new_cdp_session(page)
        touch.send("Input.dispatchTouchEvent",
                   {"type": "touchStart", "touchPoints": [{"x": first["x"], "y": first["y"]}]})
        page.wait_for_timeout(700)
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(600)
        assert page.is_visible("#selection-bar")
        assert not page.is_visible("#viewer")
        # The selection bar took its height above the grid; the grid scrolled
        # by as much, so the photograph is still under the finger.
        now = page.evaluate("""() => document.querySelectorAll('.cell')[0]
            .getBoundingClientRect().top + 20""")
        assert abs(now - first["y"]) <= 2, (now, first["y"])
        # And the next tap adds a photograph instead of being eaten.
        second = page.evaluate("""() => { const s = document.querySelector('#scroller').getBoundingClientRect();
            for (const c of [...document.querySelectorAll('.cell')].slice(1)) {
              const r = c.getBoundingClientRect();
              if (r.top > s.top && r.top + 40 < innerHeight) return { x: r.left + 20, y: r.top + 20 };
            } }""")
        assert second, "no second photograph on screen"
        page.touchscreen.tap(second["x"], second["y"])
        page.wait_for_timeout(400)
        assert page.evaluate("document.querySelectorAll('.cell.selected').length") == 2
        assert not page.is_visible("#viewer")
        assert not errors
    finally:
        context.close()
