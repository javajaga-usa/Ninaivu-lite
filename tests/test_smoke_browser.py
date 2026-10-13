"""The real server in a real browser (headless Chromium, through Playwright):
the family gallery and its viewer, the admin console, a share link and the
sign-in screens, in English and Tamil, on a phone and on a laptop. A page fails
on a script error, a console error, a 5xx answer, a missing file of our own, a
key part drawn with no size, or anything wider than the screen. And every one
of our stylesheets must parse whole: a broken comment that drops a rule (M22,
1.11.0) fails here.

Skipped when Playwright or its Chromium is not installed, so the usual test run
is unchanged; the "Browser" job in .github/workflows/tests.yml installs both
and sets NINAIVU_BROWSER_TESTS=1, which turns a skip into a failure.
Locally: pip install -r requirements-browser.txt, then
python -m playwright install chromium (NINAIVU_TEST_CHROMIUM names another
Chromium to use instead)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import make_jpeg

from ninaivu_lite import auth, db

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "ninaivu_lite" / "static" / "css"
REQUIRED = os.environ.get("NINAIVU_BROWSER_TESTS") == "1"

LANGUAGES = ["en", "ta"]
SCREENS = {"phone": (360, 780), "laptop": (1280, 800)}
ADMIN_PAGES = ["overview", "library", "duplicates", "archive", "people", "visibility", "settings"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, path: str, token: str, payload: dict) -> dict:
    """A signed-in write, as the app's own pages make it."""
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": base,
                 "Cookie": f"{auth.SESSION_COOKIE}={token}"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read())


@contextlib.contextmanager
def serve(tmp: Path, photos: Path, data: Path, count: int):
    """`python -m ninaivu_lite` on a free port over *photos*, once it has
    indexed *count* of them: (address, their ids)."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(tmp / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ninaivu_lite", "--no-browser", "--host", "127.0.0.1",
         "--port", str(port), "--data", str(data), str(photos)],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        ids: list[int] = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and len(ids) < count:
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2).close()
                conn = db.connect(data)
                ids = [r[0] for r in conn.execute("SELECT id FROM assets ORDER BY id")]
                conn.close()
            except OSError:
                pass
            time.sleep(0.3)
        if len(ids) < count:
            log.flush()
            pytest.fail("the server did not start and index the photos:\n"
                        + (tmp / "server.log").read_text(encoding="utf-8")[-3000:])
        yield base, ids
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """`python -m ninaivu_lite` on a free port, over eight new photos, with an
    administrator (signed in, for the tests) and one family profile; one photo is
    also kept in a second folder, for the Duplicates page."""
    tmp = tmp_path_factory.mktemp("browser")
    photos, data = tmp / "Photos", tmp / "data"
    for n in range(8):
        size = (480, 640) if n % 3 == 0 else (640, 480)
        make_jpeg(photos / "2019" / f"photo-{n}.jpg", f"2019:05:{10 + n:02d} 10:00:00",
                  size=size, color=(30 * n, 110, 200 - 20 * n))
    # One photo kept twice, so the console's Duplicates page has a group to draw.
    (photos / "copies").mkdir()
    shutil.copy2(photos / "2019" / "photo-1.jpg", photos / "copies" / "photo-1.jpg")
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase", name="Appa",
                             role="admin")
    auth.create_user(conn, "amma", name="Amma", role="family")
    token = auth.start_session(conn, admin.id)
    conn.close()

    with serve(tmp, photos, data, 9) as (base, ids):
        album = call(base, "/api/albums", token, {"name": "Pongal 2019", "ids": ids[:5]})
        album_id = album["id"]
        shares = {
            scope: call(base, "/api/shares", token,
                        {"scope": scope, "target_id": target, "expires_in_days": 0,
                         "password": None})["token"]
            for scope, target in (("album", album_id), ("asset", ids[0]))}
        yield {"base": base, "token": token, "shares": shares}


@pytest.fixture(scope="module")
def browser():
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        if REQUIRED:
            raise
        pytest.skip(f"Playwright could not start: {exc}")
    try:
        chromium = playwright.chromium.launch(
            executable_path=os.environ.get("NINAIVU_TEST_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        playwright.stop()
        if REQUIRED:
            raise
        pytest.skip(f"no Chromium for Playwright: {exc.__class__.__name__}")
    yield chromium
    chromium.close()
    playwright.stop()


class Watched:
    """A page whose script errors, console errors and failed answers are kept."""

    def __init__(self, context, base: str):
        self.page = context.new_page()
        self.problems: list[str] = []
        page = self.page
        page.on("pageerror", lambda error: self.problems.append(f"script error: {error}"))
        page.on("console", self._console)
        page.on("response", lambda answer: self._answer(answer, base))

    def _console(self, message):
        # A 4xx the page expects (401 before signing in) is logged by the
        # browser as a console error too; answers are judged in _answer.
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            self.problems.append(f"console error: {message.text}")

    def _answer(self, answer, base: str):
        if answer.status >= 500:
            self.problems.append(f"HTTP {answer.status}: {answer.url}")
        elif answer.status >= 400 and answer.url.startswith(base + "/static/"):
            self.problems.append(f"HTTP {answer.status}: {answer.url}")


@pytest.fixture(params=[(lang, screen) for lang in LANGUAGES for screen in SCREENS],
                ids=lambda p: f"{p[0]}-{p[1]}")
def look(request, browser, server):
    """A new browser context in one language at one screen size."""
    lang, screen = request.param
    width, height = SCREENS[screen]
    context = browser.new_context(viewport={"width": width, "height": height},
                                  locale="ta-IN" if lang == "ta" else "en-GB",
                                  service_workers="block")
    # The language is kept per device (static/js/i18n.js).
    context.add_init_script(f"try {{ localStorage.setItem('ninaivu.lang', '{lang}'); }} catch {{}}")
    watched = Watched(context, server["base"])
    watched.lang = lang
    yield watched
    context.close()


def sign_in(watched, server):
    """The administrator's session cookie, as signing in leaves it."""
    watched.page.context.add_cookies([{"name": auth.SESSION_COOKIE, "value": server["token"],
                                       "url": server["base"]}])


def settle(page):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)


def check(watched, where: str, selectors: list[str]):
    """No problems so far, nothing wider than the screen, and each of
    *selectors* drawn with a size, inside the screen's width."""
    page = watched.page
    assert page.evaluate("document.documentElement.lang") == watched.lang, where
    found = page.evaluate("""(selectors) => {
      const width = innerWidth, out = [];
      if (document.scrollingElement.scrollWidth > width) {
        out.push(`the page is ${document.scrollingElement.scrollWidth}px wide on a ${width}px screen`);
      }
      for (const selector of selectors) {
        const node = document.querySelector(selector);
        if (!node) { out.push(`${selector}: not there`); continue; }
        const box = node.getBoundingClientRect();
        if (box.width <= 0 || box.height <= 0) out.push(`${selector}: drawn ${box.width}x${box.height}`);
        else if (box.left < -1 || box.right > width + 1) {
          out.push(`${selector}: from ${Math.round(box.left)} to ${Math.round(box.right)}px on a ${width}px screen`);
        }
      }
      return out;
    }""", selectors)
    problems = watched.problems + found
    assert not problems, f"{where}:\n  " + "\n  ".join(problems)


def test_family_gallery_and_viewer(look, server):
    sign_in(look, server)
    page = look.page
    page.goto(server["base"] + "/")
    page.wait_for_selector("#grid .cell img.ready")
    settle(page)
    check(look, "gallery", ["#grid", "#grid .cell", "#profile-btn", "#lang-btn"])
    page.click("#grid .cell")
    page.wait_for_selector("#viewer:not([hidden]) #viewer-stage img")
    settle(page)
    check(look, "viewer", ["#viewer", "#viewer-stage img", "#v-close"])


def test_admin_console(look, server):
    sign_in(look, server)
    page = look.page
    page.goto(server["base"] + "/admin")
    page.wait_for_selector(".panel.active[data-panel='overview']")
    settle(page)
    for name in ADMIN_PAGES:
        # As a click on its tab does; on a phone the tab may be in a row
        # scrolled sideways, which is not what is checked here.
        page.evaluate("(name) => document.querySelector(`#tabs button[data-tab='${name}']`).click()",
                      name)
        page.wait_for_selector(f".panel.active[data-panel='{name}']")
        settle(page)
        check(look, f"admin {name}", [".admin-bar", ".admin-page-heading",
                                      f".panel.active[data-panel='{name}']"])


@pytest.mark.parametrize("scope", ["album", "asset"])
def test_share_link(look, server, scope):
    page = look.page
    page.goto(f"{server['base']}/share/{server['shares'][scope]}")
    page.wait_for_selector("#main img.ready" if scope == "album" else "#main img")
    settle(page)
    check(look, f"{scope} share", ["#title", "#main", "#main img"])


def test_sign_in_and_profile_picker(look, server):
    page = look.page
    page.goto(server["base"] + "/")
    page.wait_for_selector("#gate:not([hidden]) .picker-tile")
    settle(page)
    check(look, "who's watching", ["#gate .gate-card", "#gate .picker-tile"])
    page.goto(server["base"] + "/admin")
    page.wait_for_selector("#gate:not([hidden]) input[type='password']")
    settle(page)
    check(look, "console sign-in", ["#gate .gate-card", "#gate input[type='password']"])


# -- Every stylesheet parses whole ---------------------------------------------

# A well-formed comment: one that does not open another before it closes. A
# comment that lost its "*/" (or its "/*") is left in, so the rule it would
# swallow is still counted below, and the browser's count comes up short.
COMMENT = re.compile(r"/\*(?:(?!/\*).)*?\*/", re.S)
STRING = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")


def blocks(css: str) -> int:
    """How many rules the stylesheet means to have: each "{" is one rule (a
    style rule, an @media, a keyframe, an @font-face)."""
    return STRING.sub('""', COMMENT.sub("", css)).count("{")


def last_selector(css: str) -> str:
    """The selector of the stylesheet's last rule, as written."""
    text = STRING.sub('""', COMMENT.sub("", css))
    head = text[:text.rindex("{")]
    return " ".join(re.split(r"[{};]", head)[-1].split())


def test_every_stylesheet_parses_whole(browser, server):
    sheets = sorted(CSS.glob("*.css"))
    assert {"style.css", "admin.css", "sudar.css"} <= {p.name for p in sheets}
    page = browser.new_page()
    try:
        page.goto(server["base"] + "/healthz")
        problems = []
        for path in sheets:
            css = path.read_text(encoding="utf-8")
            parsed = page.evaluate("""(href) => new Promise((resolve, reject) => {
              const link = document.createElement('link');
              link.rel = 'stylesheet'; link.href = href;
              link.onerror = () => reject(new Error(`could not load ${href}`));
              link.onload = () => {
                const rules = [], walk = (list) => { for (const r of list) { rules.push(r); if (r.cssRules) walk(r.cssRules); } };
                walk(link.sheet.cssRules);
                resolve({count: rules.length,
                         selectors: rules.filter((r) => r.selectorText).map((r) => r.selectorText.replace(/\\s+/g, ' '))});
              };
              document.head.append(link);
            })""", f"/static/css/{path.name}")
            if parsed["count"] != blocks(css):
                problems.append(f"{path.name}: the browser kept {parsed['count']} of "
                                f"{blocks(css)} rules")
            wanted = last_selector(css)
            if wanted and not wanted.startswith("@") and wanted not in parsed["selectors"]:
                problems.append(f"{path.name}: its last rule, {wanted}, was dropped")
        assert not problems, "\n".join(problems)
    finally:
        page.close()


# -- Scrolling fetches ahead of itself -----------------------------------------

@pytest.fixture(scope="module")
def long_gallery(tmp_path_factory):
    """A gallery long enough to scroll through: 360 small photos over 30 days."""
    tmp = tmp_path_factory.mktemp("scroll")
    photos, data = tmp / "Photos", tmp / "data"
    for n in range(360):
        make_jpeg(photos / "2020" / f"p{n:03d}.jpg",
                  f"2020:{1 + n // 120:02d}:{1 + (n // 12) % 10:02d} 10:{n % 60:02d}:00",
                  size=(160, 120), color=(n % 256, (n * 7) % 256, (n * 13) % 256))
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase", name="Appa",
                             role="admin")
    token = auth.start_session(conn, admin.id)
    conn.close()
    with serve(tmp, photos, data, 360) as (base, _ids):
        yield {"base": base, "token": token}


SCROLL = """async ([speed, busy]) => {
  const scroller = document.querySelector('#scroller');
  const mounted = new Set();
  const look = () => document.querySelectorAll('#grid .cell').forEach((c) => mounted.add(c.dataset.id));
  look();
  // Each time the grid works out what to fetch ahead: how far it looked (px).
  const grid = window.__mv.grid, preload = grid.preload.bind(grid), reach = [];
  // An opened gallery has its next 300 thumbnails fetched already, which here
  // is nearly all 360: forgotten, as in a gallery longer than that, so the
  // scroll's own fetching ahead is what is measured.
  grid.prefetcher.want([], { cancel: 'all' });
  grid.prefetcher.done.clear();
  grid.preload = (now) => { const before = grid.lastPreload; preload(now);
    if (grid.lastPreload !== before && grid.scrolling) reach.push(grid.lookahead); };
  const started = performance.now();
  let last = started, wasScrolling = false, frames = 0;
  await new Promise((done) => {
    const step = (now) => {
      scroller.scrollTop += speed * (now - last) / 1000;
      last = now;
      // A busy computer: every other frame, work that runs before the
      // grid's own frame callback (the load of a Raspberry Pi, or of CI).
      if (busy && (frames++ % 2)) { const until = performance.now() + busy; while (performance.now() < until); }
      look();
      wasScrolling ||= document.querySelector('#grid').classList.contains('is-scrolling');
      if (now - started < 700) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  });
  const ended = performance.now();
  const fetched = new Set(performance.getEntriesByType('resource')
    .filter((e) => e.name.includes('/api/thumb/') && e.startTime > started && e.startTime < ended)
    .map((e) => new URL(e.name).pathname.split('/').pop()));
  await new Promise((r) => setTimeout(r, 400));
  grid.preload = preload;
  return { ahead: [...fetched].filter((id) => !mounted.has(id)).length,
           reach,
           wasScrolling,
           stillScrolling: document.querySelector('#grid').classList.contains('is-scrolling') };
}"""


def test_scrolling_fetches_thumbnails_ahead_by_speed(browser, long_gallery):
    """While the gallery scrolls, the pictures it is about to reach are asked
    for before their tiles are on the page, further ahead the faster it goes."""
    found = {}
    for speed in (600, 3000):
        context = browser.new_context(viewport={"width": 390, "height": 780},
                                      service_workers="block")
        context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                              "url": long_gallery["base"]}])
        watched = Watched(context, long_gallery["base"])
        page = watched.page
        try:
            page.goto(long_gallery["base"] + "/")
            page.wait_for_selector("#grid .cell img.ready")
            settle(page)
            page.evaluate("performance.setResourceTimingBufferSize(5000)")
            found[speed] = page.evaluate(SCROLL, [speed, 0])
            assert not watched.problems, watched.problems
        finally:
            context.close()
    assert found[600]["ahead"] > 0 and found[3000]["ahead"] > 0, found
    # Further ahead the faster: how far the grid looked, not how many pictures
    # a loaded computer happened to fetch in 0.7 s (which varied, and failed).
    assert max(found[3000]["reach"]) > max(found[600]["reach"]), found
    # Hover is held off while moving, and given back once it stops.
    assert found[3000]["wasScrolling"] and not found[3000]["stillScrolling"], found


def test_a_steady_scroll_on_a_busy_computer_still_fetches_ahead(browser, long_gallery):
    """Long, uneven frames (a loaded computer) do not make a steady scroll look
    like a jump with the scrubber, which fetches nothing on the way: the
    speed is taken from each frame's own time, and a jump is only a jump
    when it lasts."""
    context = browser.new_context(viewport={"width": 390, "height": 780},
                                  service_workers="block")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    watched = Watched(context, long_gallery["base"])
    page = watched.page
    try:
        page.goto(long_gallery["base"] + "/")
        page.wait_for_selector("#grid .cell img.ready")
        settle(page)
        page.evaluate("performance.setResourceTimingBufferSize(5000)")
        found = page.evaluate(SCROLL, [1500, 70])
        reach = found["reach"]
        assert not watched.problems, watched.problems
    finally:
        context.close()
    assert found["ahead"] > 0, found
    # Not once taken for a jump (which looks nowhere), nor for a scroll faster
    # than it is: at 1,500 px a second the grid looks 2.5 s ahead, 3,750 px
    # (the frames' clock read late made it 7,700-8,800 px, or a jump).
    assert reach and all(0 < r < 1500 * 2.5 * 1.25 for r in reach), reach


def _scrolling_page(browser, long_gallery):
    context = browser.new_context(viewport={"width": 390, "height": 780},
                                  service_workers="block")
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": long_gallery["token"],
                          "url": long_gallery["base"]}])
    watched = Watched(context, long_gallery["base"])
    watched.page.goto(long_gallery["base"] + "/")
    watched.page.wait_for_selector("#grid .cell img.ready")
    settle(watched.page)
    return context, watched


def test_an_open_photo_stops_the_grid_fetching_ahead(browser, long_gallery):
    """A photo opened mid-scroll gets the connection: the grid under it went on
    fetching up to 160 thumbnails ahead (18 measured on a phone on 3G, a
    megabyte, while the photo waited). Closed, the grid carries on."""
    context, watched = _scrolling_page(browser, long_gallery)
    page = watched.page
    try:
        found = page.evaluate("""async () => {
          const { grid, viewer } = window.__mv;
          const started = [];
          const want = grid.prefetcher.want.bind(grid.prefetcher);
          grid.prefetcher.pump = ((pump) => function () {
            const before = this.active.size; pump.call(this);
            if (this.active.size > before) started.push(performance.now()); })(grid.prefetcher.pump);
          const scroller = grid.scroller;
          for (let i = 0; i < 20; i += 1) {
            scroller.scrollTop += 60;
            await new Promise((r) => requestAnimationFrame(r));
          }
          const cell = [...document.querySelectorAll('#grid .cell')][3];
          cell.click();
          await new Promise((r) => setTimeout(r, 50));
          const opened = performance.now();
          const queued = grid.prefetcher.queue.length + grid.prefetcher.active.size;
          await new Promise((r) => setTimeout(r, 600));       // the scroll settles
          const after = started.filter((t) => t > opened).length;
          viewer.close();
          await new Promise((r) => setTimeout(r, 100));
          return { queued, after, open: !document.querySelector('#viewer').hidden,
                   resumed: grid.lookahead > 0 };
        }""")
        assert not watched.problems, watched.problems
    finally:
        context.close()
    assert found["queued"] == 0 and found["after"] == 0, found
    assert found["resumed"] and not found["open"], found


def test_a_frame_reads_the_scroll_position_once(browser, long_gallery):
    """Asking the page where the scroll is after tiles were put on it lays the
    page out again; the grid did it for every tile of a frame (1.1 s of the
    2.3 s of script a scroll took, phone CPU slowed four times)."""
    context, watched = _scrolling_page(browser, long_gallery)
    page = watched.page
    try:
        reads = page.evaluate("""async () => {
          const { grid } = window.__mv;
          const scroller = grid.scroller;
          let reads = 0;
          for (const name of ['scrollTop', 'clientHeight']) {
            const own = Object.getOwnPropertyDescriptor(Element.prototype, name);
            Object.defineProperty(scroller, name, { configurable: true,
              get() { reads += 1; return own.get.call(this); },
              set(v) { own.set.call(this, v); } });
          }
          const counts = [];
          for (let i = 0; i < 12; i += 1) {
            scroller.scrollTop += 400;
            reads = 0;
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            counts.push(reads);
          }
          return counts;
        }""")
        assert not watched.problems, watched.problems
    finally:
        context.close()
    # Two frames each time: the grid reads both once in each (a frame that put
    # more tiles on the page than it could takes the rest in the next), the
    # app's scroll handler not at all. It was one read or two for every tile.
    assert max(reads) <= 4, reads
