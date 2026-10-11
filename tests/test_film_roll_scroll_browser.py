"""Scrolling like a film roll, in a real browser on a phone-sized screen with
a Retina density (three pixels to the point, as an iPhone has), where every
tile wants the sharp 640 px thumbnail: tiles made while the scroll goes on
take the small picture and are sharpened once it stops, a tile only ever
changes to a picture the browser already holds, the hover look stays off
until the mouse moves, and no tile is a compositing layer of its own. Uses
the smoke tests' server and Chromium, and skips the same way they do."""

from __future__ import annotations

import pytest
from test_smoke_browser import REQUIRED, browser, long_gallery, settle  # noqa: F401
from ninaivu_lite import auth

pytest.importorskip("playwright.sync_api")

# What a tile's <img> was pointed at, in order, and which pictures were loaded
# on the side (grid.js swapIn and the prefetcher both use a plain Image).
WATCH = """
window.__sideLoaded = new Set();
const Real = window.Image;
window.Image = function (...args) {
  const image = new Real(...args);
  image.addEventListener('load', () => window.__sideLoaded.add(image.getAttribute('src')));
  return image;
};
window.__swaps = [];
// The script runs before the page has an element to watch.
const watch = () => new MutationObserver((records) => {
  for (const record of records) {
    const img = record.target;
    const from = record.oldValue, to = img.getAttribute('src');
    if (!from || !to || from === to) continue;
    const had = window.__sideLoaded.has(to) || (img.complete && img.naturalWidth > 0);
    window.__swaps.push({ from, to, had, ready: img.classList.contains('ready') });
  }
}).observe(document.documentElement, {
  subtree: true, attributes: true, attributeFilter: ['src'], attributeOldValue: true });
if (document.documentElement) watch();
else document.addEventListener('DOMContentLoaded', watch, { once: true });
"""

SCROLL = """async (pixels) => {
  const { grid } = window.__mv;
  const scroller = grid.scroller;
  const before = new Set([...grid.mounted.keys()]);
  const made = new Map();          // cells made while scrolling -> their first src
  const started = performance.now();
  await new Promise((done) => {
    const step = (now) => {
      scroller.scrollTop += 24;
      for (const [n, node] of grid.mounted) {
        if (!before.has(n) && !made.has(n)) made.set(n, node.img.getAttribute('src'));
      }
      if (now - started < 600 && scroller.scrollTop < pixels) requestAnimationFrame(step);
      else done();
    };
    requestAnimationFrame(step);
  });
  const midway = {
    scrolling: grid.scrolling,
    noHover: grid.container.classList.contains('no-hover'),
    pointerEvents: getComputedStyle(grid.mounted.values().next().value).pointerEvents,
    made: [...made.values()],
  };
  await new Promise((r) => setTimeout(r, 700));
  return midway;
}"""

AT_REST = """() => {
  const { grid } = window.__mv;
  const top = grid.scroller.scrollTop, height = grid.scroller.clientHeight;
  const onScreen = [];
  for (const [n, node] of grid.mounted) {
    const cell = grid.layout.cells[n];
    if (cell.y + cell.h > top && cell.y < top + height) onScreen.push(node.img.getAttribute('src'));
  }
  const one = grid.mounted.values().next().value;
  const style = getComputedStyle(one);
  return {
    scrolling: grid.scrolling,
    noHover: grid.container.classList.contains('no-hover'),
    onScreen,
    willChange: style.willChange,
    transform: style.transform,
    swaps: window.__swaps,
  };
}"""


def phone(browser, long_gallery):  # noqa: F811
    context = browser.new_context(viewport={"width": 390, "height": 780},
                                  device_scale_factor=3, service_workers="block")
    context.add_init_script(WATCH)
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(long_gallery["base"] + "/")
    page.wait_for_selector("#grid .cell img.ready")
    settle(page)
    return context, page, errors


def test_tiles_take_the_small_picture_while_scrolling_and_sharpen_at_rest(browser, long_gallery):  # noqa: F811
    context, page, errors = phone(browser, long_gallery)
    try:
        midway = page.evaluate(SCROLL, 3000)
        assert midway["scrolling"], midway
        # The hover look is off while the grid scrolls, but the tiles still
        # answer the pointer, so a tap mid-glide opens its photograph.
        assert midway["noHover"] and midway["pointerEvents"] != "none", midway
        # Every tile made on the way asked for the small picture.
        assert midway["made"], midway
        assert all("s=256" in src for src in midway["made"]), midway["made"]

        settle(page)
        rest = page.evaluate(AT_REST)
        assert not rest["scrolling"], rest
        # Stopped, but the mouse has not moved: no hover look yet.
        assert rest["noHover"], rest
        # On screen, every tile is sharp again.
        assert rest["onScreen"] and all("s=640" in src for src in rest["onScreen"]), rest["onScreen"]
        # Each was sharpened to a picture the browser already held, so the
        # tile never went blank (Safari drops a picture whose address changes).
        sharpened = [s for s in rest["swaps"] if "s=256" in s["from"] and "s=640" in s["to"]]
        assert sharpened, rest["swaps"][:5]
        early = [s for s in sharpened if not s["had"]]
        assert not early, early[:5]
        # No layer per tile: nothing is promised to change, and the position
        # is a plain 2-D transform.
        assert rest["willChange"] == "auto", rest["willChange"]
        assert rest["transform"].startswith("matrix("), rest["transform"]

        # The mouse moves: the hover look is back.
        page.mouse.move(100, 300)
        page.mouse.move(120, 330)
        page.wait_for_timeout(100)
        assert not page.evaluate("() => window.__mv.grid.container.classList.contains('no-hover')")
        assert not errors, errors
    finally:
        context.close()
