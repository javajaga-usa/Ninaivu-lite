# Ninaivu-lite — Plan

**நினைவு லைட்** · Your family's photographs, at home — the small, steady edition.

> **Status:** 1.3.0 (phases 0–4 done; Control Panel, installers, Import and Sudar added). See [CHANGELOG](../CHANGELOG.md).

## 1. What Lite is

A fast, simple, private photo and video gallery for a household, in **Tamil and English**,
that runs well on modest Windows 10+ PCs, Macs and a Raspberry Pi, and never looks broken.
It reads your photo folders, never changes them, and shows them to your family on the home network.

**Guiding rules**

1. **Ninaivu's screens, lighter engine.** No new screens: the family gallery, the admin console
   and the share page are Ninaivu's, with the buttons for features Lite doesn't have removed.
2. **Fewer options, done well.** If a feature needs a settings page to explain it, it waits.
3. **Never broken.** No raw errors, no blank screens; every failure gets a plain message in the
   user's language and a way forward.
4. **Modest hardware first.** Smooth on a Raspberry Pi 4 and a 2015 Windows 10 laptop.
5. **Three dependencies.** Flask, Pillow, waitress. Everything else is optional
   (pillow-heif for HEIC, ffmpeg for video posters, OpenCV for sideways photos without a tag).
6. **Tamil and English are equal**, including server messages.
7. **HTTP on the home network** with sound basic security. HTTPS is a later phase.

## 2. Compatibility targets

| Area | Minimum we promise | How it is enforced |
| --- | --- | --- |
| Python | **3.10 to 3.13** from source; every installer carries exactly the version in `.python-version` (3.13.7) | CI matrix 3.10 and 3.13 on Windows, macOS, Linux; a test keeps the pins equal |
| OS | **Windows 10+**, macOS 12+, Ubuntu 22.04+/Debian 12+, Raspberry Pi 4 (64-bit) | Manual test pass before each release |
| Browsers | Current Chrome / Edge / Firefox, **Safari 16.4+**, Android Chrome | Browser test pass before each release |
| Screen | 320 px phone to large TV; touch, mouse and keyboard | Checked at 390 / 1280 px |
| Library size | 100,000 items | First timeline page ~120 ms; other calls < 400 ms (measured) |

Modern CSS and JS (ES modules, `?.`, `??`) are allowed; iOS below 16.4 is not supported.
ruff targets `py310`.

## 3. Scope

### In Lite 1.0
- **One port** (default **8080**): gallery at `/`, admin console at `/admin`, share pages at `/share/<token>`.
- **Sign-in exactly like Ninaivu**: profile picker, per-person entry (none / PIN / password),
  *Just looking* tile for public media (switchable), password sign-in for admins on the console.
- **Gallery**: timeline + scrubber, folders, favourites, albums, simple search, viewer, video,
  download (file or zip), language switch.
- **Roles** Admin / Family / Guest; **three levels** Public / Family / Hidden by folder rule or
  per item, with undo; a person can be limited to one folder.
- **Share links** for a photo or album, password and expiry; metadata-free copies for visitors.
- **Console**: first-day guide, overview (with role preview), library settings, people,
  visibility, settings, backup download.
- **Launcher** `start.cmd` / `start.sh`; start with Windows; systemd unit; daily index backups.
- **Installers** with their own Python: Windows (.exe), Linux / Raspberry Pi (.sh), macOS (.dmg),
  Docker; a release workflow builds them on a tag.
- **Control Panel** (Ninaivu's, lighter; Tk only): start/stop/restart, addresses, start at sign-in,
  log; opens after the first-time setup.
- **Optional move to Ninaivu** via `--export` ([UPGRADE.md](UPGRADE.md)); Ninaivu itself is not changed.

### Added in 1.3
- **Import** (`importer.py`, `takeout.py`, `api_import.py`, `static/js/archive.js`): Ninaivu's
  archive engine cut to the core — dated `YYYY/MM/DD` archive, hash-verified copies, content
  duplicates, dry run, audit, resume, Takeout sidecars and albums, adopt into the library. One
  thread, stdlib + Pillow. Also a first-day step that asks for the folders to sweep.
- **Sudar** (`static/js/sudar/`, `static/js/studio/`, `api_sudar.py`): Ninaivu's studio over its
  own develop engine, with the built-in plain-words planner and clothing colour; no model, no
  server inference. The one server call saves an administrator's copy beside the original.

- **Straighten** (`media.detect_rotation`, `Scanner._straighten`): the camera's tag is final;
  an untagged photograph is judged by its faces (Ninaivu's cheapest detector, OpenCV's Haar
  cascades, optional) in a quiet pass after the thumbnails, and turned in the index only.
  `POST /api/asset/<id>/rotate` is the administrator's hand.

### Not in Lite (stays in full Ninaivu)
AI with models (faces, search by description, generative edits, background and object tools,
upscaling), cloud/phone backup, rotation, deleting media, the importer's pacing, drive-health
sampling and classifiers, maps, disk health, tray icon, resource modes, Tailscale/zeroconf,
update checker, notifications, screen lock, HTTPS.

### Later phases (after 1.0)
Upload from phone browser · HTTPS · map view (offline tiles) · optional face grouping as a plug-in.

## 4. Architecture

```
ninaivu_lite/
  __main__.py     start, ports, banner, logging, --export, --reset-password
  __init__.py     light: version, copyright, lazy create_app
  app.py          create_app: blueprints, write guard, private-library guard, headers, errors
  config.py       settings.json, typed, atomic save
  db.py           SQLite, numbered migrations; Ninaivu-shaped tables
  auth.py         users, scrypt, PINs, sessions, throttles, setup code
  scanner.py      incremental walk, dates, folder rules, two thumbnail passes, watching
  media.py dates.py folders.py backups.py net.py export.py
  importer.py     one background thread: dated archive, hashes, duplicates, dry run, audit
  takeout.py      a Google Photos export's albums, made again after the import
  common.py       request helpers, visibility filter, Ninaivu item shape
  pages.py        /, /admin, /share/<token>, manifests, health, local stop
  control.py      state file, start/stop from outside, start at sign-in (stdlib only)
  panel.py        the Control Panel window (Tk)
  api_auth.py api_gallery.py api_share.py api_admin.py   Ninaivu-compatible JSON API
  api_import.py   the Import page's calls (Ninaivu's archive API, trimmed)
  api_sudar.py    Sudar's one call: save the edited copy beside the original
  templates/      index.html, admin.html, share.html   (Ninaivu's, trimmed)
  static/         Ninaivu's JS modules and CSS, trimmed; i18n/en.json, ta.json
                  js/studio/ the develop engine, js/sudar/ the studio, js/archive.js the Import page
launcher/start.py, start.cmd, start.sh, tools/
tests/
```

The API answers with the same shapes Ninaivu's screens expect (segment item tuples,
`User.public()` with a `can` block, `{"error", "status"}` errors), so the front end needed
removals, not rewrites.

### Engines
| Engine | Choice |
| --- | --- |
| Web server | waitress (Werkzeug if missing) |
| Database | SQLite, WAL with fallback to DELETE journal; covering index for the grid |
| Scanner | `os.scandir`, incremental by (size, mtime), one thread, optional folder watching |
| Thumbnails | Pillow `draft()` → WebP 256 and 640 px; colour placeholder |
| Video | browser playback with HTTP Range; `ffmpeg` poster if present |
| Passwords | scrypt via `hashlib` (`scrypt$n$r$p$salt$hash`, Ninaivu-compatible) |

## 5. Basic security over HTTP
See [SECURITY.md](../SECURITY.md): scrypt hashes, rate-limited sign-in, hashed session tokens,
`HttpOnly`/`SameSite=Lax` cookie, cross-site write refusal, CSP, setup code for remote first
setup, server-side visibility checks on every item, read-only photo access.

## 6. What came from Ninaivu
Screens, JS modules, CSS, i18n files and the API contract were taken from Ninaivu and trimmed.
Server code was re-written small against that contract, reusing Ninaivu's proven functions
(password hashing, date rules, EXIF, thumbnail recipe, setup code, LAN addresses, launcher).

## 7. Phases and milestones

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0. Foundation | layout, CI, licence, README EN/TA | done |
| 1. Gallery | config, db, scanner, thumbnails, timeline, viewer, video | done |
| 2. Family | sign-in, roles, visibility, favourites, albums, shares | done |
| 3. Ninaivu screens | gallery, console and share page from Ninaivu over the Lite API | done |
| 4. Release 1.0 | launchers, start with Windows/systemd, user guide EN/TA, export, docs | done |
| 5. Later | upload from phone, HTTPS, map, plug-ins | — |

## 8. Release checklist (every release)
- [ ] CI green on Python 3.10 and 3.13, Windows / macOS / Linux.
- [ ] Fresh install by double-click on Windows 10 and Windows 11.
- [ ] Raspberry Pi 4: 100,000-item scan finishes, memory under 400 MB, server stays responsive.
- [ ] Kill the server mid-scan → restart resumes, nothing is lost or duplicated.
- [ ] Browsers: iPad on iPadOS 16.4, Android Chrome, Firefox, Edge, Safari on macOS.
- [ ] Every screen checked in Tamil and English on phone and desktop widths.
- [ ] Unplug a photo drive while running → clear message, no crash, recovers when plugged back.
- [ ] Guest/Family/Admin access tests pass; share link with wrong password refuses.
- [ ] No page ever shows a Python traceback.

## 9. Decisions
- Windows 10 minimum; modern CSS/JS; Python 3.10+ with 3.13 recommended; HTTP only for now.
- Package `ninaivu_lite`; default port **8080**; one port with the console at `/admin`.
- Three visibility levels: **Public** (guests too), **Family** (default), **Hidden** (admins only).
- Reuse Ninaivu's screens; make functions lighter, not new screens.
- Sign-in exactly like Ninaivu (profile picker, optional PIN, guest tile, admin password).
- Upgrading to Ninaivu is optional: an export file only; Ninaivu is not modified.
