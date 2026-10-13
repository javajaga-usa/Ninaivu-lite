"""The gallery fetches ahead like a phone's own photo app, and each view
remembers its place (ported from Ninaivu 1.10.10).

- Stopped (and when a gallery opens): the nearest 300 thumbnails each way,
  two the way it was going for every one behind, once the page is idle.
- While scrolling: the next stretch the way it is going, further the slower
  it goes, nothing during a fling; a fetch is called off only when the scroll
  really turns round. Four at a time on a computer, two on a phone, one on
  Data Saver or 2G (static/js/preload-policy.js, tests/preload_policy.mjs).
- The date rail under the mouse and a month pointed at in Jump to get their
  place ready before the jump.
- More tiles kept with their pictures: 1,200 on a computer, 600 on a phone.
- All media, an album, a search: each comes back to the photograph it was
  left on, after another view or a reload.
- The viewer opens on the gallery's own picture, sharp, in the box the
  photograph will take.

Measured in Chromium on 2,000 photographs over a 250 ms, 600 KB/s link, tiles
still blank on screen while scrolling: a phone scrolling 2,500 px a second
19.8% before, 0.3% after (2.4% after, 20.9% before, with its CPU slowed four
times); scrolling down and back 18.7% before, 0.1% after; a computer 0.7%
before, none after. 326 thumbnails ready at the top of a gallery (was 26),
529 more after stopping mid-way (was 48), 485 around a place on the date rail
while the mouse rested there (was none).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ninaivu_lite" / "static" / "js"
GRID = (JS / "grid.js").read_text(encoding="utf-8")
VIEWER = (JS / "viewer.js").read_text(encoding="utf-8")
CSS = (ROOT / "ninaivu_lite/static/css/style.css").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_rules_for_fetching_ahead_under_node():
    done = subprocess.run([shutil.which("node"), "--test", str(ROOT / "tests" / "preload_policy.mjs")],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


def test_three_hundred_each_way_when_stopped():
    assert "const PREFETCH_COUNT = 300;" in GRID
    ready = GRID[GRID.index("  readyAround("):GRID.index("  prefetchAround(")]
    assert "this.nearestUrls(bottom, 1, count)" in ready
    assert "this.nearestUrls(top, -1, count)" in ready
    assert "PREFETCH_COUNT / 2" in ready                       # short of memory
    assert "interleaveDirectional(ahead, behind, 2)" in ready
    now = GRID[GRID.index("  preloadNow() {"):GRID.index("  unmountedUrls(")]
    stopped = now[now.index("if (!this.scrolling) {"):now.index("const [bandTop")]
    assert "this.readyAround(top, bottom, down, profile)" in stopped
    # A255: stopping never calls off what is on its way.
    assert "cancel" not in stopped.split("this.prefetcher.want(")[1]


def test_while_scrolling_nothing_during_a_fling_and_off_only_on_a_real_turn():
    now = GRID[GRID.index("  preloadNow() {"):GRID.index("  unmountedUrls(")]
    assert "if (this.fast || profile.lean) {" in now
    assert "predictionMs(speed)" in now
    assert "{ cancel: reversed, keep: this.shownUrls() }" in now
    speed = GRID[GRID.index("  measureSpeed("):GRID.index("  band() {")]
    assert "Math.abs(instant) > REVERSE_PX_MS || this.turning >= 2" in speed


def test_more_tiles_kept_and_a_place_put_back():
    assert re.search(r"return touch \? 600 : 1200;", GRID)
    assert "  anchor() {" in GRID and "  scrollToAnchor(place) {" in GRID
    places = (JS / "gallery/places.js").read_text(encoding="utf-8")
    data = (JS / "gallery/data.js").read_text(encoding="utf-8")
    assert "placeLoaded(filters, resetScroll);" in data
    assert "if (resetScroll) leavingView();" in data
    assert "window.addEventListener('pagehide', rememberPlace)" in places
    assert "const PLACES_MAX = 40;" in places


def test_the_viewer_opens_on_the_gallery_picture_not_a_blur():
    assert "ghost.className = sharp ? 'placeholder sharp' : 'placeholder'" in VIEWER
    rule = re.search(r"\.viewer-stage > img\.placeholder\.sharp \{[^}]*\}", CSS).group(0)
    assert "filter: none" in rule and "grid-area: 1 / 1" in rule
    # A neighbour let go of while paging is warmed again on the way back.
    assert "!this.warming.has(neighbour) && !this.warmed.has(neighbour)" in VIEWER
