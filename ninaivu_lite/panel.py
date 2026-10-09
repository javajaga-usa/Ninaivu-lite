"""Ninaivu Lite's Control Panel: Ninaivu's Control Panel, lighter.

    python -m ninaivu_lite.panel
    Start - Ninaivu Lite Control Panel.vbs     (Windows, double-click, no console)
    ./start.sh --panel                         (macOS, Linux)

One small window: whether Ninaivu Lite is running and where, Start, Stop and
Restart, the gallery and console a click away, the addresses for phones, the
library at a glance, starting with the computer, and the log. Closing it
leaves Ninaivu Lite running. It needs only Tk, which comes with Python from
python.org; the resource modes and readings of Ninaivu's panel are not here.

Background work never blocks Tk: readings and actions run on threads and come
back through a queue the window empties on its own timer, because a start can
take a while and a window that freezes meanwhile looks like one that crashed.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import webbrowser
from pathlib import Path

from . import drives
from .control import Controller
from .version import APP_NAME, COPYRIGHT, LICENCE, __version__

TITLE = f"{APP_NAME} Control Panel"
ICON = Path(__file__).resolve().parent / "static" / "icons" / "icon-192.png"

# Ninaivu's Control Panel palette.
BG = "#f1f5f9"
SURFACE = "#ffffff"
INK = "#0f172a"
MUTED = "#334155"
ACCENT = "#2563eb"
BORDER = "#e2e8f0"
SECTION_TITLE = "#1e293b"
STATUS = {  # foreground, background, border
    "running": ("#15803d", "#f0fdf4", "#86efac"),
    "stopped": ("#475569", "#f8fafc", "#cbd5e1"),
    "busy": ("#1d4ed8", "#eff6ff", "#93c5fd"),
}
CARD_ACCENT = {"addresses": "#10b981", "library": "#3b82f6", "options": "#64748b"}
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
#: ▶ ■ ↻ as in Ninaivu where the font surely has them (Segoe UI); words alone elsewhere.
SYMBOL = sys.platform == "win32"


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

        root.title(TITLE)
        root.configure(bg=BG)
        try:
            self.icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                font = tkfont.nametofont(name)
                if font.cget("size") < 10:
                    font.configure(size=10)
            except tk.TclError:
                pass

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", font=(FONT, 10, "bold"), padding=(8, 4),
                        background=SURFACE, foreground=INK)
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")])
        style.configure("Start.TButton", background="#16a34a", foreground="white", padding=(10, 4))
        style.map("Start.TButton", background=[("active", "#15803d"), ("disabled", "#86efac")])
        style.configure("Stop.TButton", background="#dc2626", foreground="white", padding=(10, 4))
        style.map("Stop.TButton", background=[("active", "#b91c1c"), ("disabled", "#fca5a5")])
        # The tick box itself: the clam theme draws it a fixed few pixels, tiny
        # beside the words on a sharp screen. Sized to the font instead: the
        # text's point size in pixels at this screen's density, so it matches
        # the height of its own capitals and no more.
        box = round(10 * float(root.tk.call("tk", "scaling")))
        style.configure("TCheckbutton", background=SURFACE, foreground=INK, font=(FONT, 10),
                        indicatorsize=max(13, box), indicatormargin=(0, 0, 8, 0),
                        padding=(0, 3))
        # Ticked: a white mark on the accent blue, as the buttons are.
        style.map("TCheckbutton", background=[("active", SURFACE)],
                  indicatorbackground=[("selected", ACCENT), ("!selected", SURFACE)],
                  indicatorforeground=[("selected", "white")])

        outer = tk.Frame(root, bg=BG, padx=16, pady=10)
        outer.pack(fill="both", expand=True)

        # -- name, badge, status ------------------------------------------------
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        tk.Label(header, text=APP_NAME, font=(FONT, 20, "bold"), bg=BG, fg=INK).pack(side="left")
        badge = tk.Frame(header, bg="#fef3c7", highlightthickness=1,
                         highlightbackground="#fcd34d", padx=6, pady=2)
        badge.pack(side="left", padx=(8, 10))
        tk.Label(badge, text="CONTROL PANEL", font=(FONT, 8, "bold"),
                 bg="#fef3c7", fg="#92400e").pack()
        self.status = tk.StringVar(value="Checking…")
        self.pill = tk.Frame(header, highlightthickness=1, padx=10, pady=3)
        self.pill.pack(side="left")
        self.pill_label = tk.Label(self.pill, textvariable=self.status, font=(FONT, 11, "bold"))
        self.pill_label.pack()
        self._paint_status("busy")

        # -- what can be done now --------------------------------------------------
        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x", pady=(10, 2))
        self.start_button = ttk.Button(actions, text="▶  Start" if SYMBOL else "Start", style="Start.TButton",
                                       command=lambda: self.run(controller.start, "Starting…"))
        self.stop_button = ttk.Button(actions, text="■  Stop" if SYMBOL else "Stop", style="Stop.TButton",
                                      command=lambda: self.stop_asking(controller.stop, "Stopping…"))
        self.restart_button = ttk.Button(actions, text="↻  Restart" if SYMBOL else "Restart", style="Accent.TButton",
                                         command=lambda: self.stop_asking(controller.restart, "Restarting…"))
        for button in (self.start_button, self.stop_button, self.restart_button):
            button.pack(side="left", padx=(0, 6))
        tk.Frame(actions, bg="#cbd5e1", width=1).pack(side="left", fill="y", padx=8, pady=2)
        self.app_button = ttk.Button(actions, text="Open the family app",
                                     command=lambda: webbrowser.open(controller.url()))
        self.console_button = ttk.Button(actions, text="Open the console",
                                         command=lambda: webbrowser.open(controller.url(True)))
        self.app_button.pack(side="left", padx=(0, 6))
        self.console_button.pack(side="left")
        # Room for a progress bar that shows only while something is happening.
        lane = tk.Frame(outer, bg=BG, height=8)
        lane.pack(fill="x", pady=(6, 4))
        self.progress = ttk.Progressbar(lane, mode="indeterminate")

        # -- cards -------------------------------------------------------------------
        self.library = tk.StringVar(value="—")
        self.address_rows: list | None = None
        self.address_box = self._card(outer, "addresses", "WHERE TO OPEN IT")
        body = self._card(outer, "library", "LIBRARY")
        tk.Label(body, textvariable=self.library, font=(FONT, 10), bg=SURFACE, fg=MUTED,
                 justify="left", anchor="w", wraplength=620).pack(fill="x")
        body = self._card(outer, "options", "THIS COMPUTER")
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x")
        if controller.autostart_supported():
            self.at_sign_in = tk.BooleanVar(value=controller.autostart_enabled())
            ttk.Checkbutton(row, text="Start Ninaivu Lite when I sign in",
                            variable=self.at_sign_in,
                            command=self.toggle_autostart).pack(side="left")
            if controller.autostart_elsewhere():
                tk.Label(body, text="Another copy of Ninaivu Lite (an older folder?) starts when "
                         "you sign in. Tick the box to start this one instead.",
                         font=(FONT, 9), bg=SURFACE, fg=MUTED, justify="left", anchor="w",
                         wraplength=620).pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Open the log",
                   command=lambda: self.reveal(controller.log_file)).pack(side="right")
        ttk.Button(row, text="Open the data folder",
                   command=lambda: self.reveal(controller.data_dir)).pack(side="right", padx=(0, 6))

        self.notice = tk.StringVar(value="Closing this panel leaves Ninaivu Lite running.")
        tk.Label(outer, textvariable=self.notice, font=(FONT, 10), bg=BG, fg=MUTED,
                 wraplength=640, justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        # -- who made it -------------------------------------------------------------
        tk.Frame(outer, bg="#cbd5e1", height=1).pack(fill="x", pady=(10, 6))
        tk.Label(outer, text=f"{APP_NAME} {__version__}  ·  {COPYRIGHT}  ·  {LICENCE} licence",
                 font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor="w")

        self._set_buttons()
        # Placed, not sized: the window keeps fitting its contents as the
        # addresses and the library fill in.
        root.minsize(680, 1)
        root.update_idletasks()
        width, height = max(root.winfo_reqwidth(), 680), root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 3)
        root.geometry(f"+{x}+{y}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.watch, name="panel-watch", daemon=True).start()
        root.after(150, self.pump)

    # -- building --------------------------------------------------------------------------

    def _card(self, parent, key: str, title: str):
        tk = self.tk
        stripe = tk.Frame(parent, bg=CARD_ACCENT[key])
        stripe.pack(fill="x", pady=(6, 0))
        frame = tk.Frame(stripe, bg=SURFACE, padx=12, pady=8, highlightthickness=1,
                         highlightbackground=BORDER)
        frame.pack(side="right", fill="both", expand=True, padx=(3, 0))
        tk.Label(frame, text=title, font=(FONT, 8, "bold"), bg=SURFACE,
                 fg=SECTION_TITLE).pack(anchor="w", pady=(0, 3))
        content = tk.Frame(frame, bg=SURFACE)     # its own frame, so a card may use grid
        content.pack(fill="x")
        return content

    def _paint_status(self, kind: str) -> None:
        fg, bg, border = STATUS[kind]
        self.pill.configure(bg=bg, highlightbackground=border)
        self.pill_label.configure(bg=bg, fg=fg)

    def _set_buttons(self) -> None:
        idle = not self.busy
        self.start_button.state(["!disabled"] if idle and not self.is_running else ["disabled"])
        for button in (self.stop_button, self.restart_button):
            button.state(["!disabled"] if idle and self.is_running else ["disabled"])
        for button in (self.app_button, self.console_button):
            button.state(["!disabled"] if self.is_running else ["disabled"])

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
        folders = summary.get("folders") or []
        if folders:
            shown = "\n".join(f"•  {f}" for f in folders[:3])
            more = f"\n   and {len(folders) - 3} more" if len(folders) > 3 else ""
            items = summary.get("items")
            count = f"{items:,} photos and videos indexed" if items is not None else "Not indexed yet"
            self.library.set(f"{count}\n{shown}{more}")
        else:
            self.library.set("No photo folder yet. Start Ninaivu Lite and open the console "
                             "to choose one.")
        self._set_buttons()

    def show_addresses(self, rows: list) -> None:
        if rows == self.address_rows:
            return
        self.address_rows = rows
        box = self.address_box
        for child in box.grid_slaves():
            child.destroy()
        tk = self.tk
        for i, (label, value) in enumerate(rows, start=1):
            if label:
                tk.Label(box, text=label, font=(FONT, 10), bg=SURFACE, fg=MUTED,
                         anchor="w").grid(row=i, column=0, sticky="w", padx=(0, 16))
                tk.Label(box, text=value, font=(FONT, 11, "bold"), bg=SURFACE, fg=INK,
                         anchor="w").grid(row=i, column=1, sticky="w")
            else:
                tk.Label(box, text=value, font=(FONT, 10), bg=SURFACE, fg=MUTED, anchor="w",
                         wraplength=620, justify="left").grid(row=i, column=0, columnspan=2,
                                                              sticky="w")

    # -- a drive plugged in ------------------------------------------------------------------

    def look_for_drives(self, folders: list[str]) -> None:
        """On the watch thread: each drive or phone newly plugged in is mentioned
        once. The drive the library or the data folder lives on is not a
        visitor, and a drive answered with Don't ask again in the console is
        not mentioned at all."""
        home = [*folders, str(self.controller.data_dir)]
        never = set(self.controller.drives_never_ask())
        for drive in self.drive_watcher.pending():
            self.drive_watcher.answer(drive.id)
            if drive.id in never:
                continue
            if drive.kind == "phone" or not any(drives.is_within(path, drive.path)
                                                for path in home if path):
                self.events.put(("drive", drive))

    def offer_drive(self, drive: drives.Drive) -> None:
        """No window of its own: a line in the panel, in Tamil and English since
        the panel has no language of its own. The console shows the drive as a
        notice at its top, where Import, Export and Don't ask again are."""
        phone = drive.kind == "phone"
        name = drive.label if drive.label == drive.path or drive.shell \
            else f"{drive.label} ({drive.path})"
        self.notice.set(
            (f"{name} — ஒரு தொலைபேசி இணைக்கப்பட்டது. " if phone
             else f"{name} — ஒரு டிரைவ் இணைக்கப்பட்டது. ")
            + "இறக்குமதி செய்ய console-ஐத் திறவுங்கள்.\n"
            + (f"A phone was connected: {name}. " if phone
               else f"A drive was connected: {name}. ")
            + "Open the console: its notice at the top imports from it"
            + ("." if phone else " or exports to it."))

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
