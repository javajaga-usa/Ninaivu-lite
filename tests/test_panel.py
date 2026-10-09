"""The Control Panel: built for real where Tk and a display exist, and its
cards' colours checked everywhere, because the window is not covered by the
server's tests and a missing colour once crashed it before it appeared."""

from __future__ import annotations

import os
import re
import sys
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
    """The whole window, with a data folder of its own; nothing is asked of the internet."""
    from ninaivu_lite.control import Controller

    (tmp_path / "update-check.json").write_text('{"enabled": true}', encoding="utf-8")
    root = _tk_root()
    try:
        # (One window for the whole test: Tk on macOS aborts when a second
        # root is made after the first was destroyed.)
        view = panel.Panel(root, Controller(str(tmp_path)))
        root.update()
        # What the old update check kept goes.
        assert not (tmp_path / "update-check.json").exists()
        # A drive plugged in: the question builds over the panel, and closing
        # its window is Not now.
        import tkinter as tk

        from ninaivu_lite import drives
        root.after(300, lambda: [w.destroy() for w in root.winfo_children()
                                 if isinstance(w, tk.Toplevel)])
        assert view.ask_drive(drives.Drive("usb1", str(tmp_path), "PENDRIVE", 1, 1)) is None
        # A server left running from before an update is pointed out, once.
        view.say_if_stale("0.0.1")
        assert "0.0.1 is still running from before the update" in view.notice.get()
        view.notice.set("something else")
        view.say_if_stale("0.0.1")
        assert view.notice.get() == "something else"
        view.say_if_stale(panel.__version__)
        assert view.stale_version is None
        # No update section: the panel opens on what is going on, not on updating.
        assert "updates" not in panel.CARD_ACCENT
        view.close()
        assert view.finished.is_set()
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
