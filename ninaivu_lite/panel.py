"""Ninaivu Lite's Control Panel: Ninaivu's Control Panel, lighter.

    python -m ninaivu_lite.panel
    Ninaivu Lite.exe                           (Windows portable copy)
    Start - Ninaivu Lite Control Panel.vbs     (Windows, a copy of the source)
    ./start.sh --panel                         (macOS, Linux)

One small window: whether Ninaivu Lite is running and where, Start, Stop and
Restart, the gallery and console a click away, the addresses for phones, the
library at a glance, starting with the computer, and the log. Closing it
leaves Ninaivu Lite running. It needs only Tk, which comes with Python from
python.org; the resource modes and readings of Ninaivu's panel are not here.

The look is drawn, not borrowed from a theme: a dark band with the name and a
status pill, rounded cards and buttons, and line icons, all on Tk canvases so
the window looks the same on Windows, macOS and Linux and needs nothing more
than Tk. Sizes go through px(), so a sharp screen gets the same layout, larger.

Background work never blocks Tk: readings and actions run on threads and come
back through a queue the window empties on its own timer, because a start can
take a while and a window that freezes meanwhile looks like one that crashed.
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from . import drives
from .control import Controller
from .version import APP_NAME, COPYRIGHT, LICENCE, __version__

TITLE = f"{APP_NAME} Control Panel"
ICON = Path(__file__).resolve().parent / "static" / "icons" / "icon-192.png"

# -- the palette ---------------------------------------------------------------------------
BG = "#f3f5f9"
SURFACE = "#ffffff"
INK = "#0f172a"
MUTED = "#475569"
FAINT = "#64748b"
ACCENT = "#2563eb"
LINK = "#1d4ed8"
BORDER = "#e2e8f0"
SHADOW = "#e6eaf1"
HEADER = ("#0b1324", "#1e3a8a")          # the band, left to right
HEADER_SUB = "#a5b4cf"
STATUS = {  # pill fill, its edge, the dot, the words: on the dark band
    "running": ("#0c3b2e", "#10b981", "#34d399", "#d1fae5"),
    "stopped": ("#1e293b", "#475569", "#94a3b8", "#e2e8f0"),
    "busy": ("#1e3a8a", "#60a5fa", "#93c5fd", "#dbeafe"),
}
#: each card's colour, the tint behind its icon, and the icon
CARD_ACCENT = {
    "addresses": ("#059669", "#d1fae5", "globe"),
    "library": ("#2563eb", "#dbeafe", "photos"),
    "options": ("#7c3aed", "#ede9fe", "computer"),
}
BUTTON = {  # fill, words, edge, fill under the pointer, fill pressed
    "primary": (ACCENT, "#ffffff", ACCENT, "#1d4ed8", "#1e40af"),
    "success": ("#059669", "#ffffff", "#059669", "#047857", "#065f46"),
    "danger": ("#fef2f2", "#b91c1c", "#fecaca", "#fee2e2", "#fecaca"),
    "secondary": (SURFACE, "#1e293b", "#cbd5e1", "#f1f5f9", "#e2e8f0"),
    "ghost": (SURFACE, FAINT, SURFACE, "#f1f5f9", "#e2e8f0"),
}
DISABLED = ("#eef1f6", "#a3aec2", "#e2e8f0")

# Chosen again when the window opens, from the fonts this computer has.
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
FONT_SEMI = "Segoe UI Semibold" if sys.platform == "win32" else None
MONO = "Consolas" if sys.platform == "win32" else "Courier"
#: screen pixels per pixel of the design (96 dpi = 1)
SCALE = 1.0


def px(n: float) -> int:
    return max(1, round(n * SCALE))


def semi(size: int) -> tuple:
    """Semibold where the font has it as a family of its own (Segoe UI), else bold."""
    return (FONT_SEMI, size) if FONT_SEMI else (FONT, size, "bold")


def choose_fonts(root) -> None:
    """The nicest font this computer has, and how sharp its screen is."""
    global FONT, FONT_SEMI, MONO, SCALE
    import tkinter.font as tkfont
    try:
        SCALE = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        families = set(tkfont.families(root))
    except Exception:  # noqa: BLE001 — the defaults above will do
        return
    for name, semibold in (("Segoe UI", "Segoe UI Semibold"), ("SF Pro Text", None),
                           ("Helvetica Neue", None), ("Inter", None),
                           ("Noto Sans", None), ("DejaVu Sans", None)):
        if name in families:
            FONT, FONT_SEMI = name, semibold if semibold in families else None
            break
    for name in ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono"):
        if name in families:
            MONO = name
            break


def tell(message: str) -> None:
    """Say why the panel cannot open, with whatever this computer has."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(TITLE, message, parent=root)
        root.destroy()
        return
    except Exception:  # noqa: BLE001 — no Tk: try the next way
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, TITLE, 0x10)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"{TITLE}: {message}", file=sys.stderr)


# -- drawing ---------------------------------------------------------------------------------


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    """A rectangle with rounded corners, as one smoothed polygon."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def icon(canvas, name: str, x: float, y: float, s: float, color: str, tag="icon") -> None:
    """A small line icon, *s* pixels square, centred on x, y."""
    h = s / 2
    w = max(1.0, s / 9)
    line = {"fill": color, "width": w, "tags": tag, "capstyle": "round", "joinstyle": "round"}
    box = {"outline": color, "width": w, "tags": tag}
    if name == "play":
        canvas.create_polygon(x - h * .55, y - h * .8, x + h * .8, y, x - h * .55, y + h * .8,
                              fill=color, outline=color, width=w, joinstyle="round", tags=tag)
    elif name == "stop":
        canvas.create_rectangle(x - h * .62, y - h * .62, x + h * .62, y + h * .62,
                                fill=color, outline=color, width=w, tags=tag)
    elif name == "restart":
        r = h * .72
        canvas.create_arc(x - r, y - r, x + r, y + r, start=70, extent=290, style="arc", **box)
        ax, ay = x + r * math.cos(math.radians(70)), y - r * math.sin(math.radians(70))
        canvas.create_polygon(ax - h * .05, ay - h * .42, ax + h * .45, ay + h * .02,
                              ax - h * .12, ay + h * .38, fill=color, outline=color, tags=tag)
    elif name == "open":
        canvas.create_line(x + h * .1, y - h * .75, x - h * .75, y - h * .75, x - h * .75,
                           y + h * .75, x + h * .75, y + h * .75, x + h * .75, y - h * .1, **line)
        canvas.create_line(x - h * .1, y + h * .1, x + h * .8, y - h * .8, **line)
        canvas.create_line(x + h * .2, y - h * .8, x + h * .8, y - h * .8, x + h * .8,
                           y - h * .2, **line)
    elif name == "console":
        canvas.create_rectangle(x - h * .85, y - h * .7, x + h * .85, y + h * .7, **box)
        canvas.create_line(x - h * .85, y - h * .3, x + h * .85, y - h * .3, **line)
        canvas.create_line(x - h * .45, y + h * .05, x - h * .15, y + h * .25, x - h * .45,
                           y + h * .45, **line)
        canvas.create_line(x + h * .05, y + h * .45, x + h * .45, y + h * .45, **line)
    elif name == "folder":
        canvas.create_line(x - h * .85, y + h * .65, x - h * .85, y - h * .65, x - h * .25,
                           y - h * .65, x - h * .05, y - h * .4, x + h * .85, y - h * .4,
                           x + h * .85, y + h * .65, x - h * .85, y + h * .65, **line)
    elif name == "log":
        canvas.create_rectangle(x - h * .65, y - h * .85, x + h * .65, y + h * .85, **box)
        for dy in (-.4, 0, .4):
            canvas.create_line(x - h * .3, y + h * dy, x + h * .3, y + h * dy, **line)
    elif name == "copy":
        canvas.create_rectangle(x - h * .35, y - h * .35, x + h * .8, y + h * .8, **box)
        canvas.create_line(x - h * .8, y + h * .35, x - h * .8, y - h * .8, x + h * .35,
                           y - h * .8, **line)
    elif name == "globe":
        canvas.create_oval(x - h * .85, y - h * .85, x + h * .85, y + h * .85, **box)
        canvas.create_oval(x - h * .38, y - h * .85, x + h * .38, y + h * .85, **box)
        canvas.create_line(x - h * .85, y, x + h * .85, y, **line)
    elif name == "photos":
        canvas.create_rectangle(x - h * .85, y - h * .7, x + h * .85, y + h * .7, **box)
        canvas.create_line(x - h * .85, y + h * .45, x - h * .25, y - h * .1, x + h * .15,
                           y + h * .3, x + h * .4, y + h * .05, x + h * .85, y + h * .45, **line)
        canvas.create_oval(x + h * .2, y - h * .45, x + h * .5, y - h * .15, fill=color,
                           outline=color, tags=tag)
    elif name == "computer":
        canvas.create_rectangle(x - h * .85, y - h * .7, x + h * .85, y + h * .4, **box)
        canvas.create_line(x, y + h * .4, x, y + h * .8, **line)
        canvas.create_line(x - h * .45, y + h * .8, x + h * .45, y + h * .8, **line)
    elif name == "info":
        canvas.create_oval(x - h, y - h, x + h, y + h, fill=color, outline=color, tags=tag)
        canvas.create_line(x, y - h * .05, x, y + h * .5, fill="#ffffff", width=w * 1.2,
                           capstyle="round", tags=tag)
        canvas.create_oval(x - w * .8, y - h * .5 - w * .8, x + w * .8, y - h * .5 + w * .8,
                           fill="#ffffff", outline="#ffffff", tags=tag)
    elif name == "drive":
        canvas.create_rectangle(x - h * .85, y - h * .45, x + h * .85, y + h * .45, **box)
        canvas.create_oval(x + h * .45, y - w, x + h * .45 + 2 * w, y + w, fill=color,
                           outline=color, tags=tag)


class RoundButton:
    """A rounded button with an icon, drawn on a canvas. It answers state()
    as a ttk button does, so the panel enables and disables it the same way,
    and Tab, Space and Enter work as on any button."""

    def __init__(self, parent, text: str, command, kind: str = "secondary",
                 glyph: str | None = None, small: bool = False, bg: str = BG,
                 font=None) -> None:
        import tkinter as tk
        import tkinter.font as tkfont
        self.text, self.command, self.kind, self.glyph = text, command, kind, glyph
        self.disabled = self.hover = self.pressed = self.focused = False
        self.font = tkfont.Font(font=font or semi(10 if not small else 9))
        self.pad = px(10 if small else 14)
        self.size = px(13 if small else 14)
        self.gap = px(7) if text else 0
        width = 2 * self.pad + (self.size + self.gap if glyph else 0) + self.font.measure(text)
        height = px(30 if small else 36)
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=bg, highlightthickness=0,
                                bd=0, takefocus=1, cursor="hand2")
        c = self.canvas
        c.bind("<Configure>", lambda _e: self.draw())
        c.bind("<Enter>", lambda _e: self._set(hover=True))
        c.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        c.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<FocusIn>", lambda _e: self._set(focused=True))
        c.bind("<FocusOut>", lambda _e: self._set(focused=False))
        c.bind("<KeyPress-space>", lambda _e: self.invoke())
        c.bind("<Return>", lambda _e: self.invoke())
        self.draw()

    # what Tk's geometry managers need
    def pack(self, **kw):
        self.canvas.pack(**kw)
        return self

    def grid(self, **kw):
        self.canvas.grid(**kw)
        return self

    def state(self, spec=None):
        """As ttk: state(["disabled"]) or state(["!disabled"]); returns the state."""
        for flag in spec or ():
            if flag in ("disabled", "!disabled"):
                self.disabled = flag == "disabled"
                self.canvas.configure(cursor="" if self.disabled else "hand2",
                                      takefocus=0 if self.disabled else 1)
                self.draw()
        return ("disabled",) if self.disabled else ()

    def instate(self, spec) -> bool:
        return all(("disabled" in self.state()) == (f == "disabled") for f in spec)

    def invoke(self) -> None:
        if not self.disabled and self.command:
            self.command()

    def _release(self, event) -> None:
        inside = 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height()
        self._set(pressed=False)
        if inside:
            self.invoke()

    def _set(self, **flags) -> None:
        for name, value in flags.items():
            setattr(self, name, value)
        self.draw()

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), int(c.cget("width")) if c.winfo_width() <= 1 else 0)
        h = max(c.winfo_height(), int(c.cget("height")) if c.winfo_height() <= 1 else 0)
        fill, fg, edge, over, down = BUTTON[self.kind]
        if self.disabled:
            fill, fg, edge = DISABLED
        elif self.pressed:
            fill = down
        elif self.hover:
            fill = over
        r = px(8)
        if self.focused and not self.disabled:
            rounded(c, 0, 0, w - 1, h - 1, r + 1, fill="", outline=ACCENT, width=px(2))
            inset = px(3)
        else:
            inset = px(1)
        rounded(c, inset, inset, w - 1 - inset, h - 1 - inset, r, fill=fill, outline=edge)
        content = (self.size + self.gap if self.glyph else 0) + self.font.measure(self.text)
        x = (w - content) / 2
        if self.glyph:
            icon(c, self.glyph, x + self.size / 2, h / 2, self.size, fg)
            x += self.size + self.gap
        c.create_text(x, h / 2, text=self.text, anchor="w", font=self.font, fill=fg)


class TickBox:
    """A tick box bound to a BooleanVar, drawn to match the buttons (rounded,
    a white tick on the accent blue); *command* runs after a change."""

    def __init__(self, parent, variable, command, bg: str = SURFACE) -> None:
        import tkinter as tk
        self.variable, self.command = variable, command
        self.focused = False
        self.canvas = tk.Canvas(parent, width=px(22), height=px(22), bg=bg,
                                highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        c = self.canvas
        c.bind("<Button-1>", lambda _e: self.flip())
        c.bind("<KeyPress-space>", lambda _e: self.flip())
        c.bind("<FocusIn>", lambda _e: self._focus(True))
        c.bind("<FocusOut>", lambda _e: self._focus(False))
        variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def _focus(self, on: bool) -> None:
        self.focused = on
        self.draw()

    def flip(self) -> None:
        self.variable.set(not self.variable.get())
        self.command()

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        s = px(22)
        on = bool(self.variable.get())
        m = px(2)
        edge = ACCENT if on or self.focused else "#94a3b8"
        rounded(c, m, m, s - m, s - m, px(6), fill=ACCENT if on else SURFACE, outline=edge,
                width=px(2) if self.focused else max(1, px(1.5)))
        if on:
            c.create_line(s * .28, s * .52, s * .44, s * .68, s * .73, s * .34, fill="#ffffff",
                          width=max(2, px(2.4)), capstyle="round", joinstyle="round")


class Card:
    """A rounded panel with a soft shadow; widgets go in .body (a Frame)."""

    def __init__(self, parent, fill: str = SURFACE, edge: str = BORDER, pad: int = 16,
                 shadow: bool = True) -> None:
        import tkinter as tk
        self.fill, self.edge, self.shadow = fill, edge, shadow
        self.r = px(12)
        self.inset = px(4)
        self.canvas = tk.Canvas(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0,
                                height=1)
        self.body = tk.Frame(self.canvas, bg=fill, padx=px(pad) - self.inset,
                             pady=px(pad) - self.inset)
        self.window = self.canvas.create_window(self.inset, self.inset, anchor="nw",
                                                window=self.body)
        self.body.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)

    def _fit(self, _event=None) -> None:
        c = self.canvas
        height = self.body.winfo_reqheight() + 2 * self.inset + px(2)
        if int(c.cget("height")) != height:
            c.configure(height=height)
        width = c.winfo_width()
        if width > 1:
            c.itemconfigure(self.window, width=width - 2 * self.inset)
        c.delete("frame")
        # Stretched by its grid cell (a card beside a taller one): fill it.
        w, h = max(width, 2 * self.r), max(height, c.winfo_height())
        if self.shadow:
            rounded(c, 1, px(2), w - 1, h - 1, self.r, fill=SHADOW, outline=SHADOW, tags="frame")
        rounded(c, 0, 0, w - 1, h - px(2), self.r, fill=self.fill, outline=self.edge,
                tags="frame")
        c.tag_lower("frame")


class Panel:
    def __init__(self, root, controller: Controller) -> None:
        import tkinter as tk
        import tkinter.font as tkfont
        from tkinter import ttk

        self.tk = tk
        self.root = root
        self.controller = controller
        self.events: queue.Queue = queue.Queue()
        self.finished = threading.Event()
        self.busy = False
        self.is_running = False
        #: the version of a server still running from before an update, once said
        self.stale_version: str | None = None
        #: pendrives and external drives plugged in while the panel is open
        self.drive_watcher = drives.Watcher()
        # What the update check of 1.3.4 to 1.8.0 kept; there is no check now.
        try:
            (controller.data_dir / "update-check.json").unlink(missing_ok=True)
        except OSError:
            pass

        choose_fonts(root)
        root.title(TITLE)
        root.configure(bg=BG)
        self.icon = self.badge_icon = None
        try:
            self.icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self.icon)
            self.badge_icon = self.icon.subsample(max(1, round(192 / px(40))))
        except tk.TclError:
            pass
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                font = tkfont.nametofont(name)
                font.configure(family=FONT)
                if font.cget("size") < 10:
                    font.configure(size=10)
            except tk.TclError:
                pass

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Lane.Horizontal.TProgressbar", troughcolor=BG, background=ACCENT,
                        bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=px(4))

        # -- the band: icon, name, status ------------------------------------------------
        self.status = tk.StringVar(value="Checking…")
        self.status_kind = "stopped"      # until _paint_status below
        self.pulse = False
        self.header = tk.Canvas(root, height=px(76), bg=HEADER[0], highlightthickness=0, bd=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", lambda _e: self._draw_header())
        self.status.trace_add("write", lambda *_: self._draw_pill())

        outer = tk.Frame(root, bg=BG, padx=px(20), pady=px(16))
        outer.pack(fill="both", expand=True)

        # -- what can be done now -----------------------------------------------------------
        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x")
        self.start_button = RoundButton(actions, "Start", glyph="play", kind="success",
                                        command=lambda: self.run(controller.start, "Starting…"))
        self.stop_button = RoundButton(actions, "Stop", glyph="stop", kind="danger",
                                       command=lambda: self.stop_asking(controller.stop,
                                                                        "Stopping…"))
        self.restart_button = RoundButton(actions, "Restart", glyph="restart",
                                          command=lambda: self.stop_asking(controller.restart,
                                                                           "Restarting…"))
        for button in (self.start_button, self.stop_button, self.restart_button):
            button.pack(side="left", padx=(0, px(8)))
        self.console_button = RoundButton(actions, "Open the console", glyph="console",
                                          command=lambda: webbrowser.open(controller.url(True)))
        self.app_button = RoundButton(actions, "Open the family app", glyph="open",
                                      kind="primary",
                                      command=lambda: webbrowser.open(controller.url()))
        self.console_button.pack(side="right")
        self.app_button.pack(side="right", padx=(0, px(8)))
        # Room for a thin progress bar that shows only while something is happening.
        lane = tk.Frame(outer, bg=BG, height=px(4))
        lane.pack(fill="x", pady=(px(10), px(6)))
        self.progress = ttk.Progressbar(lane, mode="indeterminate",
                                        style="Lane.Horizontal.TProgressbar")

        # -- cards ----------------------------------------------------------------------------
        self.address_rows: list | None = None
        self.link_fonts = (tkfont.Font(font=semi(11)), tkfont.Font(font=semi(11)))
        self.link_fonts[1].configure(underline=True)
        self.library_shown: tuple | None = None
        self.address_box = self._card(outer, "addresses", "Where to open it")
        pair = tk.Frame(outer, bg=BG)
        pair.pack(fill="x")
        pair.columnconfigure(0, weight=1, uniform="pair")
        pair.columnconfigure(1, weight=1, uniform="pair")
        self.library_box = self._card(pair, "library", "Library",
                                      grid={"row": 0, "column": 0, "sticky": "nsew",
                                            "padx": (0, px(6))})
        body = self._card(pair, "options", "This computer",
                          grid={"row": 0, "column": 1, "sticky": "nsew", "padx": (px(6), 0)})
        if controller.autostart_supported():
            self.at_sign_in = tk.BooleanVar(value=controller.autostart_enabled())
            row = tk.Frame(body, bg=SURFACE)
            row.pack(fill="x", pady=(0, px(10)))
            TickBox(row, self.at_sign_in, self.toggle_autostart).canvas.pack(side="left")
            words = tk.Label(row, text="Start Ninaivu Lite when I sign in", font=(FONT, 10),
                             bg=SURFACE, fg=INK, cursor="hand2")
            words.pack(side="left", padx=(px(10), 0))
            words.bind("<Button-1>", lambda _e: (self.at_sign_in.set(not self.at_sign_in.get()),
                                                 self.toggle_autostart()))
            if controller.autostart_elsewhere():
                tk.Label(body, text="Another copy of Ninaivu Lite (an older folder?) starts when "
                         "you sign in. Tick the box to start this one instead.",
                         font=(FONT, 9), bg=SURFACE, fg=FAINT, justify="left", anchor="w",
                         wraplength=px(300)).pack(fill="x", pady=(0, px(10)))
        RoundButton(body, "Open the data folder", glyph="folder", small=True, bg=SURFACE,
                    command=lambda: self.reveal(controller.data_dir)).pack(fill="x",
                                                                           pady=(0, px(6)))
        RoundButton(body, "Open the log", glyph="log", small=True, bg=SURFACE,
                    command=lambda: self.reveal(controller.log_file)).pack(fill="x")

        # -- what just happened -------------------------------------------------------------
        self.notice = tk.StringVar(value="Closing this panel leaves Ninaivu Lite running.")
        strip = Card(outer, fill="#eaf0fb", edge="#d6e2f7", pad=12, shadow=False)
        strip.canvas.pack(fill="x", pady=(px(12), 0))
        mark = tk.Canvas(strip.body, width=px(16), height=px(16), bg="#eaf0fb",
                         highlightthickness=0, bd=0)
        icon(mark, "info", px(8), px(8), px(14), ACCENT)
        mark.pack(side="left", anchor="n", pady=(px(2), 0))
        tk.Label(strip.body, textvariable=self.notice, font=(FONT, 10), bg="#eaf0fb",
                 fg="#1e3a5f", wraplength=px(640), justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True, padx=(px(10), 0))

        # -- who made it ----------------------------------------------------------------------
        tk.Label(outer, text=f"{APP_NAME} {__version__}   ·   {COPYRIGHT}   ·   {LICENCE} licence",
                 font=(FONT, 9), bg=BG, fg=FAINT).pack(pady=(px(14), 0))

        self._paint_status("busy")
        self._set_buttons()
        self.show_library([], None)
        # Placed, not sized: the window keeps fitting its contents as the
        # addresses and the library fill in.
        root.minsize(px(760), 1)
        root.update_idletasks()
        width, height = max(root.winfo_reqwidth(), px(760)), root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 3)
        root.geometry(f"+{x}+{y}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.watch, name="panel-watch", daemon=True).start()
        root.after(150, self.pump)

    # -- building --------------------------------------------------------------------------

    def _card(self, parent, key: str, title: str, grid: dict | None = None):
        tk = self.tk
        accent, tint, glyph = CARD_ACCENT[key]
        card = Card(parent)
        if grid:
            card.canvas.grid(**grid)
        else:
            card.canvas.pack(fill="x", pady=(0, px(12)))
        head = tk.Frame(card.body, bg=SURFACE)
        head.pack(fill="x", pady=(0, px(10)))
        badge = tk.Canvas(head, width=px(30), height=px(30), bg=SURFACE, highlightthickness=0,
                          bd=0)
        rounded(badge, 0, 0, px(30) - 1, px(30) - 1, px(8), fill=tint, outline=tint)
        icon(badge, glyph, px(15), px(15), px(16), accent)
        badge.pack(side="left")
        tk.Label(head, text=title, font=semi(11), bg=SURFACE, fg=INK).pack(side="left",
                                                                         padx=(px(10), 0))
        content = tk.Frame(card.body, bg=SURFACE)   # its own frame, so a card may use grid
        content.pack(fill="x")
        return content

    def _draw_header(self) -> None:
        c = self.header
        c.delete("all")
        w, h = max(c.winfo_width(), px(760)), px(76)
        # The band: a gradient, a stripe at a time.
        (r1, g1, b1), (r2, g2, b2) = (tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
                                      for col in HEADER)
        step = 4
        for x in range(0, w, step):
            t = x / max(1, w)
            rgb = (round(a + (b - a) * t) for a, b in ((r1, r2), (g1, g2), (b1, b2)))
            colour = "#" + "".join(f"{v:02x}" for v in rgb)
            c.create_rectangle(x, 0, x + step, h, fill=colour, outline=colour)
        left = px(20)
        if self.badge_icon is not None:
            c.create_image(left, h / 2, image=self.badge_icon, anchor="w")
            left += self.badge_icon.width() + px(12)
        c.create_text(left, h / 2 - px(9), text=APP_NAME, anchor="w", font=semi(17),
                      fill="#ffffff")
        c.create_text(left + px(1), h / 2 + px(14), text="Control Panel", anchor="w",
                      font=(FONT, 10), fill=HEADER_SUB)
        self._draw_pill()

    def _draw_pill(self) -> None:
        c = self.header
        c.delete("pill")
        w, h = max(c.winfo_width(), px(760)), px(76)
        fill, edge, dot, words = STATUS[self.status_kind]
        import tkinter.font as tkfont
        font = tkfont.Font(font=semi(10))
        text = self.status.get()
        pw, ph = font.measure(text) + px(44), px(30)
        x2, y1 = w - px(20), (h - ph) / 2
        rounded(c, x2 - pw, y1, x2, y1 + ph, ph, fill=fill, outline=edge, tags="pill")
        d = px(8)
        lit = dot if not (self.status_kind == "busy" and self.pulse) else edge
        c.create_oval(x2 - pw + px(14), y1 + (ph - d) / 2, x2 - pw + px(14) + d,
                      y1 + (ph + d) / 2, fill=lit, outline=lit, tags="pill")
        c.create_text(x2 - pw + px(30), y1 + ph / 2, text=text, anchor="w", font=font,
                      fill=words, tags="pill")

    def _paint_status(self, kind: str) -> None:
        was = self.status_kind
        self.status_kind = kind
        self._draw_pill()
        if kind == "busy" and was != "busy":
            self.root.after(500, self._pulse)

    def _pulse(self) -> None:
        """The dot breathes while something is happening."""
        if self.status_kind != "busy" or self.finished.is_set():
            self.pulse = False
            return
        self.pulse = not self.pulse
        self._draw_pill()
        self.root.after(500, self._pulse)

    def _set_buttons(self) -> None:
        idle = not self.busy
        self.start_button.state(["!disabled"] if idle and not self.is_running else ["disabled"])
        for button in (self.stop_button, self.restart_button):
            button.state(["!disabled"] if idle and self.is_running else ["disabled"])
        for button in (self.app_button, self.console_button):
            button.state(["!disabled"] if self.is_running else ["disabled"])

    def copy(self, value: str, what: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.notice.set(f"Copied {what}: {value}")

    # -- work off the Tk thread -------------------------------------------------------------

    def watch(self) -> None:
        """Every couple of seconds: is it running, where, and what is in the library."""
        from . import net
        while not self.finished.is_set():
            try:
                health = self.controller.health()
                running = health is not None
                port = self.controller.port
                phones = net.lan_addresses()[:2] if running else []
                summary = self.controller.library_summary()
                self.events.put(("reading", running, port, phones, summary,
                                 self.controller.can_stop(),
                                 self.controller.setup_code() if running else None,
                                 health.get("version") if running else None))
                if running:
                    self.look_for_drives(summary.get("folders") or [])
            except Exception as exc:  # noqa: BLE001 — a bad reading must not end the loop
                self.events.put(("notice", f"Could not read the status: {exc}"))
            self.finished.wait(2.5)

    def run(self, action, label: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        self._paint_status("busy")
        self.progress.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.progress.start(12)
        self._set_buttons()

        def work():
            try:
                message = action()
            except Exception as exc:  # noqa: BLE001 — said, not raised
                message = f"Something went wrong: {exc}"
            self.events.put(("done", message))

        threading.Thread(target=work, name="panel-action", daemon=True).start()

    def pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "reading":
                    self.show_reading(*event[1:])
                elif event[0] == "done":
                    self.busy = False
                    self.progress.stop()
                    self.progress.place_forget()
                    self.notice.set(event[1])
                    self._set_buttons()
                elif event[0] == "notice":
                    self.notice.set(event[1])
                elif event[0] == "drive":
                    self.offer_drive(event[1])
        except queue.Empty:
            pass
        if not self.finished.is_set():
            self.root.after(150, self.pump)


    def show_reading(self, running, port, phones, summary, can_stop, setup_code=None,
                     version=None) -> None:
        self.is_running = running
        if version is not None or not running:
            self.say_if_stale(version)
        if not self.busy:
            self.status.set("Running" if running else "Stopped")
            self._paint_status("running" if running else "stopped")
        if running:
            rows = [("On this computer", f"http://localhost:{port}/"),
                    ("Admin console", f"http://localhost:{port}/admin")]
            rows += [("On your phone", f"http://{a}:{port}/") for a in phones]
            if not can_stop:
                rows.append(("", "Started from its own window: stop it there."))
            if setup_code:      # until the first administrator is made
                rows.append(("Setup code", setup_code))
                rows.append(("", "Asked for when the administrator is made from another device."))
        else:
            rows = [("", "Not running. Press Start, then open it on this computer "
                         "or on a phone on the same Wi-Fi.")]
        self.show_addresses(rows)
        self.show_library(summary.get("folders") or [], summary.get("items"))
        self._set_buttons()

    def show_addresses(self, rows: list) -> None:
        if rows == self.address_rows:
            return
        self.address_rows = rows
        box = self.address_box
        for child in box.grid_slaves():
            child.destroy()
        tk = self.tk
        box.columnconfigure(1, weight=1)
        for i, (label, value) in enumerate(rows, start=1):
            gap = (px(3), px(3))
            if not label:
                tk.Label(box, text=value, font=(FONT, 9), bg=SURFACE, fg=FAINT, anchor="w",
                         wraplength=px(640), justify="left").grid(row=i, column=0, columnspan=3,
                                                                  sticky="w", pady=gap)
                continue
            tk.Label(box, text=label, font=(FONT, 10), bg=SURFACE, fg=MUTED,
                     anchor="w").grid(row=i, column=0, sticky="w", padx=(0, px(20)), pady=gap)
            if label == "Setup code":
                chip = tk.Frame(box, bg="#fffbeb", highlightthickness=1,
                                highlightbackground="#fcd34d", padx=px(10), pady=px(2))
                chip.grid(row=i, column=1, sticky="w", pady=(px(6), px(2)))
                tk.Label(chip, text=value, font=(MONO, 12, "bold"), bg="#fffbeb",
                         fg="#92400e").pack()
                what = "the setup code"
            else:
                link = tk.Label(box, text=value, font=self.link_fonts[0], bg=SURFACE, fg=LINK,
                                anchor="w", cursor="hand2")
                link.grid(row=i, column=1, sticky="w", pady=gap)
                link.bind("<Button-1>", lambda _e, url=value: webbrowser.open(url))
                link.bind("<Enter>", lambda _e, w=link: w.configure(font=self.link_fonts[1]))
                link.bind("<Leave>", lambda _e, w=link: w.configure(font=self.link_fonts[0]))
                what = "the address"
            RoundButton(box, "Copy", glyph="copy", kind="ghost", small=True, bg=SURFACE,
                        font=(FONT, 9),
                        command=lambda v=value, w=what: self.copy(v, w)).grid(row=i, column=2,
                                                                              sticky="e")

    def show_library(self, folders: list, items) -> None:
        """How much is indexed, large, and the first few folders under it."""
        shown = (tuple(folders), items)
        if shown == self.library_shown:
            return
        self.library_shown = shown
        tk = self.tk
        box = self.library_box
        for child in box.winfo_children():
            child.destroy()
        if not folders:
            tk.Label(box, text="No photo folder yet. Start Ninaivu Lite and open the console "
                     "to choose one.", font=(FONT, 10), bg=SURFACE, fg=MUTED, justify="left",
                     anchor="w", wraplength=px(300)).pack(fill="x")
            return
        stat = tk.Frame(box, bg=SURFACE)
        stat.pack(fill="x", pady=(0, px(8)))
        if items is not None:
            tk.Label(stat, text=f"{items:,}", font=semi(22), bg=SURFACE,
                     fg=INK).pack(side="left", anchor="s")
            tk.Label(stat, text="photos and videos indexed", font=(FONT, 10), bg=SURFACE,
                     fg=MUTED).pack(side="left", anchor="s", padx=(px(8), 0), pady=(0, px(5)))
        else:
            tk.Label(stat, text="Not indexed yet", font=semi(12), bg=SURFACE,
                     fg=MUTED).pack(side="left")
        for folder in folders[:3]:
            row = tk.Frame(box, bg=SURFACE)
            row.pack(fill="x", pady=px(1))
            mark = tk.Canvas(row, width=px(16), height=px(16), bg=SURFACE, highlightthickness=0,
                             bd=0)
            icon(mark, "folder", px(8), px(8), px(14), "#2563eb")
            mark.pack(side="left", anchor="n", pady=(px(2), 0))
            tk.Label(row, text=folder, font=(FONT, 9), bg=SURFACE, fg=MUTED, justify="left",
                     anchor="w", wraplength=px(270)).pack(side="left", fill="x",
                                                          padx=(px(8), 0))
        if len(folders) > 3:
            tk.Label(box, text=f"and {len(folders) - 3} more", font=(FONT, 9), bg=SURFACE,
                     fg=FAINT, anchor="w").pack(fill="x", padx=(px(24), 0))

    # -- a drive plugged in ------------------------------------------------------------------

    def look_for_drives(self, folders: list[str]) -> None:
        """On the watch thread: each drive or phone newly plugged in is offered once.
        The drive the library or the data folder lives on is not a visitor."""
        home = [*folders, str(self.controller.data_dir)]
        for drive in self.drive_watcher.pending():
            self.drive_watcher.answer(drive.id)
            if drive.kind == "phone" or not any(drives.is_within(path, drive.path)
                                                for path in home if path):
                self.events.put(("drive", drive))

    def offer_drive(self, drive: drives.Drive) -> None:
        """The question, in Tamil and English, since the panel has no language
        of its own; the answer is carried out in the console."""
        choice = self.ask_drive(drive)
        if choice not in ("import", "export"):
            return
        query = urlencode({"drive": drive.path, "do": choice})
        webbrowser.open(f"{self.controller.url(True)}?{query}")
        self.notice.set("Opening the console in your browser to "
                        + ("import from " if choice == "import" else "copy the library to ")
                        + f"{drive.label}.")

    def ask_drive(self, drive: drives.Drive) -> str | None:
        """A small window over the panel: Import, Export or Not now. A test
        replaces it."""
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title(TITLE)
        win.configure(bg=SURFACE)
        win.transient(self.root)
        win.resizable(False, False)
        answer: dict[str, str | None] = {"choice": None}

        def pick(choice: str | None) -> None:
            answer["choice"] = choice
            win.destroy()

        body = tk.Frame(win, bg=SURFACE, padx=px(24), pady=px(20))
        body.pack(fill="both", expand=True)
        phone = drive.kind == "phone"
        name = drive.label if drive.label == drive.path or drive.shell \
            else f"{drive.label} ({drive.path})"
        heading = ("ஒரு தொலைபேசி இணைக்கப்பட்டது  ·  A phone was connected" if phone
                   else "ஒரு டிரைவ் இணைக்கப்பட்டது  ·  A drive was connected")
        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        badge = tk.Canvas(top, width=px(40), height=px(40), bg=SURFACE, highlightthickness=0,
                          bd=0)
        rounded(badge, 0, 0, px(40) - 1, px(40) - 1, px(10), fill="#dbeafe", outline="#dbeafe")
        icon(badge, "drive", px(20), px(20), px(22), ACCENT)
        badge.pack(side="left", anchor="n")
        words = tk.Frame(top, bg=SURFACE)
        words.pack(side="left", fill="x", padx=(px(14), 0))
        tk.Label(words, text=heading, font=semi(13), bg=SURFACE, fg=INK).pack(anchor="w")
        tk.Label(words, text=name, font=(FONT, 10), bg=SURFACE, fg=FAINT).pack(anchor="w",
                                                                              pady=(px(2), 0))
        tk.Label(body, text="இதை வைத்து என்ன செய்ய விரும்புகிறீர்கள்?\n"
                            "What would you like to do with it?",
                 font=(FONT, 10), bg=SURFACE, fg=INK, justify="left").pack(anchor="w",
                                                                         pady=(px(16), 0))
        buttons = tk.Frame(body, bg=SURFACE)
        buttons.pack(fill="x", pady=(px(14), 0))
        RoundButton(buttons, "இறக்குமதி  ·  Import media from this "
                             + ("phone" if phone else "drive"),
                    kind="primary", bg=SURFACE,
                    command=lambda: pick("import")).pack(fill="x", pady=(0, px(8)))
        if not phone:   # a phone's storage is no place for the whole library
            RoundButton(buttons, "ஏற்றுமதி  ·  Export media to this drive", bg=SURFACE,
                        command=lambda: pick("export")).pack(fill="x", pady=(0, px(8)))
        RoundButton(buttons, "இப்போது வேண்டாம்  ·  Not now", kind="ghost", bg=SURFACE,
                    command=lambda: pick(None)).pack(fill="x")
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        win.bind("<Escape>", lambda _event: pick(None))
        win.update_idletasks()
        if not win.winfo_exists():
            # Closed while it was being laid out (macOS Tk can handle events
            # here): that is Not now, and there is nothing left to place.
            return answer["choice"]
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + 60
        win.geometry(f"+{x}+{y}")
        # Over everything: the drive was just plugged in, the person is
        # looking at the computer, and may well not be looking at this window.
        win.lift()
        win.attributes("-topmost", True)
        win.focus_force()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        return answer["choice"]

    # -- the rest ---------------------------------------------------------------------------

    # -- updating ----------------------------------------------------------------------

    def say_if_stale(self, version) -> None:
        """A server still running the version from before an update (the new
        program was put in while it ran: a Mac app replaced, a zip extracted)
        keeps running the old program until it is restarted. Said once."""
        stale = version if isinstance(version, str) and version and version != __version__ \
            else None
        if stale == self.stale_version:
            return
        self.stale_version = stale
        if stale:
            self.notice.set(f"Ninaivu Lite {stale} is still running from before the update. "
                            f"Press Restart to run version {__version__}.")

    def stop_asking(self, action, doing: str) -> None:
        """Stop or Restart, asking first when it would cut a copy short (it
        carries on at the next Start, but nobody should be surprised)."""
        from .control import BUSY_WORDS
        busy = self.controller.busy()
        if busy and not self.ask(f"Ninaivu Lite is in the middle of {BUSY_WORDS.get(busy, busy)}. "
                                 "Stopping now cuts it short; it carries on, without copying "
                                 "anything twice, the next time it starts.\n\nStop anyway?"):
            return
        self.run(action, doing)

    def ask(self, question: str) -> bool:
        """A yes/no box over the window; a test replaces it."""
        from tkinter import messagebox
        return bool(messagebox.askyesno(TITLE, question, parent=self.root))

    def toggle_autostart(self) -> None:
        on = self.at_sign_in.get()
        try:
            self.controller.set_autostart(on)
            self.notice.set("Ninaivu Lite will start when you sign in." if on
                            else "Ninaivu Lite will no longer start when you sign in.")
        except OSError as exc:
            self.at_sign_in.set(not on)
            self.notice.set(f"Could not change that: {exc}")

    def reveal(self, path: Path) -> None:
        if not path.exists():
            self.notice.set(f"There is nothing at {path} yet.")
            return
        try:
            self.controller.reveal(path)
        except OSError as exc:
            self.notice.set(f"Could not open {path}: {exc}")

    def close(self) -> None:
        self.finished.set()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ninaivu_lite.panel", description=TITLE)
    parser.add_argument("--data", help="the data folder Ninaivu Lite uses")
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
    except ImportError:
        tell("This Python has no Tk, which the Control Panel is drawn with. Install Python "
             "from python.org (it includes Tk), or use start.cmd / start.sh instead.")
        return 1
    if sys.platform == "win32":
        try:  # sharp text on high-density screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        tell(f"The Control Panel could not open a window: {exc}")
        return 1
    Panel(root, Controller(args.data))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
