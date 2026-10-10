"""Regression tests for the web-page findings of the 2026-10-06 audit: A27, A28,
A39, A53, A54, A55 and A64. What can be checked without a browser is checked
here: the pages and scripts the server sends, the strings they carry, and the
one server call Sudar gained (A53). The guard over the locale files themselves
is tests/test_language.py."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ids, sign_in
from PIL import Image

from ninaivu_lite import media
from test_gallery_api import rescan

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ninaivu_lite" / "static"
JS = STATIC / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def node_eval(script: str) -> str:
    done = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                          capture_output=True, text=True, cwd=ROOT, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")


# --- A27: a change of person puts the last person's photograph away --------------------------


def test_a27_a_change_of_person_closes_the_viewer_and_the_gate_takes_the_keys():
    app_js = read(JS / "app.js")
    forget = app_js[app_js.index("function forgetPerson()"):]
    forget = forget[:forget.index("\n}\n")]
    assert "viewer.close()" in forget and "cache?.clear()" in forget
    assert "setData([]" in forget
    # Called when a session is lost (before the gate) and when anybody signs in.
    lost = app_js[app_js.index("onUnauthorized(async"):]
    assert lost.index("forgetPerson()") < lost.index("gate.show(")
    signed_in = app_js[app_js.index("onSignedIn: async"):]
    assert signed_in.index("forgetPerson()") < signed_in.index("start(user)")
    keys = app_js[app_js.index("function wireKeyboard()"):]
    assert keys.index("$('#gate').hidden") < keys.index("cycleTheme()")
    assert "getElementById('gate')" in read(JS / "palette.js")


# --- A28: old browsers get a message, a readable name and an opaque sign-in screen -----------


@pytest.mark.parametrize("page", ["/", "/admin"])
def test_a28_pages_carry_the_too_old_message(app, page):
    body = app.test_client().get(page).get_data(as_text=True)
    assert 'id="too-old"' in body and "This browser is too old" in body
    assert "இந்த உலாவி" in body                         # in Tamil too: no script will translate it
    assert re.search(r'<script src="/static/js/old-browser\.js\?v=\w+"></script>', body)
    assert re.search(r'<script type="module" src="/static/js/modern\.js\?v=\w+"></script>', body)


def test_a28_share_page_carries_it_too(app, admin):
    target = ids(app)["beach.jpg"]
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": target}).json["token"]
    body = app.test_client().get(f"/share/{token}").get_data(as_text=True)
    assert 'id="too-old"' in body and "old-browser.js" in body


def test_a28_the_fallback_script_is_old_fashioned_javascript(app):
    for name in ("old-browser.js", "modern.js"):
        assert app.test_client().get(f"/static/js/{name}").status_code == 200
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", read(JS / "old-browser.js"), flags=re.S)
    assert not re.search(r"=>|\blet\b|\bconst\b|`|\?\.|\?\?|\bclass\b", code)
    assert "__ninaivuCanRun" in code and "__ninaivuCanRun" in read(JS / "modern.js")


def test_a28_share_js_has_no_top_level_await():
    top = [line for line in read(JS / "share.js").splitlines() if re.match(r"(const \w+ = )?await\b", line)]
    assert not top


def test_a28_gradient_lettering_only_where_color_mix_works():
    css = read(STATIC / "css" / "style.css")
    for found in re.finditer(r"-webkit-text-fill-color: transparent", css):
        before = css[:found.start()]
        opened = before.rfind("@supports (color: color-mix(")
        assert opened != -1
        # Still inside that @supports block: more { than } since it opened.
        inside = before[opened:]
        assert inside.count("{") > inside.count("}"), css[found.start() - 200:found.start()]


def test_a28_the_sign_in_screen_has_a_plain_background_first():
    css = read(STATIC / "css" / "style.css")
    for rule in re.findall(r"\n\.gate \{[^}]*\}", css):
        plain = rule.find("background: var(--bg);")
        mixed = rule.find("color-mix(")
        assert plain != -1 and (mixed == -1 or plain < mixed), rule


# --- A39: the visitor reads why a video is not shown ---------------------------------------


def test_a39_the_refusal_reaches_the_page(app, admin, monkeypatch):
    target = ids(app)["clip.mp4"]
    admin.post("/api/visibility", json={"ids": [target], "visibility": "public"})
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": target}).json["token"]
    monkeypatch.setattr(media, "strip_video", lambda path, out: False)
    stranger = app.test_client()
    item = stranger.get(f"/api/share/{token}").json["item"]
    # What mediaRefusal() asks: one byte, and the answer says why in words.
    refused = stranger.get(item["src"], headers={"Range": "bytes=0-0", "Accept": "application/json"})
    assert refused.status_code == 415
    message = refused.json["error"]
    assert message in json.loads(read(STATIC / "i18n" / "ta.json"))
    share_js, viewer_js = read(JS / "share.js"), read(JS / "viewer.js")
    assert "mediaRefusal(" in share_js and "video.onerror" in viewer_js
    assert "mediaRefusal(" in viewer_js


# --- A53: Sudar saves from the full-size original, in its own format ------------------------


def test_a53_full_size_source_for_a_tiff(app, admin, family, library):
    root, _ = library
    Image.new("RGB", (3200, 2000), (40, 90, 160)).save(root / "scan.tif", "TIFF")
    rescan(app)
    target = ids(app)["scan.tif"]
    item = admin.get(f"/api/asset/{target}").json
    assert not item["playable"]                         # viewed through a 2560 px copy
    full = admin.get(f"/api/asset/{target}/edit-source")
    assert full.status_code == 200 and full.mimetype == "image/jpeg"
    assert Image.open(io.BytesIO(full.data)).size == (3200, 2000)
    assert family.get(f"/api/asset/{target}/edit-source").status_code == 403
    assert admin.get(f"/api/asset/{ids(app)['clip.mp4']}/edit-source").status_code == 400


def test_a53_full_size_source_stays_within_the_save_limit(app, admin, library, monkeypatch):
    from ninaivu_lite import api_sudar
    root, _ = library
    Image.new("RGB", (1000, 800), (10, 10, 10)).save(root / "wide.tif", "TIFF")
    rescan(app)
    monkeypatch.setattr(api_sudar, "MAX_PIXELS", 200_000)
    full = admin.get(f"/api/asset/{ids(app)['wide.tif']}/edit-source")
    w, h = Image.open(io.BytesIO(full.data)).size
    assert w * h <= 200_000 and abs(w / h - 1.25) < 0.01


def test_a53_a_jpeg_copy_is_saved_as_a_jpeg(app, admin):
    target = ids(app)["beach.jpg"]
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (1, 2, 3)).save(out, "JPEG")
    saved = admin.post(f"/api/asset/{target}/edited-copy", data=out.getvalue(),
                       content_type="image/jpeg")
    assert saved.status_code == 201 and saved.json["name"].endswith(".jpg")
    library_js = read(JS / "sudar" / "library.mjs")
    assert "'Content-Type': blob.type" in library_js


@needs_node
def test_a53_save_format_follows_the_original():
    out = node_eval(
        "import {saveType} from './ninaivu_lite/static/js/sudar/files.mjs';"
        "console.log(['a.JPG','b.heic','c.png','d.tif','e.webp','f'].map(n=>saveType({name:n})).join(' '))")
    assert out == "image/jpeg image/jpeg image/png image/png image/webp image/jpeg"


@needs_node
def test_a53_a_worker_that_failed_is_not_run_again_on_the_page():
    """A worker that ran and failed is reported; only a worker that could not
    start hands the render to the page (here, a fake canvas counts it)."""
    script = """
import {AIPhotoService} from './ninaivu_lite/static/js/sudar/service.mjs';
let pageRenders = 0;
globalThis.OffscreenCanvas = class { constructor() { pageRenders += 1; throw new Error('page render'); } };
globalThis.createImageBitmap = async () => ({ close() {} });
const bitmap = { width: 10, height: 10 };
const make = (mode) => class {
  constructor() { setTimeout(() => mode === 'error'
    ? this.onmessage({ data: { error: 'worker failed' } }) : this.onerror(new Event('error')), 0); }
  postMessage() {} terminate() {}
};
const service = new AIPhotoService();
const results = [];
for (const mode of ['error', 'unavailable']) {
  globalThis.Worker = make(mode);
  pageRenders = 0;
  try { await service.applyAdjustments(bitmap, {}); results.push('ok'); }
  catch (e) { results.push(`${e.message}/${pageRenders}`); }
}
console.log(results.join(' '));
"""
    assert node_eval(script) == "worker failed/0 page render/1"


# --- A54: a failed "load more" does not end the gallery -----------------------------------


def test_a54_load_more_keeps_its_place_and_offers_a_retry(app):
    app_js = read(JS / "app.js")
    more = app_js[app_js.index("async function loadMoreIfNear("):]
    more = more[:more.index("\n}\n")]
    assert "page.next = null" not in more
    assert "retryAt" in more and "showLoadMoreFailed(page)" in more
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'id="load-more-failed"' in body and 'id="load-more-retry"' in body
    # A status call that failed is not "no library folder yet".
    reload_fn = app_js[app_js.index("async function reload("):]
    assert reload_fn.index("state.statusFailed") < reload_fn.index("No library folder yet")


# --- A55: the server's messages and the last English strings are in Tamil -----------------


def test_a55_install_banner_share_page_and_offline_page_are_translated():
    app_js = read(JS / "app.js")
    assert "desc.innerHTML = 'Tap" not in app_js
    assert "i18n.t('Tap {share} Share below" in app_js
    share_js = read(JS / "share.js")
    assert "i18n.t(body.error)" in share_js and "i18n.t(data.error)" in share_js
    sw = read(STATIC / "sw.js")
    assert "Ninaivu is offline" in sw and "நினைவுடன் இணைப்பு இல்லை" in sw
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    for key in ("Client secret", "Workflows", "Graphics card", "Hardware"):
        assert ta[key] != key


def test_a55_a_server_error_is_shown_in_tamil(app, admin):
    """The page translates `error` with i18n.t(); the sentence has to be there."""
    ta = json.loads(read(STATIC / "i18n" / "ta.json"))
    bad = admin.post("/api/admin/settings", json={"language": "fr"})
    assert bad.status_code == 400 and bad.json["error"] in ta
    me = sign_in(app, admin)
    refused = admin.delete(f"/api/people/{me.id}")
    assert refused.status_code == 409 and refused.json["error"] in ta


# --- A64: dialogs hold the focus and make the page behind inert -----------------------------


def test_a64_dialogs_are_wired(app):
    dialogs = read(JS / "dialogs.js")
    assert ".inert" in dialogs and "returnTo" in dialogs
    for overlay in (".viewer", ".modal", ".sheet", ".gate", ".palette"):
        assert overlay in dialogs
    assert "wireDialogs();" in read(JS / "app.js")
    assert app.test_client().get("/static/js/dialogs.js").status_code == 200
