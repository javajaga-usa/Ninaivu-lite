"""The console's flows in a real browser: the folder picker the first day,
Library settings and Import all use. Uses the smoke tests' server and
Chromium, and skips the same way they do when Playwright is not installed."""

from __future__ import annotations

import pytest
from test_smoke_browser import browser, server, sign_in  # noqa: F401

pytest.importorskip("playwright.sync_api")


def test_flow2_a_path_typed_while_the_folder_list_loads_is_the_one_used(browser, server, tmp_path):  # noqa: F811
    """The picker emptied the typed path when the folder list arrived, so on a
    slow drive (a Pi, a Windows PC listing its network drives) "Use this
    folder" took the folder the picker had opened at, the home folder, and
    the first day made that the library. A picker opened again also handed
    back the last one's folder if pressed before its list came."""
    context = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    class Look:
        pass
    look = Look()
    look.page = page
    sign_in(look, server)
    held = []
    try:
        page.goto(server["base"] + "/admin")
        page.wait_for_load_state("networkidle")
        page.route("**/api/library/browse**", lambda route: held.append(route))
        page.evaluate("""() => { import('/static/js/console/folder-picker.js').then((m) => {
            window.pickedFolder = null;
            m.openFolderPicker({ anyFolder: true, pick: (path) => { window.pickedFolder = path; } });
          }); }""")
        page.wait_for_selector("#folder-modal:not([hidden])")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "the picker did not ask for a folder list"
        typed = str(tmp_path / "Old Photos")
        page.fill("#fm-manual", typed)
        held.pop().continue_()
        page.wait_for_function("() => !document.querySelector('#fm-dirs').innerText.includes('Loading')")
        assert page.input_value("#fm-manual") == typed
        page.click("#fm-apply")
        assert page.evaluate("window.pickedFolder") == typed

        # Opened again: until its own list comes, nothing is there to use.
        page.evaluate("""() => { import('/static/js/console/folder-picker.js').then((m) => {
            m.openFolderPicker({ anyFolder: true, pick: (path) => { window.pickedFolder = path; } });
          }); }""")
        page.wait_for_selector("#folder-modal:not([hidden])")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held
        assert page.is_disabled("#fm-apply")
        held.pop().continue_()
        page.wait_for_function("() => !document.querySelector('#fm-apply').disabled")
        assert not errors, errors
    finally:
        for route in held:
            route.continue_()
        context.close()
