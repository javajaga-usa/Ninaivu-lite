# Changelog

## 1.3.7 — 2026-10-02

- **Upgrading on Windows no longer fails with a write error.** Windows cannot replace a
  program that is in use, and a running Ninaivu Lite or an open Control Panel kept the old
  one in use. The installer now asks the server to stop and then, while anything is still in
  use, asks you to press *Stop* and close the Control Panel, with *Retry*, instead of failing
  half-way. The uninstaller does the same. The panel's *Download* button says so before you
  start.
- **A replaced profile picture is a new file**, never written over the old one, which a
  browser may still be reading; that failed on Windows in 1.3.6.

## 1.3.6 — 2026-10-02

- **The administrator has a tile on the sign-in screen.** Until now the picker left
  administrators out, so the admin had to choose *Sign in with a username instead* every
  time. Their tile is there now, locked: tapping it asks for the password, and nothing less
  opens it. The console still takes the username and password.
- **A photograph as your profile picture.** Open any photograph in the gallery and choose
  *Use as my profile picture* from its ⋯ menu: the middle of it becomes a 256 px square kept
  in the data folder (`avatars/`), shown on your tile and beside your name. *Remove picture*
  in your profile brings the initials back; an administrator can remove anyone's under
  *People*, and a deleted profile takes its picture with it. The picture is never guessed
  from a name: nothing in a library says whose face is whose.

## 1.3.5 — 2026-10-02

- **The Control Panel opens again.** 1.3.4's new UPDATES card asked for a stripe colour the
  panel's palette did not have, so the window crashed while it was being built and nothing
  appeared after installing. The colour is there now, and a test opens the panel for real
  (where Tk and a display exist) so this cannot slip through again.

## 1.3.4 — 2026-10-02

- **The Control Panel says when a newer version is out.** Once a day it asks GitHub for the
  latest release (one small request, nothing about the library in it) and, when there is a
  newer one, shows the version with a **Download** button to the release page. *Check now*
  asks at once; the box beneath switches the daily question off. The answer and the switch
  live in `update-check.json` in the data folder, not in the server's settings.

## 1.3.3 — 2026-10-02

- **The archive destination is never empty.** The Import page opens with the archive set to
  a `Ninaivu Archive` folder inside the default library folder (under Pictures when there is
  no library yet), so what comes in is shown to the family as it lands. Browse changes it, and
  a folder the administrator chose is kept from then on. The first-day step uses the same rule.

## 1.3.2 — 2026-10-01

- **Video previews without ffmpeg.** None of the installers carry ffmpeg, so every video showed
  a plain tile. Now the browser that can play a video makes its picture: when a family member
  or administrator scrolls past a video with no preview, or plays one, the gallery draws one
  frame and sends it to the server, which keeps it as the tile for everyone. One at a time, a
  few dozen a visit, never for guests, never twice. A video the browser cannot decode keeps the
  plain tile. With ffmpeg installed, nothing changes.
- **A turn by hand remakes both thumbnails at once.** The 640 px one used to keep the old way
  up until the next scan, so big tiles and the viewer's stand-in looked unturned.

## 1.3.1 — 2026-10-01

- **Import refuses a source that is already in the library.** Archiving the library beside
  itself would have shown every photo twice; the console and the first-day step now say so.
- **The importer's refusals are translated.** Sentences with a path in them travel as their
  English key and the path, so a Tamil console shows them in Tamil.
- **A running import shows in the strip at the top of every console page**, with its progress
  and a way to the Import page, beside the indexer.
- **Rotate** in the viewer is offered for photographs only.
- **A guest's copy of a turned photograph comes out upright**, and is not turned a second time
  on screen.
- Sudar's footer says what it does: a download carries no metadata; a saved copy keeps the
  original's camera, exposure and place.
- The importer's pause test no longer depends on how fast the machine is.

## 1.3.0 — 2026-10-01

- **Import** (console → Library → Import), from Ninaivu's archive engine, cut to what a
  household uses: sweep old drives, memory cards, phone backups and backup folders into one
  archive filed as `YYYY/MM/DD` by the day each photo was taken (EXIF, the video's own
  header, a Google Takeout sidecar, the file name, a dated folder, then the file's clock).
  Sources are only ever read; every copy is hashed as it is read and read back from the
  archive before it counts; duplicates are found by content and left where they are; the
  same name with different bytes gets `_1`. **Start is also Resume**, a **dry run** decides
  everything and writes nothing, and **Audit** re-reads every archived file. A Takeout
  export comes across with its dates, places and descriptions, and its albums can be made
  in the library after the import. One press adds the archive to the library. Standard
  library and Pillow only: no pacing, drive-health sampling or classifiers.
- **The first day asks about old photos.** Between the library folder and the household,
  the walk-through explains what the importer does and asks which folders or drives hold
  the photos to bring in; the archive is built inside the library folder and indexed as it
  lands.
- **Sudar** (சுடர்), Ninaivu's photo studio, in the viewer (*Edit with Sudar*): light,
  colour, detail and framing through the same develop engine as Ninaivu, looks,
  suggestions measured from the picture, *make it warmer* in plain words through the
  built-in planner, clothing colour by brush, before and after, undo and redo. Everything
  runs in the browser; no model, nothing sent anywhere. The original is never changed: the
  result is downloaded, or an administrator saves it as a copy beside the original, which
  keeps the camera, exposure and place from the original and appears in the library at once.
- **Sideways photographs are turned upright during the scan**, nobody asked, no file changed:
  the camera's own orientation tag is final; a photograph without one (a scanned print, one a
  messaging app stripped) is judged by the faces in it when OpenCV is installed
  (`requirements-straighten.txt`, optional: the classifiers ship with it, nothing is
  downloaded), and turned only when one way up is clearly ahead. The turn lives in the index:
  thumbnails are remade and the viewer turns the picture. **Rotate** in the viewer lets an
  administrator correct any by hand, and that answer outlives every rescan.
- The index now reads the same date chain as the importer (Takeout sidecars and dated
  folders included), and takes a photo's place from a Takeout sidecar when the file has none.

## 1.2.1 — 2026-10-01

- **Windows: a signed uninstaller, and a published code signing policy.** The uninstaller is
  now made by the build and packed into the installer, so it can be signed along with it
  (Windows' Smart App Control refuses unsigned programs). Signing is free through SignPath
  Foundation once the project is accepted there; the policy is in `docs/CODE-SIGNING.md`.
  Every build now also installs the Windows installer, opens the Control Panel, starts and
  stops Ninaivu Lite and uninstalls it, on GitHub's Windows machine.
- The Windows installer file now carries its product name and version (Properties, Details).
- Windows system folders are refused as photo folders wherever Windows is installed, not only
  on drive C.

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
- **One Python everywhere**: `.python-version` (3.13.7) is the single place the bundled Python
  is written; the Windows, Linux and macOS installers, the Docker image and the release
  workflow all use it, and a test fails if they drift. Running from source still works on 3.10+.
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
