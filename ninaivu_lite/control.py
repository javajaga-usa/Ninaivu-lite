"""Start, stop and look after the server from outside it — what the Control
Panel (:mod:`ninaivu_lite.panel`) uses. Standard library only, so the panel
opens quickly and works even before the web app's packages load.

How the two find each other: a running server writes ``server.json`` in its
data folder (its process id, port and a random stop token, and the first-run
setup code while there is no administrator yet) and removes it as it stops.
The panel reads that file, asks ``/api/health`` whether that is really
Ninaivu Lite answering, and stops it by sending the token back to
``/api/local/stop``, which answers only requests from this computer. It never
force-kills: a server that does not stop when asked is left alone and said so.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import DEFAULT_PORT, Config
from .lock import known_instance

STATE_FILE = "server.json"
ROOT = Path(__file__).resolve().parent.parent
#: The Windows sign-in shortcut of the installed copy (the uninstaller removes
#: it by this name). Before 1.6.0 every kind of copy used it.
STARTUP_NAME = "Ninaivu Lite.vbs"
#: Each kind of copy its own, so turning one off never turns off another
#: (tools/start-with-windows.cmd writes the checkout's).
STARTUP_NAMES = {"installed": STARTUP_NAME,
                 "portable": "Ninaivu Lite (portable).vbs",
                 "checkout": "Ninaivu Lite (source).vbs"}
LINUX_AUTOSTART = "ninaivu-lite.desktop"
#: What a Stop would cut short, as the panel says it.
BUSY_WORDS = {"import": "an import into the archive", "export": "copying to a drive",
              "phone": "an import from a phone"}


def install_kind(root: Path | None = None) -> str:
    """What kind of copy this is: "installed" (the Windows installer's folder),
    "portable" (the same folder from the zip, which has no uninstaller) or
    "checkout"."""
    root = root or ROOT
    if root.name.lower() == "pkgs":        # pynsist's layout: <folder>/pkgs/ninaivu_lite
        return "installed" if (root.parent / "uninstall.exe").is_file() else "portable"
    return "checkout"


#: The portable zip's one program, at the top of its folder beside the log;
#: everything else is in app\ (the program) and data\ (the family's).
PORTABLE_EXE = "Ninaivu Lite.exe"
PORTABLE_LOG = "Ninaivu Lite.log"


def portable_home(root: Path | None = None) -> Path | None:
    """The folder a portable copy was extracted to (<home>/app/pkgs/ninaivu_lite,
    with Ninaivu Lite.exe in <home>), or None for any other copy."""
    root = root or ROOT
    if install_kind(root) != "portable" or root.parent.name.lower() != "app":
        return None
    home = root.parent.parent
    return home if (home / PORTABLE_EXE).is_file() else None


def log_path(data_dir: str | Path, root: Path | None = None) -> Path:
    """Where the server writes its log: beside Ninaivu Lite.exe for a portable
    copy on its own data folder, so it is one of the two things at its top;
    in the data folder's logs folder otherwise."""
    data = Path(data_dir)
    home = portable_home(root)
    if home is not None:
        same = os.path.normcase(os.path.abspath(home / "data")) == os.path.normcase(os.path.abspath(data))
        if same:
            return home / PORTABLE_LOG
    return data / "logs" / "ninaivu-lite.log"


# --- the server's side --------------------------------------------------------------------

def write_state(data_dir: str | Path, port: int, setup_code: str | None = None) -> str:
    """Called by the server once it is listening. Returns the stop token.
    ``setup_code``, while there is no administrator, is for the Control Panel
    to show: the file is this account's alone, as the code is this computer's."""
    token = secrets.token_urlsafe(24)
    path = Path(data_dir) / STATE_FILE
    tmp = path.with_suffix(".tmp")
    state = {"pid": os.getpid(), "port": port, "token": token, "started": time.time()}
    if setup_code:
        state["setup_code"] = setup_code
    tmp.write_text(json.dumps(state), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return token


def clear_state(data_dir: str | Path) -> None:
    """Called as the server stops; only removes the file if it is this process's."""
    path = Path(data_dir) / STATE_FILE
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


# --- the panel's side ---------------------------------------------------------------------

def python_for_background() -> str:
    """The interpreter to start a server with: on Windows the windowless
    pythonw.exe beside this one, so no console appears."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "python.exe":
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            return str(windowless)
    return str(exe)


class Controller:
    """What the panel can ask about and do. Every method is safe to call from
    a background thread; none of them touch Tk."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.cfg = Config.load(data_dir)
        self.data_dir = Path(self.cfg.data_dir)

    # -- what is going on -----------------------------------------------------------------

    def state(self) -> dict:
        try:
            data = json.loads((self.data_dir / STATE_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def port(self) -> int:
        port = self.state().get("port")
        return port if isinstance(port, int) else DEFAULT_PORT

    def url(self, admin: bool = False) -> str:
        return f"http://localhost:{self.port}" + ("/admin" if admin else "/")

    def health(self, timeout: float = 1.5) -> dict | None:
        """The server's answer to /api/health, or None if Ninaivu Lite is not
        what is answering (or nothing is)."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health",
                                        timeout=timeout) as response:
                data = json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        if not isinstance(data, dict) or data.get("app") != "Ninaivu Lite":
            return None
        # Another copy's server (an installed one beside this portable one)
        # on the same port is not this library's.
        mine, theirs = known_instance(self.data_dir), data.get("instance")
        if mine and theirs and mine != theirs:
            return None
        return data

    def running(self) -> bool:
        return self.health() is not None

    def can_stop(self) -> bool:
        """Was the running server started in a way this panel can stop?"""
        return bool(self.state().get("token"))

    def needs_setup(self) -> bool:
        """No administrator has been made yet (read without the server)."""
        import sqlite3
        try:
            conn = read_only(self.data_dir / "ninaivu-lite.db")
            try:
                return conn.execute("SELECT 1 FROM users WHERE role = 'admin' AND active = 1 "
                                    "LIMIT 1").fetchone() is None
            finally:
                conn.close()
        except sqlite3.Error:
            return True

    def setup_code(self) -> str | None:
        """The code asked for when the first administrator is made from another
        device, while none has been made yet (read without the server)."""
        code = self.state().get("setup_code")
        if not isinstance(code, str) or not code:
            return None
        import sqlite3
        db = self.data_dir / "ninaivu-lite.db"
        try:
            conn = read_only(db)
            try:
                made = conn.execute("SELECT 1 FROM users WHERE role = 'admin' AND active = 1 "
                                    "LIMIT 1").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            made = None
        return None if made else code

    def current_folders(self) -> list[str]:
        """The library folders as the settings say now (only read: nothing is
        recovered or written, as Config.load may)."""
        try:
            data = json.loads((self.data_dir / "settings.json").read_text(encoding="utf-8"))
            found = data.get("folders") if isinstance(data, dict) else None
            if isinstance(found, list) and all(isinstance(f, str) for f in found):
                return list(found)
        except (OSError, ValueError):
            pass
        return list(self.cfg.folders)

    def library_summary(self) -> dict:
        """Folders and how many items are indexed, read without the server."""
        import sqlite3
        # Read again each time: the first-day wizard adds the first folder
        # while this panel is open.
        folders = self.current_folders()
        items = None
        db = self.data_dir / "ninaivu-lite.db"
        if db.is_file():
            try:
                conn = read_only(db)
                try:
                    items = conn.execute(
                        "SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0]
                finally:
                    conn.close()
            except sqlite3.Error:
                items = None
        return {"folders": folders, "items": items}

    # -- doing things ---------------------------------------------------------------------

    def start(self, wait: float = 45.0) -> str:
        """Start the server in the background and wait until it answers.
        Returns a sentence saying what happened."""
        if self.running():
            return "Ninaivu Lite is already running."
        cmd = [python_for_background(), "-m", "ninaivu_lite", "--no-browser",
               "--data", str(self.data_dir)]
        # What the server says when it refuses to start goes here, for the
        # panel to show: it has no console of its own.
        said = self.data_dir / "logs" / "last-start.txt"
        try:
            said.parent.mkdir(parents=True, exist_ok=True)
            errors = open(said, "wb")       # noqa: SIM115 — handed to the child
        except OSError:
            errors = subprocess.DEVNULL
        kwargs: dict = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL, "stderr": errors}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.DETACHED_PROCESS
                                       | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(cmd, **kwargs)  # noqa: S603 — our own interpreter
        except OSError as exc:
            return f"Could not start Ninaivu Lite: {exc}"
        finally:
            if errors is not subprocess.DEVNULL:
                errors.close()
        deadline = time.time() + wait
        while time.time() < deadline:
            if process.poll() is not None:
                why = start_refusal(said)
                return (f"Ninaivu Lite did not start. {why}" if why
                        else "Ninaivu Lite stopped as it started. The log says why.")
            if self.state().get("pid") == process.pid and self.running():
                return "Ninaivu Lite is running."
            time.sleep(0.5)
        return "Ninaivu Lite is taking a long time to start. The log may say why."

    def stop(self, wait: float = 20.0) -> str:
        if not self.running():
            return "Ninaivu Lite is not running."
        token = self.state().get("token")
        if not token:
            return ("This Ninaivu Lite was started another way. "
                    "Close its window to stop it.")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/local/stop",
            data=json.dumps({"token": token}).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5):
                pass
        except (OSError, urllib.error.URLError) as exc:
            return f"Ninaivu Lite did not accept the request to stop: {exc}"
        deadline = time.time() + wait
        while time.time() < deadline:
            if not self.running():
                return "Ninaivu Lite has stopped."
            time.sleep(0.5)
        return "Ninaivu Lite is taking a long time to stop."

    def busy(self) -> str | None:
        """What the running server is in the middle of ("import", "export",
        "phone"), or None."""
        data = self.health() or {}
        return data.get("busy") if isinstance(data.get("busy"), str) else None

    def restart(self) -> str:
        message = self.stop()
        if self.running():
            return message
        return self.start()

    # -- start when I sign in -------------------------------------------------------------

    @staticmethod
    def autostart_supported() -> bool:
        return sys.platform == "win32" or sys.platform.startswith("linux")

    @staticmethod
    def _autostart_path(name: str | None = None) -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            return (base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
                    / (name or STARTUP_NAMES[install_kind()]))
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return base / "autostart" / LINUX_AUTOSTART

    @classmethod
    def _old_autostart(cls) -> Path | None:
        """This copy's shortcut from before 1.6.0, under the name every kind
        then shared; another copy's is left alone."""
        if sys.platform != "win32" or STARTUP_NAMES[install_kind()] == STARTUP_NAME:
            return None
        path = cls._autostart_path(STARTUP_NAME)
        text = read_shortcut(path)
        return path if text is not None and f'"{ROOT}"'.lower() in text.lower() else None

    def _starts_this_copy(self, path: Path) -> bool:
        """The shortcut starts this copy on this data folder (a portable copy
        unpacked into a new folder finds the old one's under the same name)."""
        text = read_shortcut(path)
        if text is None:
            return False
        text = text.lower()
        return str(ROOT).lower() in text and str(self.data_dir).lower() in text

    @staticmethod
    def _user_service() -> Path | None:
        """The systemd user service install.sh sets up on Linux, if there is one."""
        if not sys.platform.startswith("linux"):
            return None
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        unit = base / "systemd" / "user" / "ninaivu-lite.service"
        return unit if unit.is_file() else None

    def autostart_enabled(self) -> bool:
        service = self._user_service()
        if service is not None:
            return (service.parent / "default.target.wants" / service.name).exists()
        return self._starts_this_copy(self._autostart_path()) or self._old_autostart() is not None

    def autostart_elsewhere(self) -> bool:
        """A start-at-sign-in shortcut of this kind exists but starts another
        copy (the folder this one was upgraded from): ticking the box moves it
        here."""
        if self._user_service() is not None:
            return False
        path = self._autostart_path()
        return path.is_file() and not self._starts_this_copy(path)

    def set_autostart(self, on: bool) -> None:
        service = self._user_service()
        if service is not None:
            # Installed with a service: that is what starts it, so that is
            # what the box turns on and off (a second starter would race it).
            subprocess.run(["systemctl", "--user", "enable" if on else "disable",  # noqa: S603, S607
                            "ninaivu-lite"], check=False, capture_output=True, timeout=30)
            return
        path = self._autostart_path()
        old = self._old_autostart()
        if old is not None:
            old.unlink(missing_ok=True)
        if not on:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        python = python_for_background()
        data = str(self.data_dir)
        if sys.platform == "win32":
            text = ('Set shell = CreateObject("WScript.Shell")\r\n'
                    f'shell.CurrentDirectory = "{ROOT}"\r\n'
                    f'shell.Run """{python}"" -m ninaivu_lite --no-browser --data ""{data}""", 0, False\r\n')
            # UTF-16 with its byte-order mark: Windows Script Host reads any
            # other .vbs in the ANSI code page, and a Tamil (or "José") user
            # name in the data folder's path came out garbled.
            path.write_text(text, encoding="utf-16")
        else:
            path.write_text("[Desktop Entry]\nType=Application\nName=Ninaivu Lite\n"
                            f"Path={ROOT}\n"
                            f'Exec="{python}" -m ninaivu_lite --no-browser --data "{data}"\n'
                            "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
                            encoding="utf-8")

    # -- places ---------------------------------------------------------------------------

    @property
    def log_file(self) -> Path:
        return log_path(self.data_dir)

    @staticmethod
    def reveal(path: Path) -> None:
        """Open a file or folder with this computer's own app for it."""
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — a path we chose
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def read_only(path: Path):
    """The index, opened only to read. A plain path, not a file: URI, which
    breaks on '#', '%' or '?' in a folder name and cannot name a network share."""
    import sqlite3
    if not path.is_file():           # never made here: that is the server's to do
        raise sqlite3.OperationalError(f"no index at {path}")
    conn = sqlite3.connect(str(path), timeout=1)
    conn.execute("PRAGMA query_only = 1")
    return conn


def read_shortcut(path: Path) -> str | None:
    """A start-at-sign-in file's text, whichever encoding it was written in."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def start_refusal(path: Path) -> str:
    """What a server that would not start printed, as one short paragraph."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.startswith(("Traceback", "  File "))]
    return " ".join(lines)[-600:]


def main(argv: list[str] | None = None) -> int:
    """For the installers: ``python -m ninaivu_lite.control --autostart on|off``,
    ``--stop`` (before an upgrade or an uninstall), ``--start`` (after an
    upgrade, when it was running before), ``--status``, ``--running`` (exit
    status 0 when running, 1 when not) and ``--needs-setup`` (exit status 0
    while no administrator has been made)."""
    import argparse
    parser = argparse.ArgumentParser(prog="ninaivu_lite.control")
    parser.add_argument("--data", help="the data folder Ninaivu Lite uses")
    parser.add_argument("--autostart", choices=["on", "off"])
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--running", action="store_true")
    parser.add_argument("--needs-setup", action="store_true")
    args = parser.parse_args(argv)
    controller = Controller(args.data)
    if args.running:
        return 0 if controller.running() else 1
    if args.needs_setup:
        return 0 if controller.needs_setup() else 1
    if args.stop:
        doing = controller.busy()
        print(controller.stop())
        if doing:
            print(f"It was in the middle of {BUSY_WORDS.get(doing, doing)}; "
                  "Start carries on where it stopped.")
    if args.autostart and controller.autostart_supported():
        try:
            controller.set_autostart(args.autostart == "on")
        except OSError as exc:
            print(f"Could not change starting at sign-in: {exc}", file=sys.stderr)
            return 1
    if args.start:
        print(controller.start())
    if args.status:
        print(f"running at {controller.url()}" if controller.running() else "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

