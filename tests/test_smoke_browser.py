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

import json
import os
import re
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
ADMIN_PAGES = ["overview", "library", "archive", "people", "visibility", "settings"]


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


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """`python -m ninaivu_lite` on a free port, over eight new photos, with an
    administrator (signed in, for the tests) and one family profile."""
    tmp = tmp_path_factory.mktemp("browser")
    photos, data = tmp / "Photos", tmp / "data"
    for n in range(8):
        size = (480, 640) if n % 3 == 0 else (640, 480)
        make_jpeg(photos / "2019" / f"photo-{n}.jpg", f"2019:05:{10 + n:02d} 10:00:00",
                  size=size, color=(30 * n, 110, 200 - 20 * n))
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase", name="Appa",
                             role="admin")
    auth.create_user(conn, "amma", name="Amma", role="family")
    token = auth.start_session(conn, admin.id)
    conn.close()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(tmp / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ninaivu_lite", "--no-browser", "--host", "127.0.0.1",
         "--port", str(port), "--data", str(data), str(photos)],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        ids: list[int] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and len(ids) < 8:
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
        if len(ids) < 8:
            log.flush()
            pytest.fail("the server did not start and index the photos:\n"
                        + (tmp / "server.log").read_text(encoding="utf-8")[-3000:])
        album = call(base, "/api/albums", token, {"name": "Pongal 2019", "ids": ids[:5]})
        album_id = album["id"]
        shares = {
            scope: call(base, "/api/shares", token,
                        {"scope": scope, "target_id": target, "expires_in_days": 0,
                         "password": None})["token"]
            for scope, target in (("album", album_id), ("asset", ids[0]))}
        yield {"base": base, "token": token, "shares": shares}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


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
