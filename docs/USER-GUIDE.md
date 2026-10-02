# Ninaivu Lite — user guide

[தமிழில் படிக்க](USER-GUIDE.ta.md)

Ninaivu Lite shows your family's photos and videos to everyone at home, on any phone, tablet or
computer on the same Wi-Fi. It **only reads** your photo folders: it never moves, edits or
deletes a photograph. (The importer copies photos *into* the library from old drives, and
Sudar can save an edited *copy* beside an original; neither changes a photograph you have.)

## 1. Start it

**With an installer** (from the releases page): run it. On Windows the Control Panel opens when
it finishes — press **Start**, then **Open the family app**. Nothing else is needed; skip to
*The Control Panel* below.

**Without an installer:**

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
a screen, use `tools/ninaivu-lite.service` instead.

## 2. The first day

1. The first visit asks you to **make the administrator**: your name, a username and a
   password of at least 8 characters. If you set this up from a phone, it also asks for the
   short **setup code** printed in the Ninaivu Lite window.
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

- **Timeline** — newest first; drag the date bar on the right to jump to a year.
- **Folders** — the left menu lists folders with their counts.
- **Search** — type a file name, a folder, a year or month, or a camera name.
- **Viewer** — tap a photo. Swipe or use ← → to move, Esc to close. ♡ adds it to your
  favourites; ⓘ shows the date, size and camera; ⬇ downloads the original.
- **Select** — *Select all* on a day, or tick photos, to add them to an album or download them
  together.
- **Albums** — make an album from a selection; albums can be shared.
- **Language** — English or தமிழ், from the language button at the top. Each person's choice
  is remembered.
- **Sudar** (⋯ → *Edit with Sudar*) — the photo studio, in the browser: light, colour, detail
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

## 6. Sharing with someone outside the family

From the viewer or an album, choose **Share**. You can add a password and an expiry date.
Send the link to someone on your home network. They see only that photo or album, as a copy
without location or camera details. Your list of links is in your profile; you can turn a link
off at any time.

> Links work only on your home network. Ninaivu Lite is not meant to be reached from the internet.

## 7. Looking after it

- **Backups** — every day Ninaivu Lite keeps a copy of its index (people, albums,
  favourites, visibility) in its data folder and keeps the last seven. *Settings → Download a
  backup* gives you one to keep elsewhere. Your photos are your own files: back them up as you
  always do.
- **Data folder** — `%LOCALAPPDATA%\Ninaivu-lite` on Windows, `~/Library/Application
  Support/Ninaivu-lite` on macOS, `~/.local/share/ninaivu-lite` on Linux. It holds settings,
  the index, previews and logs, never your photos.
- **Forgot the admin password?** Close Ninaivu Lite, then on that computer run
  `python -m ninaivu_lite --reset-password <username>` (inside the folder, after
  `.venv\Scripts\activate` on Windows or `source .venv/bin/activate` elsewhere).
- **A drive unplugged?** Its photos show as unavailable and come back when it returns.
  Nothing is lost.
- **Updating** — replace the program files with the new version and start it again. Your
  data folder stays as it is.

## 8. Moving up to Ninaivu (optional)

If you later want faces, search by description or cloud backup, the full Ninaivu can take
over your library. Run `python -m ninaivu_lite --export lite-export.json` and give that file to
Ninaivu. See [UPGRADE.md](UPGRADE.md).

## 9. Common questions

**The phone can't open the address.** Use the address printed in the window (not
`localhost`), make sure the phone is on the same Wi-Fi, and allow Python through the Windows
firewall when asked.

**Some videos show a plain tile.** A video gets its preview picture the first time a family
member or administrator scrolls past it or plays it: their browser draws one frame and
Ninaivu keeps it for everyone. A video the browser cannot play keeps the plain tile (install
`ffmpeg` on the computer for those); it can still be downloaded.

**iPhone HEIC photos show a plain tile.** Run `.venv\Scripts\pip install pillow-heif`
(Windows) or `.venv/bin/pip install pillow-heif`, then restart.

**Port 8080 is busy.** Ninaivu Lite uses the next free port and prints it; or choose one
with `--port`.
