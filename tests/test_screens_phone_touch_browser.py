"""Phone touch behaviour in a real browser with phone emulation (touch, 3x
pixels): the Back button with a photograph open, a tap that stops a glide
of the grid, the selection bar's and the console's buttons under a finger,
and the first-day steps on a small phone. Uses the smoke tests' server and
Chromium, and skips the same way they do."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request

import pytest
from test_smoke_browser import (REQUIRED, ROOT, browser, free_port, long_gallery,  # noqa: F401
                                server, settle)
from ninaivu_lite import auth, db

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
        # A dialog over the photograph keeps it: Back leaves both in place.
        page.locator("#grid .cell").nth(1).tap()
        page.wait_for_selector("#viewer:not([hidden])")
        page.tap("#v-share")
        page.wait_for_selector("#share-modal:not([hidden])")
        page.go_back()
        page.wait_for_timeout(400)
        assert page.is_visible("#viewer") and page.is_visible("#share-modal")
        page.tap("#share-close")
        page.tap("#v-close")
        page.wait_for_timeout(400)
        page.go_back()
        page.wait_for_timeout(400)
        assert page.url.endswith("/healthz"), page.url
        assert not errors, errors
    finally:
        context.close()


def test_back_closes_the_drawer_not_the_gallery(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server)
    drawer_open = "document.querySelector('.shell').classList.contains('mobile-open')"
    try:
        page.goto(server["base"] + "/healthz")
        page.goto(server["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        start = page.evaluate("history.length")
        page.tap("#sidebar-toggle")
        page.wait_for_timeout(300)
        assert page.evaluate(drawer_open)
        # PHONE6: Back went to the page before the gallery.
        page.go_back()
        page.wait_for_timeout(400)
        assert page.url == server["base"] + "/", page.url
        assert not page.evaluate(drawer_open)
        # Closed by a pick in it, then the photograph opened and closed: one
        # entry each came and went, and Back leaves the gallery as before.
        page.tap("#sidebar-toggle")
        page.wait_for_timeout(300)
        page.tap(".side-item[data-view='all']")
        page.wait_for_timeout(500)
        assert not page.evaluate(drawer_open)
        page.locator("#grid .cell").first.tap()
        page.wait_for_selector("#viewer:not([hidden])")
        page.tap("#v-close")
        page.wait_for_timeout(400)
        # Never more than the one entry above the gallery's own.
        assert page.evaluate("history.length") == start + 1, page.evaluate("history.length")
        assert not page.evaluate("history.state?.viewer || history.state?.drawer")
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
        # A jump to a place (one scroll, as Jump to or a tile brought into
        # view makes) just before is no glide: the tap still opens.
        page.evaluate("window.__mv.grid.scroller.scrollTop += 1")
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


def test_the_console_section_buttons_take_a_finger_sized_tap(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server, 360, 740)
    try:
        page.goto(server["base"] + "/admin")
        page.wait_for_selector(".panel.active")
        settle(page)
        found = page.evaluate("""() => [...document.querySelectorAll('#tab-groups button')].map((b) => {
          const r = b.getBoundingClientRect();
          if (r.right > innerWidth) return null;
          const at = (y) => { const e = document.elementFromPoint(r.left + r.width / 2, y);
                              return !!e && (e === b || b.contains(e)); };
          // 4 px above and below a 34 px button: inside a 44 px target.
          return { group: b.dataset.group, above: at(r.top - 4), below: at(r.bottom + 4) };
        }).filter(Boolean)""")
        assert len(found) >= 3, found
        for button in found:
            # PHONE4: the sideways row cut the target off at its top edge.
            assert button["above"] and button["below"], button
        assert not errors, errors
    finally:
        context.close()


def test_the_year_chips_take_a_finger_sized_tap(browser, server):  # noqa: F811
    context, page, errors = phone(browser, server, 360, 740)
    try:
        page.goto(server["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        # The strip shows with two years or more; this library has one, so
        # the strip is given two chips as filters.js draws them.
        found = page.evaluate("""() => {
          const strip = document.querySelector('#year-strip');
          strip.innerHTML = '<button class="chip">2024</button><button class="chip">2023</button>';
          strip.hidden = false;
          const chip = strip.querySelector('.chip'), r = chip.getBoundingClientRect();
          const at = (y) => { const e = document.elementFromPoint(r.left + r.width / 2, y);
                              return !!e && (e === chip || chip.contains(e)); };
          // 5 px above and below a 33 px chip: inside a 44 px target.
          return { height: r.height, strip: strip.offsetHeight,
                   above: at(r.top - 5), below: at(r.bottom + 5) }; }""")
        # PHONE7: the sideways strip stopped the target 2 px under the chip.
        assert found["above"] and found["below"], found
        assert found["strip"] <= 47, found      # was 43: a little taller only
        assert not errors, errors
    finally:
        context.close()


@pytest.fixture(scope="module")
def first_day(tmp_path_factory):
    """`python -m ninaivu_lite` with no library folder yet and an
    administrator: the console opens on the first-day steps."""
    tmp = tmp_path_factory.mktemp("first-day")
    data = tmp / "data"
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase", name="Appa",
                             role="admin")
    token = auth.start_session(conn, admin.id)
    conn.close()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with open(tmp / "server.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ninaivu_lite", "--no-browser", "--host", "127.0.0.1",
             "--port", str(port), "--data", str(data)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=2).close()
                    break
                except OSError:
                    time.sleep(0.3)
            yield {"base": base, "token": token}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_the_first_day_steps_show_their_own_buttons_on_a_small_phone(browser, first_day):  # noqa: F811
    context, page, errors = phone(browser, first_day, 320, 568)
    context.add_init_script("try { localStorage.setItem('ninaivu.lang', 'ta'); } catch {}")
    try:
        page.goto(first_day["base"] + "/admin")
        page.wait_for_selector("#first-day:not([hidden]) #fd-body button")
        settle(page)
        out_of_sight = """() => { const body = document.querySelector('#fd-body').getBoundingClientRect();
          return [...document.querySelectorAll('#fd-body button, #fd-body input')]
            .filter((b) => b.offsetWidth && b.getBoundingClientRect().bottom > body.bottom + 1)
            .map((b) => b.textContent.trim() || b.placeholder); }"""
        assert page.evaluate("document.documentElement.lang") == "ta"
        # PHONE5: "Choose a folder" sat 60 px below the step's little scroll
        # (Tamil, 320 px), the household's Name, PIN and Add 60 px too.
        assert page.evaluate(out_of_sight) == []
        page.click("#fd-next")
        page.click("#fd-next")
        page.wait_for_selector("#fd-body form")
        page.wait_for_timeout(200)
        assert page.evaluate(out_of_sight) == []
        # Back and Next are still on the card (M72).
        bottom = page.evaluate("document.querySelector('#fd-next').getBoundingClientRect().bottom")
        assert bottom <= 568, bottom
        assert not errors, errors
    finally:
        context.close()
