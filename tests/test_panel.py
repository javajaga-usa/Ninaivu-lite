"""The Control Panel: built for real where Tk and a display exist, and its
cards' colours checked everywhere, because the window is not covered by the
server's tests and a missing colour once crashed it before it appeared."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from ninaivu_lite import panel

SOURCE = Path(panel.__file__).read_text(encoding="utf-8")


def test_every_card_has_a_stripe_colour():
    """1.3.4 added an UPDATES card without a colour: KeyError while building."""
    keys = set(re.findall(r'self\._card\(\w+, "([a-z]+)"', SOURCE))
    assert keys, "no cards found in panel.py"
    assert keys <= set(panel.CARD_ACCENT), keys - set(panel.CARD_ACCENT)


def _tk_root():
    try:
        import tkinter as tk
    except ImportError:
        pytest.skip("this Python has no Tk")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    try:
        return tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk cannot open a window here: {exc}")


def test_the_panel_builds_and_closes(tmp_path, monkeypatch):
    """The whole window, with a data folder of its own and GitHub never asked."""
    from ninaivu_lite import updates
    from ninaivu_lite.control import Controller

    monkeypatch.setattr(updates, "check", lambda data_dir, force=False: {
        "version": "9.9.9", "url": updates.RELEASES_PAGE, "available": True, "checked_at": 0})
    root = _tk_root()
    try:
        view = panel.Panel(root, Controller(str(tmp_path)))
        for _ in range(60):                       # let the update thread answer
            root.update()                         # runs the panel's own pump
            if view.update_text.get():
                break
            time.sleep(0.05)
        assert "9.9.9" in view.update_text.get()
        assert view.download_button.winfo_manager() == "pack"
        opened = []
        monkeypatch.setattr(panel.webbrowser, "open", lambda url: opened.append(url))
        view.is_running = True
        view.download()
        assert opened == [updates.RELEASES_PAGE]
        assert view.notice.get().startswith("Before running the installer: press Stop, then close")
        view.is_running = False
        view.download()
        assert view.notice.get().startswith("Before running the installer, close this Control Panel")
        view.updates_on.set(False)
        view.toggle_updates()
        assert view.update_text.get() == ""
        assert updates.enabled(tmp_path) is False
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
