# Ninaivu Lite

**நினைவு லைட்** · *memory, lightly*

Your family's photographs, at home — the small, steady edition of
[Ninaivu](https://github.com/javajaga-usa/Ninaivu).

Ninaivu Lite is a private photo and video gallery for a household, in **Tamil and English**.
It runs on a computer at home, reads your photo folders without ever changing them, and shows
them to your family on phones, tablets and computers on the same Wi-Fi. Nothing leaves your house.

It uses **Ninaivu's own screens** — the family gallery, the admin console and the share
page — over a much lighter engine: no AI, no cloud, three small dependencies.

> **Status: 1.2.1.** See the [changelog](CHANGELOG.md) and the [user guide](docs/USER-GUIDE.md)
> ([தமிழில்](docs/USER-GUIDE.ta.md)).

## Why Lite

| | Ninaivu | Ninaivu Lite |
| --- | --- | --- |
| Goal | Everything a family library can do | The essentials, fast and hard to break |
| Screens | Family app, admin console, share page | The same screens, fewer buttons |
| AI (faces, search by description, studio) | Yes | No |
| Cloud backup, phone backup, editing | Yes | No |
| Dependencies | Many, some large | Flask, Pillow, waitress |
| Runs on | Capable PCs | Windows 10+, macOS, Linux, Raspberry Pi 4 |

## What it does

- **Gallery** at `http://<computer>:8080/` — timeline with a date scrubber, folders,
  favourites, albums, simple search (name, folder, date, camera), a full-screen viewer with
  swipe and arrow keys, video playback, download.
- **Sign-in like Ninaivu** — a profile picker; each person enters with no secret, a PIN or a
  password; a *Just looking* tile shows public photos to visitors (can be turned off).
- **Three roles** — Admin, Family, Guest. Every photo is **Public**, **Family** or **Hidden**,
  set per folder (new files follow the folder) or per photo, with undo.
- **Admin console** at `/admin` — overview, library folders and rescans, people (role,
  PIN/password, which folder they see), visibility, settings and a backup download.
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
| Linux PC | `sh Ninaivu-Lite-<version>-linux-amd64.sh` |
| Raspberry Pi 4 / 5 (64-bit OS) | `sh Ninaivu-Lite-<version>-linux-arm64.sh` |
| macOS | the `.dmg` for Apple silicon (`arm64`) or Intel (`x86_64`) |
| Docker | `docker compose -f installers/docker/docker-compose.yml up -d` |

How they are built: [installers/README.md](installers/README.md).

## Start from a download of this repository

You need **Python 3.10 or newer** (3.13 recommended: it is the one the installers carry) — from [python.org](https://www.python.org/downloads/);
on Windows tick *Add python.exe to PATH*.

**Windows:** download this repository, then double-click **`start.cmd`**
(or drag your photo folder onto it).
**macOS / Linux:** `./start.sh` (or `./start.sh ~/Pictures`).

The first start prepares everything in a `.venv` folder (a minute or two; it needs the
internet once). It then opens your browser at `http://localhost:8080` and prints the address
to open on phones. The first visit asks you to make the administrator; after that, the
console walks you through adding folders and family.

When the first-time setup finishes, the **Control Panel** opens too. Open it any time with
**`Start - Ninaivu Lite Control Panel.vbs`** (Windows, no console window) or
`./start.sh --panel`; from there Ninaivu Lite runs in the background, with no window to keep open.

Run it by hand instead:

```bash
python -m pip install -r requirements.txt
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

**Start with the computer:** tick *Start Ninaivu Lite when I sign in* in the Control Panel
(or run `tools\start-with-windows.cmd`); on a Raspberry Pi or server, use the systemd unit in
`tools/ninaivu-lite.service`.

**Optional extras:** `pip install pillow-heif` shows iPhone HEIC photos; `ffmpeg` on the PATH
gives videos a preview picture. Without them those files still appear, with a plain tile and a
download button.

## Security

Lite uses plain **HTTP** and is meant for your **home network only** — do not open it to the
internet. Passwords and PINs are stored as scrypt hashes, sign-in is rate-limited, and other
websites cannot act on your behalf. See [SECURITY.md](SECURITY.md).

## Developing

```bash
python -m pip install -r requirements-dev.txt
ruff check .
python -m pytest
```

Every pull request runs on Windows, macOS and Linux with Python 3.10 and 3.13. The screens are
Ninaivu's (`ninaivu_lite/templates`, `ninaivu_lite/static`); strings are in
`static/i18n/en.json` and `ta.json`, keyed by the English sentence.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org). The Windows installer is built from this
repository by the public release workflow on GitHub and every signing request is approved by
hand. Committers, reviewers and approvers: [Jagadeesh Rajendran](https://github.com/javajaga-usa).
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

**என்ன செய்யும்:** காலவரிசை, கோப்புறைகள், விருப்பங்கள், ஆல்பங்கள், எளிய தேடல், முழுத்திரைப் பார்வை;
சுயவிவரத் தேர்வுடன் உள்நுழைவு (PIN அல்லது கடவுச்சொல் விருப்பம்); நிர்வாகி, குடும்பம், விருந்தினர்;
ஒவ்வொரு படமும் பொது, குடும்பம் அல்லது மறைக்கப்பட்டவை; `/admin` இல் நிர்வாகப் பக்கம்;
கடவுச்சொல், காலாவதியுடன் பகிர்வு இணைப்புகள்; தமிழும் ஆங்கிலமும்.

**தொடங்க:** Python 3.10 அல்லது புதியது தேவை. Windows-இல் **`start.cmd`** ஐ இருமுறை சொடுக்கவும்;
macOS / Linux-இல் `./start.sh`. உலாவி `http://localhost:8080` இல் திறக்கும்; கைப்பேசியில் திறக்க
வேண்டிய முகவரியும் காட்டப்படும். முதல் முறை நிர்வாகிக் கணக்கை உருவாக்கச் சொல்லும்.

முழு வழிகாட்டி: [docs/USER-GUIDE.ta.md](docs/USER-GUIDE.ta.md).

**பாதுகாப்பு:** இது வீட்டு நெட்வொர்க்குக்கு மட்டும். இணையத்துக்குத் திறக்க வேண்டாம்.
