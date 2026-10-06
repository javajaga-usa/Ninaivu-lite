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
from urllib.parse import urlencode

from . import drives, updates
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
CARD_ACCENT = {"addresses": "#10b981", "library": "#3b82f6", "options": "#64748b",
               "updates": "#f59e0b"}
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
        #: set by Download: once the stop it asked for is done, the panel closes
        self.close_when_done = False
        #: pendrives and external drives plugged in while the panel is open
        self.drive_watcher = drives.Watcher()

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
                                      command=lambda: self.run(controller.stop, "Stopping…"))
        self.restart_button = ttk.Button(actions, text="↻  Restart" if SYMBOL else "Restart", style="Accent.TButton",
                                         command=lambda: self.run(controller.restart, "Restarting…"))
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
        ttk.Button(row, text="Open the log",
                   command=lambda: self.reveal(controller.log_file)).pack(side="right")
        ttk.Button(row, text="Open the data folder",
                   command=lambda: self.reveal(controller.data_dir)).pack(side="right", padx=(0, 6))

        # -- a newer version? ---------------------------------------------------------
        # One small question to GitHub a day, from a thread; the answer is a
        # line here and a button to the download page, never anything more.
        body = self._card(outer, "updates", "UPDATES")
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x")
        self.update_text = tk.StringVar(value="")
        self.update_url = updates.RELEASES_PAGE
        tk.Label(row, textvariable=self.update_text, font=(FONT, 10), bg=SURFACE, fg=INK,
                 anchor="w", justify="left", wraplength=400).pack(side="left", fill="x", expand=True)
        self.download_button = ttk.Button(row, text="Download", style="Accent.TButton",
                                          command=self.download)
        ttk.Button(row, text="Check now",
                   command=lambda: self.check_updates(force=True)).pack(side="right")
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x", pady=(6, 0))
        self.updates_on = tk.BooleanVar(value=updates.enabled(controller.data_dir))
        ttk.Checkbutton(row, text="Tell me when a new version is available (asks GitHub once a day)",
                        variable=self.updates_on,
                        command=self.toggle_updates).pack(side="left")
        # Nothing is asked of GitHub until the box is ticked or Check now is
        # pressed; said so, in place of an answer, while it is off.
        if not self.updates_on.get():
            self.update_text.set("Not checking. Press Check now, or tick the box below.")

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
        self.check_updates()
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
                running = self.controller.running()
                port = self.controller.port
                phones = net.lan_addresses()[:2] if running else []
                summary = self.controller.library_summary()
                self.events.put(("reading", running, port, phones, summary,
                                 self.controller.can_stop()))
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
                    if self.close_when_done:
                        self.close()
                        return
                elif event[0] == "notice":
                    self.notice.set(event[1])
                elif event[0] == "update":
                    self.show_update(event[1], event[2])
                elif event[0] == "drive":
                    self.offer_drive(event[1])
        except queue.Empty:
            pass
        if not self.finished.is_set():
            self.root.after(150, self.pump)

    def show_reading(self, running, port, phones, summary, can_stop) -> None:
        self.is_running = running
        if not self.busy:
            self.status.set("Running" if running else "Stopped")
            self._paint_status("running" if running else "stopped")
        if running:
            rows = [("On this computer", f"http://localhost:{port}/"),
                    ("Admin console", f"http://localhost:{port}/admin")]
            rows += [("On your phone", f"http://{a}:{port}/") for a in phones]
            if not can_stop:
                rows.append(("", "Started from its own window: stop it there."))
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
        from tkinter import ttk
        win = tk.Toplevel(self.root)
        win.title(TITLE)
        win.configure(bg=SURFACE)
        win.transient(self.root)
        win.resizable(False, False)
        answer: dict[str, str | None] = {"choice": None}

        def pick(choice: str | None) -> None:
            answer["choice"] = choice
            win.destroy()

        body = tk.Frame(win, bg=SURFACE, padx=20, pady=16)
        body.pack(fill="both", expand=True)
        phone = drive.kind == "phone"
        name = drive.label if drive.label == drive.path or drive.shell \
            else f"{drive.label} ({drive.path})"
        heading = ("ஒரு தொலைபேசி இணைக்கப்பட்டது  ·  A phone was connected" if phone
                   else "ஒரு டிரைவ் இணைக்கப்பட்டது  ·  A drive was connected")
        tk.Label(body, text=heading,
                 font=(FONT, 13, "bold"), bg=SURFACE, fg=INK).pack(anchor="w")
        tk.Label(body, text=name, font=(FONT, 11), bg=SURFACE, fg=MUTED).pack(anchor="w",
                                                                             pady=(4, 10))
        tk.Label(body, text="இதை வைத்து என்ன செய்ய விரும்புகிறீர்கள்?\n"
                            "What would you like to do with it?",
                 font=(FONT, 10), bg=SURFACE, fg=INK, justify="left").pack(anchor="w")
        buttons = tk.Frame(body, bg=SURFACE)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="இறக்குமதி  ·  Import media from this "
                                 + ("phone" if phone else "drive"),
                   style="Accent.TButton",
                   command=lambda: pick("import")).pack(fill="x", pady=(0, 6))
        if not phone:   # a phone's storage is no place for the whole library
            ttk.Button(buttons, text="ஏற்றுமதி  ·  Export media to this drive",
                       command=lambda: pick("export")).pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="இப்போது வேண்டாம்  ·  Not now",
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

    # -- updates ----------------------------------------------------------------------

    def check_updates(self, force: bool = False) -> None:
        """Ask on a thread; the answer comes back through the queue."""
        if not force and not self.updates_on.get():
            return

        def work():
            info = updates.check(self.controller.data_dir, force=force)
            self.events.put(("update", info, force))

        threading.Thread(target=work, name="panel-updates", daemon=True).start()

    def show_update(self, info, asked: bool) -> None:
        if info is None:
            self.update_text.set("Could not reach GitHub to check." if asked else "")
            self.download_button.pack_forget()
            return
        # Only ever a page on GitHub: the answer is read back from a file in
        # the data folder, and what it names is opened (on Windows, run).
        url = info.get("url")
        self.update_url = url if isinstance(url, str) and url.startswith("https://github.com/") \
            else updates.RELEASES_PAGE
        if info["available"]:
            self.update_text.set(f"Version {info['version']} is available (you have {__version__}). "
                                 "Before installing it, stop Ninaivu Lite and close this panel.")
            self.download_button.pack(side="right", padx=(0, 6))
        else:
            self.update_text.set(f"You have the latest version, {__version__}." if asked else "")
            self.download_button.pack_forget()

    def download(self) -> None:
        """The release page, then the one thing the installer needs: nothing of
        the old program in use. Windows cannot replace a file in use, and a
        running Ninaivu Lite or this panel keeps the program in use, so the
        panel offers to stop the one and close the other, before the installer
        has to ask."""
        webbrowser.open(self.update_url)
        if self.is_running:
            question = ("Before running the installer, Ninaivu Lite must be stopped and this "
                        "panel closed: files in use cannot be replaced.\n\n"
                        "Stop Ninaivu Lite and close this panel now?")
        else:
            question = ("Before running the installer, this panel must be closed: files in "
                        "use cannot be replaced.\n\nClose this panel now?")
        if not self.ask(question):
            self.notice.set(("Before running the installer: press Stop, then close this "
                             "Control Panel.") if self.is_running
                            else "Before running the installer, close this Control Panel.")
            return
        if self.is_running and self.controller.can_stop():
            self.close_when_done = True
            self.run(self.controller.stop, "Stopping…")
        elif self.is_running:
            self.notice.set("Ninaivu Lite was started from its own window: close that window, "
                            "then close this panel, before running the installer.")
        else:
            self.close()

    def ask(self, question: str) -> bool:
        """A yes/no box over the window; a test replaces it."""
        from tkinter import messagebox
        return bool(messagebox.askyesno(TITLE, question, parent=self.root))

    def toggle_updates(self) -> None:
        on = self.updates_on.get()
        updates.set_enabled(self.controller.data_dir, on)
        if on:
            self.check_updates(force=True)
        else:
            self.update_text.set("Not checking. Press Check now, or tick the box below.")
            self.download_button.pack_forget()

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
