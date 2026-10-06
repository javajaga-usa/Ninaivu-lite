# Ninaivu Lite — user guide

[தமிழில் படிக்க](USER-GUIDE.ta.md)

Ninaivu Lite shows your family's photos and videos to everyone at home, on any phone, tablet or
computer on the same Wi-Fi. It **only reads** your photo folders: it never moves, edits or
deletes a photograph. (The importer copies photos *into* the library from old drives, and
Sudar can save an edited *copy* beside an original; neither changes a photograph you have.)

## 1. Start it

**With an installer** (from the releases page): run it. On Windows the Control Panel opens when
it finishes — press **Start**, then **Open the family app**. Nothing else is needed; skip to
*The Control Panel* below. The installers are not code-signed yet, so Windows may say
*Windows protected your PC* (**More info → Run anyway**); installing for everyone on the
computer asks for administrator permission.

**On Linux**, run `sh Ninaivu-Lite-<version>-linux-amd64.sh` (`-arm64.sh` on a Raspberry Pi).
Run as yourself, it keeps everything in your account and starts when the computer does. Run
with `sudo` (a server, or a computer the whole house uses), it runs as its own
`ninaivu-lite` account; see *Linux, installed with sudo* in section 7.

**Without installing anything** (Windows): download the `…-windows-x64-portable.zip` from the
releases page, right-click it, choose *Properties*, tick *Unblock*, then *Extract All* to a
folder that stays put (or a pendrive). Double-click **`Ninaivu Lite Control Panel.vbs`** in it.
Everything stays in that folder: the program, and the family's settings, people and index
in the `data` folder that appears beside it the first time. To upgrade, extract the new
version to a new folder and copy the old `data` folder into it. Its `README-PORTABLE.txt`
says how to move or remove it.

**Without an installer, from the source:**

1. Install **Python 3.10 or newer** from python.org. On Windows, tick *Add python.exe to PATH*.
2. Download Ninaivu Lite and unzip it somewhere permanent (for example `C:\Ninaivu-lite`).
3. Double-click **`start.cmd`** (Windows) or run **`./start.sh`** (macOS, Linux).
   The first start takes a minute or two and needs the internet once.
4. Your browser opens at `http://localhost:8080`. The window also prints an address such as
   `http://192.168.1.20:8080` — open that on phones and tablets.

Keep the window open while the family uses it. Closing it stops Ninaivu Lite.

### The Control Panel

When the first-time setup finishes, the **Control Panel** opens as well. Open it any time by
double-clicking **`Start - Ninaivu Lite Control Panel.vbs`** (Windows) or with
`./start.sh --panel` (macOS, Linux). It shows whether Ninaivu Lite is running and the
addresses to open, and has:

- **Start**, **Stop**, **Restart** — started from here, Ninaivu Lite runs in the background
  with no window to keep open;
- **Open the family app** and **Open the console**;
- **Start Ninaivu Lite when I sign in** — tick it once and it starts with the computer;
- **Open the log** and **Open the data folder**, for when something needs looking into;
- **Updates** — press **Check now** to ask GitHub whether a newer Ninaivu Lite exists; if so,
  the panel says which version with a **Download** button to the release page. Tick *Tell me
  when a new version is available* and it asks once a day by itself. Nothing is asked until
  you do one of those, and nothing about your library is ever sent.
  Before running the installer you downloaded, press **Stop** and close the Control Panel:
  Windows cannot replace a program that is in use. **Download** offers to do both for you,
  and the installer waits and asks until nothing is in use. Your settings, people, index
  and photographs are untouched by an upgrade.

Closing the Control Panel leaves Ninaivu Lite running. On a Raspberry Pi or a server without
a screen, the Linux installer sets up a service that starts it at boot. Installed with `sudo`,
there is no Control Panel: use `sudo systemctl start|stop|restart ninaivu-lite`.

## 2. The first day

1. The first visit asks you to **make the administrator**: your name, a username and a
   password of at least 8 characters. If you set this up from a phone, it also asks for the
   short **setup code**. The Control Panel shows it; it is also printed in the window
   Ninaivu Lite was started from (a copy of the source) and written to
   `logs/ninaivu-lite.log` in the data folder (*Open the log*). On a Linux service, run
   `journalctl -u ninaivu-lite | grep code` (with `--user` if installed without `sudo`); with
   Docker, `docker logs ninaivu-lite`.
2. The admin console then helps you:
   - **Choose the photo folder** — browse to it and press *Use this folder*. You can add
     more folders later under *Library settings*.
   - **Bring in old photos** — the walk-through asks which folders or drives hold photos
     to bring in (old drives, memory cards, phone backups, backup folders). Add the top
     folder of each: everything inside it is scanned, however deep. Press *Start the
     import* and the photos are copied into an archive inside your library folder, filed
     by the day each was taken, every copy checked. The originals are never changed, moved
     or deleted. Skip it if there is nothing to bring in; *Import*, under *Library*, does
     the same at any time.
   - **Add your family** — under *People*, *Add someone*: a name, a role, and how they
     enter (no secret, a PIN, or a password).
3. Indexing starts by itself. Photos appear as they are found; small previews are made in
   the background. A large library can take a while the first time; later starts are quick.

## 3. Roles and who sees what

| Role | Can |
| --- | --- |
| **Admin** | everything: folders, people, who sees what, settings |
| **Family** | see Public and Family photos, download, favourites, albums, share links |
| **Guest** | see Public photos only |

Every photo is one of:

- **Public** — everyone, including guests and *Just looking* visitors
- **Family** — family members and admins (the default for new photos)
- **Hidden** — admins only

Set this in the console under **Visibility**: choose a folder and a level. The rule covers
everything inside that folder, including photos added later. Admins can also change one photo
while viewing it in the gallery. A folder change can be undone with *Undo that change*.

You can also give a person **one folder** (for example only `Trips`): they then see nothing
outside it.

## 4. Signing in

On the gallery page everyone picks their own picture:

- a profile with no secret opens straight away;
- a profile with a **PIN** or **password** asks for it;
- **Just looking** shows only Public photos, with no sign-in. Turn it off in the console under
  *Settings* if the library should be private.

An administrator's tile is locked and asks for their password. The console,
`http://<computer>:8080/admin`, takes the username and password. To leave the gallery, open your
profile (top right) and choose *Sign out* (the door symbol).

**Your picture on the sign-in screen.** Every tile starts as your initials on your colour. To
use a photograph instead, open it in the gallery and choose *Use as my profile picture* from
its ⋯ menu: the middle of it becomes a small square beside your name. *Remove picture* in your
profile puts the initials back; an administrator can remove anyone's from *People*. The
picture is shown to whoever reaches the sign-in screen, so choose one you are happy to show.

## 5. Using the gallery

- **Timeline** — newest first; drag the date bar on the right to jump to a year. On a phone
  a row of years sits above the photographs instead: tap one to see that year.
- **Folders** — the left menu lists folders with their counts.
- **Search** — type a file name, a folder, a year or month, or a camera name.
- **Viewer** — tap a photo. Swipe or use ← → to move, Esc to close. ♡ adds it to your
  favourites; ⓘ shows the date, size and camera; ⬇ downloads the original.
- **Select** — *Select all* on a day, or tick photos (on a phone, press and hold a photo, then
  tap others), to add them to an album or download them
  together.
- **Albums** — make an album from a selection; albums can be shared.
- **Language** — English or தமிழ், from the language button at the top. Each person's choice
  is remembered.
- **Sudar** (⋯ → *Edit with Sudar (AI)*) — the photo studio, in the browser: light, colour, detail
  and framing sliders, looks, suggestions measured from the picture, *make it warmer* in plain
  words (*AI assist*), and clothing colour by brush. Nothing is sent anywhere and the original
  is never changed: download the result, or, as the administrator, *Save copy to Ninaivu
  library* puts it beside the original.
- **Sideways photos** are put right during the scan, from the camera's own tag, and, when the
  OpenCV extra is installed, from the faces in a photo that has none. The administrator can
  turn any photo by hand with ⋯ → *Rotate* (or **R**); the file itself is never changed.

### Import: old drives into one archive

*Library → Import* in the console sweeps photos and videos off any number of old drives,
cards and backup folders into one archive filed as `YYYY/MM/DD`:

1. **Sources** — add each top folder or drive. Every folder inside is scanned.
2. **Destination** — where the archive is built. Choose the archive's own root; pick a folder
   inside one and Ninaivu uses the root instead, and says so.
3. **Run** — *Start consolidation* copies and checks every file (hashed as it is read, read
   back from the archive before it counts). Duplicates are found by content and left where
   they are; the same name with different bytes gets `_1`. **Start also resumes** an
   interrupted run. *Dry run* decides everything and writes nothing; *Audit archive* re-reads
   every archived file and re-checks its hash.

When it finishes, *Add to library* makes the archive part of the gallery. A Google Photos
export (Takeout) comes across with its dates, places and descriptions, and *Make the albums*
recreates its albums once the archive is indexed. *Export manifest* writes a list of every
file with its hash.

### A pendrive, an external drive or a phone plugged in

Plug a USB pendrive, a memory card, an external hard drive or a phone (by its cable) into the
computer Ninaivu Lite runs on, and the console (and the Control Panel, if it is open) asks
what to do with it:

- **Import media from this drive** opens *Import* with the drive as the source. Check the
  destination and press *Start consolidation*. Nothing on the drive is changed.
- **Export media to this drive** copies the library's photos and videos onto the drive, into
  a `Ninaivu Lite` folder, keeping the library's own folders. Only what is new is copied the
  next time; nothing already on the drive is overwritten or deleted.
- **Not now** asks again only when the drive is next plugged in.

The computer's own disks, and a drive the library itself is on, are never asked about.

**A phone** is offered *Import media from this phone* only. Unlock it and choose *File
transfer* (Android) or *Trust this computer* (iPhone), or Windows cannot see its photos. On
Windows, Ninaivu Lite copies the camera folders (DCIM, Pictures, Movies) across the cable,
imports them into the archive, then removes its temporary copies; the next time, only new
photos come across. Nothing on the phone is changed. On Linux, open the phone in the file
manager first; on a Mac, phones are not detected. When Ninaivu Lite was installed on Linux
with `sudo`, it runs as its own account and may not see drives or phones your desktop opens:
add their folders under *Import* yourself.

## 6. Sharing with someone outside the family

Choose **Share link** in the viewer, or **Share album** on an album. You can add a password and an expiry date.
Send the link to someone on your home network. They see only that photo or album, as a copy
without location or camera details. A video is sent with its location removed too, which
needs ffmpeg on the computer; without it, a guest or someone with a link is told the video
cannot be shared, unless an administrator turns on *Send videos to guests and share links as
they are when their location cannot be removed* in Settings. Your list of links is in your
profile; you can turn a link off at any time.

> Links work only on your home network. Ninaivu Lite is not meant to be reached from the internet.

## 7. Looking after it

- **Backups** — every day Ninaivu Lite makes a full recovery zip (the index with people,
  albums, favourites, share links and visibility, the settings, and profile pictures) in
  `backups/` in its data folder, and keeps the last seven. *Settings → Download a backup*
  gives you one to keep elsewhere. To put one back, stop Ninaivu Lite and run the command
  below with `--restore <zip>`; what was there is kept in a `before-restore-…` folder inside
  the data folder, never deleted. Your photos are not in the zip: they are your own files,
  so back them up as you always do.
- **Data folder** — `%LOCALAPPDATA%\Ninaivu-lite` on Windows, `~/Library/Application
  Support/Ninaivu-lite` on macOS, `~/.local/share/ninaivu-lite` on Linux; the `data` folder
  beside the program for the portable zip; `/var/lib/ninaivu-lite` for Linux installed with
  `sudo`; the `ninaivu_lite_data` volume for Docker. It holds settings, the index, previews,
  backups and logs, never your photos.
- **Forgot the admin password?** Stop Ninaivu Lite, then on that computer run the command
  for how it was installed (`--restore <zip>` goes in the same place):
  - **Windows installer, or Linux without `sudo`:** `ninaivu-lite --reset-password <username>`
    (on Windows, in a new Command Prompt).
  - **Linux with `sudo`:** `sudo systemctl stop ninaivu-lite`, then
    `sudo -u ninaivu-lite /usr/local/bin/ninaivu-lite --reset-password <username>`, then
    `sudo systemctl start ninaivu-lite`.
  - **Portable zip:** in its folder, `Python\python.exe -m ninaivu_lite --data "<that
    folder>\data" --reset-password <username>`.
  - **A copy of the source:** `python -m ninaivu_lite --reset-password <username>` (inside the
    folder, after `.venv\Scripts\activate` on Windows or `source .venv/bin/activate`
    elsewhere).
  - **Docker:** `docker compose -f installers/docker/docker-compose.yml run --rm ninaivu-lite
    python -m ninaivu_lite --data /data --reset-password <username>`.
- **A drive unplugged?** Its photos show as unavailable and come back when it returns.
  Nothing is lost.
- **Updating** — with an installer, run the new installer (the Control Panel's
  **Download** button opens its page); on Linux, run the newer `.sh` the same way as the first.
  For the portable zip, extract the new version to a new folder and copy the old `data`
  folder into it. For a copy of the source, replace the program files and start it again.
  Your data folder stays as it is.
- **Linux, installed with `sudo`** — Ninaivu Lite runs as a service under its own
  `ninaivu-lite` account, never as root, with its data in `/var/lib/ninaivu-lite`. That
  account must be able to read your photo folders and pass through every folder above them.
  The installer names any it cannot read; let it in with, for example:
  `sudo setfacl -R -m u:ninaivu-lite:rX /home/me/Pictures` (what is there now),
  `sudo setfacl -R -d -m u:ninaivu-lite:rX /home/me/Pictures` (what is added later),
  `sudo setfacl -m u:ninaivu-lite:x /home/me` (each folder above it), then
  `sudo systemctl restart ninaivu-lite`.

## 8. Moving up to Ninaivu (optional)

If you later want faces, search by description or cloud backup, the full Ninaivu can take
over your library. Run `python -m ninaivu_lite --export lite-export.json` (with an installer,
`ninaivu-lite --export lite-export.json`; otherwise the command in section 7 for a forgotten
password, with `--export lite-export.json` in its place) and give that file to Ninaivu. See
[UPGRADE.md](UPGRADE.md).

## 9. Common questions

**The phone can't open the address.** Use the address the Control Panel or the window shows (not
`localhost`), make sure the phone is on the same Wi-Fi, and allow Python through the Windows
firewall when asked. On Linux, let port 8080 through the firewall:
`sudo ufw allow 8080/tcp` (Ubuntu, Raspberry Pi OS) or
`sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload` (Fedora).

**"This address is not one Ninaivu Lite answers to."** Ninaivu Lite answers only to this
computer's own names: its addresses (such as `http://192.168.1.20:8080`), its network name
(`mypc` or `mypc.local`) and `localhost`. To use another name (a name of your own, or a
reverse proxy), stop Ninaivu Lite and add it to `"allowed_hosts"` in `settings.json` in the
data folder, for example `"allowed_hosts": ["photos.home"]`, or to the `NINAIVU_ALLOWED_HOSTS`
environment variable (names separated by commas; with Docker, `ALLOWED_HOSTS`), then start
it again.

**A guest or a share link says a video cannot be shared.** Install `ffmpeg` on the computer:
Ninaivu Lite needs it to remove a video's location before sending it. Or, as the
administrator, turn on *Send videos to guests and share links as they are when their
location cannot be removed* in *Settings* (the video then keeps its location).

**Some videos show a plain tile.** A video gets its preview picture the first time a family
member or administrator scrolls past it or plays it: their browser draws one frame and
Ninaivu keeps it for everyone. A video the browser cannot play keeps the plain tile (install
`ffmpeg` on the computer for those); it can still be downloaded.

**iPhone HEIC photos show a plain tile.** Run `.venv\Scripts\pip install pillow-heif`
(Windows) or `.venv/bin/pip install pillow-heif`, then restart.

**Port 8080 is busy.** Ninaivu Lite uses the next free port and prints it; or choose one
with `--port`.
