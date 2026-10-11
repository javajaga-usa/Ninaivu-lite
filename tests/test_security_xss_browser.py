"""Names the household typed, or a file or folder was given, are words, never
markup: in the real server and a real browser, every screen that shows an album
name, a person's name, a file or folder name, the home's name or a search is
opened with each of them made of HTML, and nothing they say is ever built.

(The Content-Security-Policy would stop a script, but markup alone can still
draw a fake sign-in form or a picture from anywhere; neither may appear.)"""

from __future__ import annotations

import os

import pytest

from conftest import make_jpeg
from test_smoke_browser import ADMIN_PAGES, Watched, browser, call, serve, settle  # noqa: F401

from ninaivu_lite import auth, db

pytestmark = pytest.mark.skipif(os.name == "nt", reason="< and > cannot be in a Windows file name")


def payload(where: str) -> str:
    return f'<img src="x" data-pwn="{where}"><b data-pwn="{where}">x'


@pytest.fixture(scope="module")
def marked(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("xss")
    photos, data = tmp / "Photos", tmp / "data"
    # A folder and a file named in markup ("/" is the one thing a name may not hold).
    folder = photos / payload("folder")
    make_jpeg(folder / f"{payload('file')}.jpg", "2019:05:10 10:00:00")
    make_jpeg(photos / "plain" / "a.jpg", "2019:05:11 10:00:00")
    make_jpeg(folder / "copy.jpg", "2019:05:12 10:00:00")
    # The same photograph twice, so Duplicates lists the marked names too.
    (photos / "plain" / "copy.jpg").write_bytes((folder / "copy.jpg").read_bytes())
    conn = db.connect(data)
    admin = auth.create_user(conn, "appa", password="admin passphrase",
                             name=payload("admin-name"), role="admin")
    auth.create_user(conn, "amma", name=payload("person"), role="family")
    token = auth.start_session(conn, admin.id)
    conn.close()
    with serve(tmp, photos, data, 4) as (base, ids):
        call(base, "/api/admin/settings", token, {"house_name": payload("home")})
        album = call(base, "/api/albums", token, {"name": payload("album"), "ids": ids})
        share = call(base, "/api/shares", token, {"scope": "album", "target_id": album["id"]})
        yield {"base": base, "token": token, "share": share["token"]}


@pytest.fixture()
def page(browser, marked):  # noqa: F811
    context = browser.new_context(viewport={"width": 1280, "height": 800},
                                  service_workers="block")
    watched = Watched(context, marked["base"])
    yield watched.page
    context.close()


def built(page) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('[data-pwn]')].map((n) => n.dataset.pwn)")


def signed_in(page, marked):
    page.context.add_cookies([{"name": auth.SESSION_COOKIE, "value": marked["token"],
                               "url": marked["base"]}])


def test_sec_the_gallery_builds_no_markup_from_names(page, marked):
    signed_in(page, marked)
    page.goto(marked["base"] + "/")
    page.wait_for_selector("#grid .cell img.ready")
    page.wait_for_selector("#album-list :is(button, a, div)")
    settle(page)
    assert not built(page)
    # The viewer and its details: the file's name and its folder (the oldest).
    page.locator("#grid .cell").last.click()
    page.wait_for_selector("#viewer:not([hidden]) #viewer-stage img")
    page.click("#v-info")
    settle(page)
    assert "data-pwn" in page.text_content("#viewer")
    assert not built(page)
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    # Search: suggestions name folders, and the words typed are shown back.
    page.fill("#search", "<b data-pwn=search>")
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    settle(page)
    page.fill("#search", "pwn")
    page.wait_for_timeout(600)
    settle(page)
    assert not built(page)


def test_sec_the_console_builds_no_markup_from_names(page, marked):
    signed_in(page, marked)
    page.goto(marked["base"] + "/admin")
    page.wait_for_selector(".panel.active[data-panel='overview']")
    settle(page)
    for name in ADMIN_PAGES:
        page.evaluate("(name) => document.querySelector(`#tabs button[data-tab='${name}']`).click()",
                      name)
        page.wait_for_selector(f".panel.active[data-panel='{name}']")
        settle(page)
        assert not built(page), name


def test_sec_the_sign_in_screen_and_a_share_link_build_no_markup(page, marked):
    page.goto(marked["base"] + "/")
    page.wait_for_selector("#gate:not([hidden]) .picker-tile")
    settle(page)
    assert not built(page)
    page.goto(f"{marked['base']}/share/{marked['share']}")
    page.wait_for_selector("#main img")
    settle(page)
    assert not built(page)
    assert "data-pwn" in page.text_content("#title")
