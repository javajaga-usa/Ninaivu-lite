"""Phone touch behaviour in a real browser with phone emulation (touch, 3x
pixels): the Back button with a photograph open, a tap that stops a glide
of the grid, and the selection bar's buttons under a finger. Uses the smoke
tests' server and Chromium, and skips the same way they do."""

from __future__ import annotations

import pytest
from test_smoke_browser import REQUIRED, browser, long_gallery, server, settle  # noqa: F401
from ninaivu_lite import auth

pytest.importorskip("playwright.sync_api")


def phone(browser, where, width=390, height=844):  # noqa: F811
    context = browser.new_context(viewport={"width": width, "height": height},
                                  device_scale_factor=3, is_mobile=True, has_touch=True,
                                  service_workers="block")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": where["token"],
                          "url": where["base"]}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, errors


def tap(touch, x, y):
    touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def test_back_closes_the_photograph_not_the_gallery(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server)
    try:
        page.goto(server["base"] + "/healthz")
        page.goto(server["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        page.locator("#grid .cell").first.tap()
        page.wait_for_selector("#viewer:not([hidden])")
        # PHONE1: Back went to the page before the gallery.
        page.go_back()
        page.wait_for_timeout(400)
        assert page.url == server["base"] + "/", page.url
        assert page.is_hidden("#viewer")
        # Closed by its own button, the photograph leaves no entry behind:
        # Back from the gallery leaves it, as before.
        page.locator("#grid .cell").nth(1).tap()
        page.wait_for_selector("#viewer:not([hidden])")
        page.tap("#v-close")
        page.wait_for_timeout(400)
        assert page.is_hidden("#viewer")
        assert page.url == server["base"] + "/", page.url
        page.go_back()
        page.wait_for_timeout(400)
        assert page.url.endswith("/healthz"), page.url
        assert not errors, errors
    finally:
        context.close()


# The grid glides on after a flick: a scroll every frame for 700 ms, as a
# fling runs. Returns once it has stopped.
GLIDE = """() => new Promise((done) => {
  const scroller = window.__mv.grid.scroller, started = performance.now();
  const step = (now) => {
    scroller.scrollTop += 6;
    if (now - started < 700) requestAnimationFrame(step); else done();
  };
  requestAnimationFrame(step);
})"""

# Where a tap lands on a photograph: the middle of the largest one on screen.
TARGET = """() => {
  let best = null;
  for (const cell of document.querySelectorAll('#grid .cell')) {
    const r = cell.getBoundingClientRect();
    if (r.top < 150 || r.bottom > innerHeight - 60) continue;
    if (!best || r.width * r.height > best.area) {
      best = { x: r.left + r.width / 2, y: r.top + r.height / 2, area: r.width * r.height };
    }
  }
  return best;
}"""


def test_a_tap_that_stops_a_glide_opens_nothing(browser, long_gallery):  # noqa: F811
    context, page, errors = phone(browser, long_gallery)
    try:
        page.goto(long_gallery["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        page.evaluate("""() => { window.__clicked = [];
            document.addEventListener('click', (e) => window.__clicked.push(!!e.target.closest('.cell')), true); }""")
        touch = context.new_cdp_session(page)
        page.evaluate(f"() => {{ window.__glide = ({GLIDE})(); }}")
        page.wait_for_timeout(250)
        spot = page.evaluate(TARGET)
        assert spot, "no photograph on screen"
        tap(touch, spot["x"], spot["y"])
        page.wait_for_timeout(100)
        clicked = page.evaluate("window.__clicked")
        page.evaluate("() => window.__glide")
        page.wait_for_timeout(400)
        # The tap reached a photograph mid-glide (the test means something)...
        assert clicked == [True], clicked
        # ...and PHONE2: it only stopped the glide; nothing opened.
        assert page.is_hidden("#viewer")
        # At rest, the next tap opens the photograph under the finger.
        spot = page.evaluate(TARGET)
        under = page.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('.cell').dataset.id",
                              [spot["x"], spot["y"]])
        page.evaluate("""() => { window.__opened = null; window.__mv.grid.addEventListener(
            'open', (e) => { window.__opened = String(e.detail.id); }, { once: true }); }""")
        tap(touch, spot["x"], spot["y"])
        page.wait_for_selector("#viewer:not([hidden])")
        opened = page.evaluate("window.__opened")
        assert opened == under, (opened, under)
        assert not errors, errors
    finally:
        context.close()


def test_the_selection_actions_take_a_finger_sized_tap(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server, 360, 740)
    try:
        page.goto(server["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        first = page.evaluate("""() => { const r = document.querySelector('#grid .cell').getBoundingClientRect();
            return { x: r.left + 20, y: r.top + 20 }; }""")
        touch = context.new_cdp_session(page)
        touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [first]})
        page.wait_for_timeout(700)
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_selector("#selection-bar:not([hidden])")
        page.wait_for_timeout(300)
        found = page.evaluate("""() => {
          const bar = document.querySelector('#selection-bar').getBoundingClientRect();
          const out = { bar: bar.height, buttons: [] };
          for (const b of document.querySelectorAll('.sel-actions button')) {
            const r = b.getBoundingClientRect();
            if (!r.width || r.right > innerWidth) continue;
            const at = (y) => { const e = document.elementFromPoint(r.left + r.width / 2, y);
                                return !!e && (e === b || b.contains(e)); };
            // 7 px above and below a 28 px button: inside a 44 px target.
            out.buttons.push({ id: b.id, height: r.height, above: at(r.top - 7), below: at(r.bottom + 7) });
          }
          return out; }""")
        assert found["buttons"], found
        for button in found["buttons"]:
            # PHONE3: the sideways row clipped the target to the 28 px drawn.
            assert button["above"] and button["below"], button
        # Nothing drawn moved: the bar is as tall as before (M63's one line).
        assert found["bar"] <= 92, found
        assert not errors, errors
    finally:
        context.close()
