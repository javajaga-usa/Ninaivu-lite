"""Set up and start Ninaivu Lite. Run by start.cmd (Windows) and start.sh (macOS, Linux).

1. Checks this Python is new enough (3.10+).
2. Makes a private environment in ``.venv`` beside this folder, once.
3. Installs what Ninaivu Lite needs, and again only when requirements.txt changes.
4. The first time, opens the Control Panel once everything is installed.
5. Starts Ninaivu Lite, passing on any folders or options given
   (``--panel`` opens only the Control Panel instead).

Standard library only: it has to run before anything is installed. Messages are
in English and Tamil, because the person reading them may not have chosen yet.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
VENV = HERE / ".venv"
REQUIREMENTS = HERE / "requirements.txt"
STAMP = VENV / "installed-requirements.sha256"
MINIMUM = (3, 10)


def say(english: str, tamil: str) -> None:
    # The Windows console cannot join Tamil letters, so there it would look
    # broken: English only. The pages themselves are in Tamil as usual.
    lines = (english,) if os.name == "nt" else (english, tamil)
    for line in lines:
        try:
            print(f"  {line}", flush=True)
        except UnicodeEncodeError:          # a console that cannot show Tamil
            print(f"  {line.encode('ascii', 'replace').decode()}", flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def ensure_environment() -> Path:
    python = venv_python()
    if not python.exists():
        say("First start: preparing Ninaivu Lite (a minute or two)...",
            "முதல் தொடக்கம்: நினைவு லைட்டைத் தயார் செய்கிறது (ஓரிரு நிமிடங்கள்)...")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    if not STAMP.exists() or STAMP.read_text().strip() != requirements_hash():
        say("Installing what Ninaivu Lite needs...",
            "நினைவு லைட்டுக்குத் தேவையானவற்றை நிறுவுகிறது...")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
             "-r", str(REQUIREMENTS)], cwd=HERE, check=False)
        if result.returncode != 0:
            if STAMP.exists():
                # Installed before; an update failed (offline, perhaps). The old
                # set still works, so start with it rather than not at all.
                say("Could not update; starting with what is already installed.",
                    "புதுப்பிக்க முடியவில்லை; ஏற்கனவே நிறுவியவற்றுடன் தொடங்குகிறது.")
                return python
            say("Installation failed. Is this computer connected to the internet?",
                "நிறுவல் தோல்வியடைந்தது. இந்தக் கணினி இணையத்துடன் இணைக்கப்பட்டுள்ளதா?")
            raise SystemExit(1)
        STAMP.write_text(requirements_hash())
    return python


def data_argument(argv: list[str]) -> list[str]:
    """``--data DIR`` from the arguments, to hand on to the Control Panel."""
    for i, arg in enumerate(argv):
        if arg == "--data" and i + 1 < len(argv):
            return ["--data", argv[i + 1]]
        if arg.startswith("--data="):
            return ["--data", arg.split("=", 1)[1]]
    return []


def has_tk(python: Path) -> bool:
    """Whether this Python can draw a window: Debian, Ubuntu and Raspberry Pi
    OS leave Tk out (python3-tk) unless asked."""
    try:
        return subprocess.call([str(python), "-c", "import tkinter"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30) == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_panel(python: Path, argv: list[str]) -> bool:
    """Open the Control Panel beside this window, without a console of its own.
    False, after saying how to get it, when this Python has no Tk."""
    if not has_tk(python):
        say("The Control Panel needs Tk, which this Python does not have. On Debian, Ubuntu "
            "or Raspberry Pi OS: sudo apt install python3-tk",
            "கட்டுப்பாட்டுப் பலகத்துக்கு Tk தேவை; இந்த Python இல் அது இல்லை. Debian, Ubuntu, "
            "Raspberry Pi OS இல்: sudo apt install python3-tk")
        return False
    if os.name == "nt" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    kwargs: dict = {"cwd": HERE, "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([str(python), "-m", "ninaivu_lite.panel", *data_argument(argv)], **kwargs)
    except OSError:
        return False
    return True


def main(argv: list[str]) -> int:
    if sys.version_info < MINIMUM:
        say(f"Ninaivu Lite needs Python 3.10 or newer; this is {sys.version.split()[0]}.",
            f"நினைவு லைட்டுக்கு Python 3.10 அல்லது புதியது தேவை; இது {sys.version.split()[0]}.")
        say("Get it from https://www.python.org/downloads/",
            "https://www.python.org/downloads/ இலிருந்து பெறவும்")
        return 1
    first_time = not venv_python().exists() or not STAMP.exists()
    python = ensure_environment()
    if "--panel" in argv:
        # Only the Control Panel: it starts and stops Ninaivu Lite itself.
        open_panel(python, argv)
        return 0
    if first_time and open_panel(python, argv):
        say("Ready. The Control Panel is open: Ninaivu Lite can be started and stopped there.",
            "தயார். கட்டுப்பாட்டுப் பலகம் திறந்துள்ளது: அங்கே நினைவு லைட்டைத் தொடங்கலாம், நிறுத்தலாம்.")
    command = [str(python), "-m", "ninaivu_lite", *argv]
    try:
        return subprocess.call(command, cwd=HERE)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
