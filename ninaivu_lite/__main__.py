"""``python -m ninaivu_lite [photo folders…]`` — start the server."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_NAME, COPYRIGHT, __version__, backups, create_app, net
from .config import DEFAULT_PORT, Config, make_private
from .scanner import Scanner


def use_utf8_output() -> None:
    """Make the banner safe to print (from Ninaivu).

    On Windows, output redirected to a file or a service log falls back to the
    locale's code page, and the first Tamil letter or dash would raise
    UnicodeEncodeError after the port is bound. Losing a character beats
    losing the server.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


def setup_logging(data_dir: str) -> None:
    """Warnings to the console; everything to logs/ninaivu-lite.log (1 MB, three kept) for
    when something needs looking into after the window is closed."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if sys.stderr is not None:          # pythonw, at sign-in, has no console
        # The window shows the banner and real problems; the rest goes to the file.
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("  %(levelname)s: %(message)s"))
        root.addHandler(console)
    try:
        folder = Path(data_dir) / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "ninaivu-lite.log", maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError as exc:
        root.warning("could not open the log file: %s", exc)
    # One line per request is noise in a family's log.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def add_folders(cfg: Config, paths: list[str]) -> list[str] | None:
    """Add folders named on the command line to the saved ones (the admin page
    can do the same). None, after saying why, if any of them cannot be used."""
    from . import folders
    added = []
    for raw in paths:
        path = str(Path(raw).resolve())
        if path in cfg.folders:
            continue
        problem = folders.problem(path, cfg.data_dir, cfg.folders + added)
        if problem:
            print(f"  {raw}: {problem}", file=sys.stderr)
            return None
        added.append(path)
    if added:
        cfg.folders = cfg.folders + added
        cfg.save()
    return added


def reset_password(cfg: Config, username: str) -> int:
    """For whoever is at this computer and has forgotten a password. Signs that
    person out everywhere and turns the account back on."""
    import getpass

    from . import auth, db
    conn = db.connect(cfg.data_dir)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username.lower(),)).fetchone()
    if row is None:
        names = ", ".join(r[0] for r in conn.execute("SELECT username FROM users ORDER BY username"))
        print(f"  No one is called {username!r}. People: {names or '(none yet)'}", file=sys.stderr)
        return 2
    first = getpass.getpass("  New password (at least 8 characters): ")
    if first != getpass.getpass("  The same again: "):
        print("  The two passwords do not match.", file=sys.stderr)
        return 2
    try:
        auth.set_password(conn, row["id"], first)
        auth.update_profile(conn, row["id"], active=1)
    except auth.AccountError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2
    print(f"  Password changed for {username}.")
    return 0


def state_port(data_dir: str | Path, default: int) -> int:
    """The port the server on this data folder said it listens on (its
    server.json), or *default*."""
    import json
    try:
        state = json.loads((Path(data_dir) / "server.json").read_text(encoding="utf-8"))
        return int(state.get("port", default))
    except (OSError, ValueError, TypeError, AttributeError):
        return default


def restore_backup(data_dir: str | None, bundle: str) -> int:
    """Put a backup zip back, but never under a running server."""
    from .config import default_data_dir
    folder = Path(data_dir) if data_dir else default_data_dir()
    if net.already_running(state_port(folder, DEFAULT_PORT)):
        print(f"  {APP_NAME} is running. Stop it (Control Panel, Stop) and run this again.",
              file=sys.stderr)
        return 2
    try:
        aside = backups.restore(folder, bundle)
    except backups.RestoreError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2
    print(f"  Restored from {bundle}.")
    print(f"  What was there before is kept in {aside}.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ninaivu_lite", description=f"{APP_NAME} {__version__}")
    p.add_argument("folders", nargs="*", help="photo folders to show (remembered)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"port to listen on (default {DEFAULT_PORT}; the next free one if taken)")
    p.add_argument("--host", default="0.0.0.0",
                   help="address to listen on (default: every network on this computer)")
    p.add_argument("--data", help="folder for settings, index and thumbnails")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    p.add_argument("--export", metavar="FILE",
                   help="write people, albums, favourites and links to FILE for Ninaivu, then stop")
    p.add_argument("--reset-password", metavar="USERNAME",
                   help="set a new password for USERNAME (for a forgotten admin password), then stop")
    p.add_argument("--restore", metavar="ZIP",
                   help="put back a backup zip (Ninaivu Lite must be stopped), then stop")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def serve(app, host: str, port: int) -> None:
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        logging.getLogger(__name__).warning(
            "waitress is not installed; using Flask's built-in server")
        app.run(host=host, port=port, threaded=True, use_reloader=False)
        return
    from .app import MAX_REQUEST_BYTES
    # Eight threads for a household; a body larger than any route takes is
    # refused by waitress before Flask sees it; a connection that sends
    # nothing for a minute is dropped rather than holding a thread.
    waitress_serve(app, host=host, port=port, threads=8, ident=APP_NAME,
                   max_request_body_size=MAX_REQUEST_BYTES, channel_timeout=60)


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    args = parse_args(argv)
    if args.restore:
        # Before anything opens the index: a restore must not be mixed with it.
        return restore_backup(args.data, args.restore)
    cfg = Config.load(args.data)
    make_private(cfg.data_dir)
    setup_logging(cfg.data_dir)
    if cfg.folders_unknown:
        print(f"  The settings in {cfg.data_dir} are missing or damaged, and the index there\n"
              "  cannot be read to recover the library folders. Nothing was changed.\n"
              "  Restore a backup (--restore), or look at the log in the logs folder.",
              file=sys.stderr)
        return 2
    from . import db
    try:
        db.connect(cfg.data_dir).close()
    except db.NewerIndex as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2
    if args.reset_password:
        return reset_password(cfg, args.reset_password)
    if args.export:
        from . import export
        Path(args.export).write_bytes(export.dumps(db.connect(cfg.data_dir), cfg))
        print(f"  Written: {args.export}")
        return 0
    running = next((p for p in dict.fromkeys((args.port, state_port(cfg.data_dir, args.port)))
                    if net.already_running(p)), None)
    if running:
        # Started at sign-in, or a second double-click: open the one that is
        # running instead of starting another on a different port.
        local = f"http://localhost:{running}"
        print(f"\n  {APP_NAME} is already running: {local}\n")
        new = [f for f in args.folders if str(Path(f).resolve()) not in cfg.folders]
        if new:
            # Saved here, they would be written over by the running server's
            # next save of its own settings: it is the one to add them.
            print("  The folders named were not added. Add them on the Admin page, under\n"
                  "  Library folders, or stop it and run this again.\n", file=sys.stderr)
        if not args.no_browser:
            webbrowser.open(local)
        return 2 if new else 0
    if args.folders:
        added = add_folders(cfg, args.folders)
        if added is None:
            return 2
    cfg.host = args.host
    cfg.port = net.pick_port(args.host, args.port)

    addresses = net.lan_addresses() if args.host in ("0.0.0.0", "") else []
    scanner = Scanner(cfg.data_dir, cfg.folders)
    app = create_app(cfg, addresses=addresses, scanner=scanner)
    scanner.start()
    keeper = backups.Keeper(cfg.data_dir)
    keeper.start()

    local = f"http://localhost:{cfg.port}"
    print(f"\n  {APP_NAME} {__version__}  ·  {COPYRIGHT}")
    print(f"  On this computer:  {local}")
    for address in addresses[:3]:
        print(f"  On your phone:     http://{address}:{cfg.port}")
    if cfg.port != args.port:
        print(f"  (port {args.port} was busy, so {cfg.port} is used)")
    from . import auth
    if auth.needs_setup(db.connect(cfg.data_dir)):
        print("\n  First time: open the address above to make the administrator.")
        print(f"  From another device you will be asked for this code: {auth.setup_code()}")
    print("  Press Ctrl+C to stop.\n")

    from . import control
    app.config["STOP_TOKEN"] = control.write_state(cfg.data_dir, cfg.port)

    def finish() -> None:
        keeper.stop()
        scanner.stop()
        control.clear_state(cfg.data_dir)

    def stop_when_asked() -> None:
        # Asked by the Control Panel: let the answer go out, tidy up, and end
        # the process (the web server has no gentler way to be told).
        time.sleep(0.4)
        logging.getLogger(__name__).info("stopping, as the Control Panel asked")
        finish()
        logging.shutdown()
        os._exit(0)

    app.config["STOP"] = stop_when_asked
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(local,)).start()
    try:
        serve(app, args.host, cfg.port)
    except KeyboardInterrupt:
        pass
    finally:
        finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
