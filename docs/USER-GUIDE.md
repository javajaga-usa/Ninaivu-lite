# Ninaivu Lite — user guide

[தமிழில் படிக்க](USER-GUIDE.ta.md)

Ninaivu Lite shows your family's photos and videos to everyone at home, on any phone, tablet or
computer on the same Wi-Fi. It **only reads** your photo folders: it never moves, edits or
deletes a photograph.

## 1. Start it

1. Install **Python 3.10 or newer** from python.org. On Windows, tick *Add python.exe to PATH*.
2. Download Ninaivu Lite and unzip it somewhere permanent (for example `C:\Ninaivu-lite`).
3. Double-click **`start.cmd`** (Windows) or run **`./start.sh`** (macOS, Linux).
   The first start takes a minute or two and needs the internet once.
4. Your browser opens at `http://localhost:8080`. The window also prints an address such as
   `http://192.168.1.20:8080` — open that on phones and tablets.

Keep the window open while the family uses it. Closing it stops Ninaivu Lite.
To start it whenever the computer starts, run `tools\start-with-windows.cmd` once
(Windows; `tools\start-with-windows.cmd off` undoes it) or install
`tools/ninaivu-lite.service` (Linux).

## 2. The first day

1. The first visit asks you to **make the administrator**: your name, a username and a
   password of at least 8 characters. If you set this up from a phone, it also asks for the
   short **setup code** printed in the Ninaivu Lite window.
2. The admin console then helps you:
   - **Choose the photo folder** — browse to it and press *Use this folder*. You can add
     more folders later under *Library settings*.
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

Administrators sign in on the console, `http://<computer>:8080/admin`, with their username and
password. To leave the gallery, open your profile (top right) and choose *Switch profile*.

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

**Some videos show a plain tile.** Install `ffmpeg` for preview pictures. Videos the browser
cannot play can still be downloaded.

**iPhone HEIC photos show a plain tile.** Run `.venv\Scripts\pip install pillow-heif`
(Windows) or `.venv/bin/pip install pillow-heif`, then restart.

**Port 8080 is busy.** Ninaivu Lite uses the next free port and prints it; or choose one
with `--port`.
