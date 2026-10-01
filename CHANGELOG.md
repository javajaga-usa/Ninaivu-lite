# Changelog

## 1.2.0 — 2026-10-01

- **Installers** (`installers/`), each with its own Python and every package, so the computer
  needs neither Python nor the internet to install:
  - **Windows** — `Ninaivu-Lite-<version>-windows-x64.exe`: Start-menu and Desktop shortcuts
    for the Control Panel, a *Start at sign-in* box, and the Control Panel opened when the
    installer finishes. Carries Noto Sans Tamil.
  - **Linux and Raspberry Pi** — one `.sh` file per architecture (amd64, arm64): commands,
    a desktop entry, a systemd service, upgrade in place, an uninstaller.
  - **macOS** — `Ninaivu Lite.app` in a disk image, for Apple silicon and Intel.
  - **Docker** — an image and a compose file; the photos are mounted read-only.
  - A release workflow builds them all on a version tag and attaches them to the release.
- `python -m ninaivu_lite.control --stop | --autostart on|off | --status`, used by the
  installers to stop a running Ninaivu Lite before an upgrade or an uninstall.

## 1.1.0 — 2026-10-01

- **Control Panel** — Ninaivu's Control Panel, lighter: one small window showing whether
  Ninaivu Lite is running and where, with Start, Stop and Restart, the family app and console
  a click away, the addresses for phones, the library at a glance, *Start when I sign in*,
  the log and the data folder. It opens by itself when the first-time setup finishes, and any
  time from **Start - Ninaivu Lite Control Panel.vbs** (Windows) or `./start.sh --panel`.
  It needs nothing extra (Tk comes with Python) and never force-kills the server.
- **Copyright, as in Ninaivu** — © 2026 Jagadeesh Rajendran, MIT licence: in LICENSE, an
  *About Ninaivu* box in the gallery, the console's Settings page, the start-up banner and the
  Control Panel.
- **A calmer start window** — only the banner and real problems are printed; everything else
  goes to the log file. On Windows the window's own messages are in English only, because the
  Windows console cannot join Tamil letters (the screens are in Tamil as before).
- **First-day guide: people are no longer lost.** A person typed in but not yet added is
  saved when you press *Open the console*, and a refusal (a PIN too easy to guess, such as
  1234, or a name already used) is now said right under the form instead of in a note at the
  foot of the screen — so nobody looks added when they were not.
- The package no longer loads Flask and Pillow until the server starts, so the Control Panel
  opens quickly on old computers.

## 1.0.0 — 2026-10-01

The first release: Ninaivu's screens over a small, steady engine.

- **Gallery** (`/`) — Ninaivu's family app: timeline with date scrubber, folders, favourites,
  albums, search by name/folder/date/camera, full-screen viewer, video playback, download
  (one file or a zip), Tamil and English.
- **Sign-in like Ninaivu** — profile picker; no secret, PIN or password per person;
  *Just looking* for public photos (can be turned off); admins sign in on the console.
- **Admin console** (`/admin`) — first-day guide, overview with a "what each role sees"
  preview, library folders and rescans, people (role, entry, assigned folder, sign out
  everywhere), folder visibility rules with undo, settings, backup download.
- **Roles and visibility** — Admin / Family / Guest; Public / Family / Hidden, by folder rule
  or per photo; checked on the server for every item, page and link.
- **Share links** — one photo or an album, optional password and expiry; visitors and guests
  get copies without location or camera data.
- **Engine** — Flask + waitress, SQLite (WAL with fallback), incremental scanner (size + time),
  WebP previews in two sizes, dates from EXIF, video headers or file names, folder watching,
  daily index backups (last seven kept).
- **Launch** — `start.cmd` / `start.sh` make their own environment; start with Windows or
  systemd; one instance per data folder; port 8080 or the next free one.
- **Move up** — `--export` writes a file the full Ninaivu can import ([docs/UPGRADE.md](docs/UPGRADE.md)).
- **Recovery** — `--reset-password USERNAME` for a forgotten password.

Not in Lite (stays in Ninaivu): AI of any kind, cloud and phone backup, editing and rotation,
deleting media, maps, the screen lock, HTTPS.
