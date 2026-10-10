# Ninaivu Lite

**நினைவு லைட்** · *memory, lightly*

Your family's photographs, at home — the small, steady edition of
[Ninaivu](https://github.com/javajaga-usa/Ninaivu).

Ninaivu Lite is a private photo and video gallery for a household, in **Tamil and English**.
It runs on a computer at home, reads your photo folders without ever changing them, and shows
them to your family on phones, tablets and computers on the same Wi-Fi. Nothing leaves your house.

It uses **Ninaivu's own screens** — the family gallery, the admin console and the share
page — over a much lighter engine: no models, no cloud, three small dependencies. Two of
Ninaivu's tools come along, lightened: the **Import** that sweeps old drives into one
archive, and **Sudar**, the photo studio that runs in the browser.

> **Status: 1.10.0.** See the [changelog](CHANGELOG.md) and the [user guide](docs/USER-GUIDE.md)
> ([தமிழில்](docs/USER-GUIDE.ta.md)).

**[Download the latest release](https://github.com/javajaga-usa/Ninaivu-lite/releases/latest)**
· **[Ninaivu and Lite comparison](https://javajaga-usa.github.io/Ninaivu/#ninaivu-lite)**
· **[English guide](docs/USER-GUIDE.md)** ([PDF](docs/Ninaivu-Lite-User-Guide-English.pdf)) · **[தமிழ் வழிகாட்டி](docs/USER-GUIDE.ta.md)** ([PDF](docs/Ninaivu-Lite-User-Guide-Tamil.pdf))

## Why Lite

| | Ninaivu | Ninaivu Lite |
| --- | --- | --- |
| Goal | Everything a family library can do | The essentials, fast and hard to break |
| Screens | Family app, admin console, share page | The same screens, fewer buttons |
| AI with models (faces, search by description, generative edits) | Yes | No |
| Sudar, the photo studio | Yes, with the model-backed tools | Yes, in the browser |
| Sideways photos put right | A model, faces, or CLIP; with Undo | The camera's tag, then faces (optional OpenCV) |
| Import old drives into one archive | Yes, with pacing and drive-health checks | Yes, the essentials |
| Cloud backup, phone backup | Yes | No |
| Dependencies | Many, some large | Flask, Pillow, waitress |
| Runs on | Capable PCs | Windows 10+, macOS, Linux, Raspberry Pi 4 |

## Features at a glance

| For the family | For the administrator | Under the hood |
| --- | --- | --- |
| Timeline by the day each photo was taken, with a date bar and a year row on phones | Admin console: overview, library folders, rescans, people, visibility, settings, backup | Three dependencies: Flask, Pillow, waitress |
| Search by name, folder, date or camera | Three roles (Admin, Family, Guest) and Public / Family / Hidden per folder or per photo, with undo | Installers for Windows, macOS, Linux and Raspberry Pi that carry their own Python |
| Full-screen viewer: swipe, keys, zoom, slideshow, ambient frame, details, rotate | People with a PIN, a password or nothing, each seeing one folder or all | Nothing leaves the house: no accounts, no cloud, no telemetry, no update checks |
| Favourites per person; albums from any folder, shareable | Import old drives, cards and backups into one hash-checked archive filed by date; Google Takeout kept whole | Photographs and videos are only ever read; edits and turns are kept beside them or in the index |
| Share links with a password and an expiry; the visitor's copy carries no location | Sideways photos put right during the scan (camera tag, then faces with optional OpenCV) | gzip, long caches and a virtual grid: a 100,000-photo library scrolls on a phone |
| Sudar photo studio in the browser: light, colour, detail, crops, looks, plain-words requests, clothing colour | Control Panel: start, stop, restart, addresses for phones, start with the computer, getting ready for an update | SQLite index with numbered migrations; thumbnails and viewing copies cached in the data folder |
| A photo as your sign-in picture; Tamil or English per person | Daily recovery zips (index, settings, profile pictures; the last seven kept) and `--restore`; export for Ninaivu | Tests on Windows, macOS and Linux, Python 3.10 and 3.13, on every push to `main` and every pull request |

## What it does

- **Gallery** at `http://<computer>:8080/` — timeline with a date scrubber, folders,
  favourites, albums, simple search (name, folder, date, camera), a full-screen viewer with
  swipe and arrow keys, video playback, download.
- **Sign-in like Ninaivu** — a profile picker; each person enters with no secret, a PIN or a
  password (the administrator's tile takes the password); a *Just looking* tile shows public
  photos to visitors (can be turned off). A tile shows initials on a colour, or a photograph
  the person chose from the library.
- **Three roles** — Admin, Family, Guest. Every photo is **Public**, **Family** or **Hidden**,
  set per folder (new files follow the folder) or per photo, with undo.
- **Admin console** at `/admin` — overview, library folders and rescans, people (role,
  PIN/password, which folder they see), visibility, settings and a backup download.
- **Import** — sweep old drives, cards and backup folders into one archive filed by the day
  each photo was taken, every copy hash-checked, duplicates left in place, sources never
  changed. Google Takeout exports keep their dates, places, descriptions and albums.
- **Sideways photos put right** during the scan: by the camera's tag, and, with the optional
  OpenCV extra, by the faces in a photo that has none. Stored in the index; files are never
  changed. Rotate in the viewer corrects any by hand.
- **Sudar** — edit a photo in the browser: light, colour, detail, crops, looks, *make it
  warmer* in plain words, clothing colour. No model, nothing leaves the house, the original
  is never changed; an administrator can save the result as a copy beside it.
- **Control Panel** — a small window (like Ninaivu's) to start, stop and restart Ninaivu
  Lite, open the gallery and console, see the address for phones, and start with the computer.
- **Share links** for one photo or an album, with an optional password and expiry.
  Visitors get a copy without location or camera data.
- **Tamil and English** on every screen, chosen per person.
- **Moves up to Ninaivu** if you ever need more: `--export` writes one file that Ninaivu can
  read ([details](docs/UPGRADE.md)). Optional — Lite is complete on its own.

## Install

The simplest way is an **installer** from the
[releases page](https://github.com/javajaga-usa/Ninaivu-lite/releases): it needs no Python
and downloads nothing.

| | |
| --- | --- |
| Windows 10 / 11 | `Ninaivu-Lite-<version>-windows-x64.exe` — the Control Panel opens when it finishes |
| Windows portable | `Ninaivu-Lite-<version>-windows-x64-portable.zip` — extract and open `Ninaivu Lite.exe`; only it and its log sit at the top, the program is in `app`, the data in `data` |
| Linux PC | `sh Ninaivu-Lite-<version>-linux-amd64.sh` (with `sudo`, for a server: see below) |
| Raspberry Pi 4 / 5 (64-bit OS) | `sh Ninaivu-Lite-<version>-linux-arm64.sh` |
| macOS | the `.dmg` for Apple silicon (`arm64`) or Intel (`x86_64`) |
| Docker | `docker compose -f installers/docker/docker-compose.yml up -d` |

How they are built: [installers/README.md](installers/README.md).

### Updating

Ninaivu Lite never looks for updates by itself and asks nothing of GitHub or anywhere
else: a newer version is a setup file you download from the
[releases page](https://github.com/javajaga-usa/Ninaivu-lite/releases) when you choose to.

1. **First stop Ninaivu Lite.** In the Control Panel press **Stop**, then close the Control
   Panel. On a Linux server:
   `sudo systemctl stop ninaivu-lite`.
2. **Run the newer installer** over the old one: the same `.exe`, `.sh` or `.dmg` as the
   first time. On a Mac, drag the new app over the old one in Applications.
3. Start it again (the Windows and Linux installers do it for you if it was running).

Your photos, people, settings, index and backups are kept: they live in the data folder, not
with the program, and no installer touches it. The first start after an update brings the
index forward on its own, after keeping a copy of it as it was in the data folder's
`backups` (a `before-update-…` zip). If you forget to stop it, the Windows and Linux
installers offer to stop it for you, and refuse to replace a copy they cannot stop rather
than leave it half updated. For the portable zip, extract the new version to a new folder
and copy the old `data` folder into it (see `app\README-PORTABLE.txt`).

**The releases are not code-signed yet.** Free signing through the
[SignPath Foundation](https://signpath.org) has been applied for and is pending; until then
Windows SmartScreen says *Windows protected your PC* (**More info → Run anyway**), a PC with
**Smart App Control** on refuses the installer, and macOS asks before opening the app
(**System Settings → Privacy & Security → Open Anyway**). The Windows installer installs for
you alone by default; if you choose to install it for everyone on the computer, or start it
from an administrator's account, Windows asks for administrator permission (UAC). The policy
signing will follow is in [docs/CODE-SIGNING.md](docs/CODE-SIGNING.md).

**Linux, installed with `sudo`** (a server or a Raspberry Pi): the program goes in
`/opt/ninaivu-lite`, and a system service starts it at boot as its own `ninaivu-lite`
account, never as root. Its data folder is `/var/lib/ninaivu-lite`. That account must be
able to read your photo folders, and to pass through every folder above them; for example:

```sh
sudo setfacl -R -m u:ninaivu-lite:rX /home/me/Pictures      # what is there now
sudo setfacl -R -d -m u:ninaivu-lite:rX /home/me/Pictures   # and what is added later
sudo setfacl -m u:ninaivu-lite:x /home/me                   # each folder above it
sudo systemctl restart ninaivu-lite
```

The installer names any folder the account cannot read. Under the service, the "a drive
was connected" notice may not see drives or phones the desktop opens for you (they are
opened for your account, not the service's): add their folders under *Import* by hand.
Without `sudo`, everything stays in your own account (`~/.local/share/ninaivu-lite`).

**Phones cannot open it on Linux?** A firewall may be in the way. Let port 8080 in:
`sudo ufw allow 8080/tcp` (Ubuntu, Raspberry Pi OS), or
`sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload` (Fedora).

## Start from a download of this repository

You need **Python 3.10 or newer** (3.13 recommended: it is the one the installers carry) — from [python.org](https://www.python.org/downloads/);
on Windows tick *Add python.exe to PATH*.

**Windows:** download this repository, then double-click **`start.cmd`**
(or drag your photo folder onto it).
**macOS / Linux:** `./start.sh` (or `./start.sh ~/Pictures`).

The first start prepares everything in a `.venv` folder (a minute or two; it needs the
internet once). It then opens your browser at `http://localhost:8080` and prints the address
to open on phones. The first visit asks you to make the administrator; after that, the
console walks you through adding folders and family. Making the administrator from another
device (a phone) asks for a **setup code**: it is printed in the window Ninaivu Lite was
started from and written to `logs/ninaivu-lite.log` in the data folder, and the Control Panel
shows it. For the Linux service: `journalctl -u ninaivu-lite | grep code` (`journalctl --user
-u ninaivu-lite` without `sudo`); for Docker: `docker logs ninaivu-lite`.

When the first-time setup finishes, the **Control Panel** opens too. Open it any time with
**`Start - Ninaivu Lite Control Panel.vbs`** (Windows, no console window) or
`./start.sh --panel`; from there Ninaivu Lite runs in the background, with no window to keep open.

Run it by hand instead:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-straighten.txt   # optional: sideways photos without a tag
python -m ninaivu_lite "D:\Photos"          # Windows
python3 -m ninaivu_lite ~/Pictures         # macOS and Linux
```

| Option | |
| --- | --- |
| `--port 8080` | port (the next free one if taken) |
| `--host 0.0.0.0` | address to listen on |
| `--data DIR` | where settings, index and thumbnails live |
| `--no-browser` | don't open a browser |
| `--export FILE` | write the move-to-Ninaivu file, then stop |
| `--reset-password NAME` | set a new password for someone (a forgotten admin password), then stop |
| `--restore ZIP` | put back a backup zip (stop Ninaivu Lite first), then stop |

**A forgotten password, or a backup to put back**, by how it was installed (stop Ninaivu
Lite first):

| Installed with | Run |
| --- | --- |
| Windows installer, or Linux without `sudo` | `ninaivu-lite --reset-password NAME` (a new Command Prompt on Windows) |
| Linux with `sudo` | `sudo systemctl stop ninaivu-lite`, then `sudo -u ninaivu-lite /usr/local/bin/ninaivu-lite --reset-password NAME` |
| Windows portable zip | in its `Ninaivu Lite` folder: `app\Python\python.exe -m ninaivu_lite --data "<that folder>\data" --reset-password NAME` |
| this repository | `python -m ninaivu_lite --reset-password NAME`, inside the activated `.venv` |
| Docker | `docker compose -f installers/docker/docker-compose.yml run --rm ninaivu-lite python -m ninaivu_lite --data /data --reset-password NAME` |

`--restore <zip>` goes in the same place as `--reset-password NAME` (with Docker, the zip
must be in a folder the container can see). Add `--data DIR` if you started Ninaivu Lite with
a data folder of your own. What was in the data folder before a restore is kept in a
`before-restore-…` folder inside it, never deleted.

**Start with the computer:** tick *Start Ninaivu Lite when I sign in* in the Control Panel
(or run `tools\start-with-windows.cmd`); on a Raspberry Pi or a server, use the
[Linux installer](#install), which sets up a systemd service that starts it at boot.

**Optional extras:** `pip install pillow-heif` shows iPhone HEIC photos. `ffmpeg` on the PATH
gives videos the browsers cannot play a preview picture (the family's browsers make the others
without it), and **is needed to share videos with guests and share links**: it removes a
video's location before it is sent. Without it such a video is refused to them, unless an
administrator turns on *Send videos to guests and share links as they are when their location
cannot be removed* in *Settings*. Without these extras the files still appear, with a plain
tile and a download button.

**Names it answers to:** Ninaivu Lite answers only to this computer's own names: any of its
addresses (`http://192.168.1.20:8080`), its network name (`mypc`, `mypc.local`) and
`localhost`. Any other name (a reverse proxy, a name of your own) goes in `allowed_hosts` in
`settings.json` in the data folder, edited while Ninaivu Lite is stopped
(`"allowed_hosts": ["photos.home"]`), or in the
`NINAIVU_ALLOWED_HOSTS` environment variable, separated by commas; with Docker, set
`ALLOWED_HOSTS`, since a container does not know the computer's name.

**Home network only:** a request from an internet address (a port forwarded on the router, a
tunnel such as ngrok or Cloudflare Tunnel) is refused with *"Ninaivu Lite answers only the home
network."* Phones and computers on the same network, and private VPNs such as Tailscale, are
answered. To reach it from the internet on purpose (behind HTTPS of your own), set
`"allow_internet": true` in `settings.json`, or `NINAIVU_ALLOW_INTERNET=1` (with Docker,
`ALLOW_INTERNET=1`).

## Security

Lite uses plain **HTTP** and is meant for your **home network only** — do not open it to the
internet. Passwords and PINs are stored as scrypt hashes, sign-in is rate-limited, and other
websites cannot act on your behalf. See [SECURITY.md](SECURITY.md).

## Developing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, pull requests and reporting bugs.

```bash
python -m pip install -r requirements-dev.txt
ruff check .
python -m pytest
```

Every pull request runs on Windows, macOS and Linux with Python 3.10 and 3.13. The screens are
Ninaivu's (`ninaivu_lite/templates`, `ninaivu_lite/static`); strings are in
`ninaivu_lite/static/i18n/en.json` and `ta.json`, keyed by the English sentence.

## Code signing policy

**Not signed yet:** the application to the [SignPath Foundation](https://signpath.org) for
free code signing is pending, and until it is approved the releases are unsigned. Once it is,
code signing will be provided free by [SignPath.io](https://about.signpath.io), with a
certificate by the SignPath Foundation. The Windows installer is built from this
repository by the public release workflow on GitHub, and every signing request will be
approved by hand. Committers, reviewers and approvers: [Jagadeesh Rajendran](https://github.com/javajaga-usa).
Privacy: this program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.
Full policy: [docs/CODE-SIGNING.md](docs/CODE-SIGNING.md).

## Licence

MIT. © 2026 Jagadeesh Rajendran. See [LICENSE](LICENSE).

---

## தமிழில்

**நினைவு லைட்** — உங்கள் குடும்பப் புகைப்படங்கள், உங்கள் வீட்டிலேயே.
இது [நினைவு](https://github.com/javajaga-usa/Ninaivu) மென்பொருளின் சிறிய, நிலையான பதிப்பு.

வீட்டிலுள்ள ஒரு கணினியில் இயங்கி, உங்கள் புகைப்படக் கோப்புறைகளை மாற்றாமல் படித்து,
அதே Wi-Fi-இல் உள்ள கைப்பேசி, டேப்லெட், கணினிகளில் குடும்பத்தினருக்குக் காட்டுகிறது.
எதுவும் உங்கள் வீட்டை விட்டு வெளியே போவதில்லை. நினைவின் அதே திரைகள் — குறைவான பொத்தான்கள்,
இலகுவான இயந்திரம்.

**என்ன செய்யும்:** காலவரிசை, கோப்புறைகள், பிடித்தவை, ஆல்பங்கள், எளிய தேடல், முழுத்திரைப் பார்வை;
சுயவிவரத் தேர்வுடன் உள்நுழைவு (PIN அல்லது கடவுச்சொல் விருப்பம்); நிர்வாகி, குடும்பம், விருந்தினர்;
ஒவ்வொரு படமும் பொது, குடும்பம் அல்லது மறைக்கப்பட்டவை; `/admin` இல் நிர்வாகப் பக்கம்;
கடவுச்சொல், காலாவதியுடன் பகிர்வு இணைப்புகள்; தமிழும் ஆங்கிலமும்.

**தொடங்க:** Python 3.10 அல்லது புதியது தேவை. Windows-இல் **`start.cmd`** ஐ இருமுறை சொடுக்கவும்;
macOS / Linux-இல் `./start.sh`. உலாவி `http://localhost:8080` இல் திறக்கும்; கைப்பேசியில் திறக்க
வேண்டிய முகவரியும் காட்டப்படும். முதல் முறை நிர்வாகிக் கணக்கை உருவாக்கச் சொல்லும்.

முழு வழிகாட்டி: [docs/USER-GUIDE.ta.md](docs/USER-GUIDE.ta.md).

**பாதுகாப்பு:** இது வீட்டு நெட்வொர்க்குக்கு மட்டும். இணையத்துக்குத் திறக்க வேண்டாம்.
