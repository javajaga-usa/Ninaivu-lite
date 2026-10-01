"""Start, stop and look after the server from outside it — what the Control
Panel (:mod:`ninaivu_lite.panel`) uses. Standard library only, so the panel
opens quickly and works even before the web app's packages load.

How the two find each other: a running server writes ``server.json`` in its
data folder (its process id, port and a random stop token) and removes it as it
stops. The panel reads that file, asks ``/api/health`` whether that is really
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

STATE_FILE = "server.json"
ROOT = Path(__file__).resolve().parent.parent
#: The Windows sign-in shortcut, shared with tools/start-with-windows.cmd.
STARTUP_NAME = "Ninaivu Lite.vbs"
LINUX_AUTOSTART = "ninaivu-lite.desktop"


# --- the server's side --------------------------------------------------------------------

def write_state(data_dir: str | Path, port: int) -> str:
    """Called by the server once it is listening. Returns the stop token."""
    token = secrets.token_urlsafe(24)
    path = Path(data_dir) / STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token,
                               "started": time.time()}), encoding="utf-8")
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
        return data if isinstance(data, dict) and data.get("app") == "Ninaivu Lite" else None

    def running(self) -> bool:
        return self.health() is not None

    def can_stop(self) -> bool:
        """Was the running server started in a way this panel can stop?"""
        return bool(self.state().get("token"))

    def library_summary(self) -> dict:
        """Folders and how many items are indexed, read without the server."""
        import sqlite3
        folders = list(self.cfg.folders)
        items = None
        db = self.data_dir / "ninaivu-lite.db"
        if db.is_file():
            try:
                conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1)
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
        kwargs: dict = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
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
        deadline = time.time() + wait
        while time.time() < deadline:
            if process.poll() is not None:
                return "Ninaivu Lite stopped as it started. The log says why."
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
    def _autostart_path() -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return base / "autostart" / LINUX_AUTOSTART

    def autostart_enabled(self) -> bool:
        return self._autostart_path().is_file()

    def set_autostart(self, on: bool) -> None:
        path = self._autostart_path()
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
            path.write_text(text, encoding="utf-8")
        else:
            path.write_text("[Desktop Entry]\nType=Application\nName=Ninaivu Lite\n"
                            f"Path={ROOT}\n"
                            f'Exec="{python}" -m ninaivu_lite --no-browser --data "{data}"\n'
                            "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
                            encoding="utf-8")

    # -- places ---------------------------------------------------------------------------

    @property
    def log_file(self) -> Path:
        return self.data_dir / "logs" / "ninaivu-lite.log"

    @staticmethod
    def reveal(path: Path) -> None:
        """Open a file or folder with this computer's own app for it."""
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — a path we chose
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def main(argv: list[str] | None = None) -> int:
    """For the installers: ``python -m ninaivu_lite.control --autostart on|off``,
    ``--stop`` (before an upgrade or an uninstall) and ``--status``."""
    import argparse
    parser = argparse.ArgumentParser(prog="ninaivu_lite.control")
    parser.add_argument("--data", help="the data folder Ninaivu Lite uses")
    parser.add_argument("--autostart", choices=["on", "off"])
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    controller = Controller(args.data)
    if args.stop:
        print(controller.stop())
    if args.autostart and controller.autostart_supported():
        try:
            controller.set_autostart(args.autostart == "on")
        except OSError as exc:
            print(f"Could not change starting at sign-in: {exc}", file=sys.stderr)
            return 1
    if args.status:
        print(f"running at {controller.url()}" if controller.running() else "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
