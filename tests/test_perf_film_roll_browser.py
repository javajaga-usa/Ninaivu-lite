"""The film-roll scroll (grid.js) on a phone with a Retina density, where every
tile wants the sharp 640 px thumbnail: the sharp pictures fetched on the side
for tiles that took the small one while the scroll went on stop when a photo
is opened over the grid, as the rest of the fetching ahead does (PERF2).
Uses the smoke tests' server and Chromium, and skips the same way they do."""

from __future__ import annotations

import pytest
from test_smoke_browser import browser, long_gallery, settle  # noqa: F401
from ninaivu_lite import auth

pytest.importorskip("playwright.sync_api")

# Every picture asked for with `new Image()` (grid.js's prefetcher and its
# side fetches for sharpening both use one), so a test can tell them from a
# tile's own <img>.
SIDE = """
window.__sideUrls = new Set();
const Real = window.Image;
window.Image = function (...args) {
  const image = new Real(...args);
  const own = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  Object.defineProperty(image, 'src', { configurable: true, get() { return own.get.call(this); },
    set(v) { window.__sideUrls.add(new URL(v, location.href).href); own.set.call(this, v); } });
  return image;
};
"""


def phone(browser, long_gallery):  # noqa: F811
    context = browser.new_context(viewport={"width": 390, "height": 780},
                                  device_scale_factor=3, service_workers="block")
    context.add_init_script(SIDE)
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(long_gallery["base"] + "/")
    page.wait_for_selector("#grid .cell img.ready")
    settle(page)
    return context, page, errors


SCROLL = """async ([pixels, ms]) => {
  const { grid } = window.__mv;
  const before = new Set([...grid.mounted.keys()]);
  const made = [];
  const started = performance.now();
  const from = grid.scroller.scrollTop;
  await new Promise((done) => {
    const step = (now) => {
      grid.scroller.scrollTop = from + pixels * Math.min(1, (now - started) / ms);
      for (const [n, node] of grid.mounted) {
        if (!before.has(n)) { before.add(n); made.push(node.img.getAttribute('src')); }
      }
      if (now - started < ms) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  });
  return made;
}"""


def test_an_open_photo_stops_the_sharpening_fetches(browser, long_gallery):  # noqa: F811
    """A photo opened just after a scroll stopped waited behind the sharp
    pictures being fetched for the tiles under it (850 KB on a phone, over a
    250 ms link: the photo took 0.74 s instead of 0.62 s). They stop with
    the rest of the fetching ahead (A224), and once the photo is closed the
    tiles are sharpened after all."""
    context, page, errors = phone(browser, long_gallery)
    finished, failed, asked = [], [], []
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        # A slow link: what is asked for is still coming when the photo opens.
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 1500, "downloadThroughput": 2_000_000,
            "uploadThroughput": 1_000_000})
        page.on("request", lambda r: asked.append(r.url) if "/api/thumb/" in r.url else None)
        page.on("requestfinished", lambda r: finished.append(r.url))
        page.on("requestfailed", lambda r: failed.append(r.url))
        page.evaluate(SCROLL, [1800, 500])
        # The scroll stops (and the tiles it made, holding the small picture,
        # are sharpened on the side); a moment later a photo is opened.
        page.wait_for_function("() => !window.__mv.grid.scrolling")
        page.wait_for_timeout(100)
        side = set(page.evaluate("() => [...window.__sideUrls]"))
        sharpening = [u for u in asked if "s=640" in u and u in side and u not in finished]
        assert sharpening, asked[-10:]
        page.evaluate("""() => { const { grid } = window.__mv;
            const top = grid.scroller.scrollTop;
            for (const [n, node] of grid.mounted) {
              if (grid.layout.cells[n].y > top + 50) { node.click(); return; } } }""")
        page.wait_for_selector("#viewer:not([hidden])")
        opened = len(finished)
        # Until each has come or been stopped (the slow link queues them).
        for _ in range(100):
            if all(u in finished or u in failed for u in sharpening):
                break
            page.wait_for_timeout(200)
        late = [u for u in finished[opened:] if u in sharpening]
        assert not late, late

        # Closed: the grid is back, and so are its sharp pictures.
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        page.evaluate("() => window.__mv.viewer.close()")
        page.wait_for_function("""() => { const { grid } = window.__mv;
            const top = grid.scroller.scrollTop, height = grid.scroller.clientHeight;
            const on = [...grid.mounted].filter(([n]) => { const c = grid.layout.cells[n];
              return c.y + c.h > top && c.y < top + height; });
            return on.length && on.every(([, node]) => node.img.getAttribute('src').includes('s=640')); }""",
                               timeout=10000)
        assert not errors, errors
    finally:
        context.close()
