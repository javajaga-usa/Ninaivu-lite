# Changelog

## Unreleased

- **A new look for the Control Panel.** The window opens on a dark band with the app's icon,
  its name and a status pill (a green dot for *Running*, grey for *Stopped*, a blue dot that
  pulses while it starts, stops or restarts). Buttons are rounded and carry icons: **Start**
  in green, **Stop** in soft red, **Open the family app** in blue. *Where to open it*,
  *Library* and *This computer* are rounded cards with an icon each, and *Library* and
  *This computer* sit side by side. Addresses can be clicked to open them, and each address
  and the setup code has a **Copy** button. *Library* shows the number of photos and videos
  indexed in large type above its folders. Messages appear in a light blue strip with an
  information icon. Every control and what it does is unchanged; the window is drawn with
  Tk alone, so it looks the same on Windows, macOS and Linux and in the portable copy.
- **No update section in the Control Panel.** A first install opened on an *Updating* card
  ("To update, run the newer setup file…" and **Get ready to update**), as if an update were
  under way. The card and its button are gone. There is still no update check: to update,
  press **Stop**, close the panel and run the newer setup file (the installers still offer
  to stop a running copy, and photos, people, settings and the index are kept). A server
  left running from before an update is still pointed out, with **Restart**.
- **A portable copy's Control Panel no longer shows the installed copy's server as its own.**
  With both on one computer, a portable copy whose data folder had not yet been named by a
  server of its own read the installed copy's server on port 8080 as *Running*, said
  "Started from its own window: stop it there.", and its **Start** answered that Ninaivu
  Lite was already running. A data folder with no name yet now counts no server as its own,
  so the portable copy starts its own server on the next free port.
- **A tidy portable zip.** Extracted, the portable copy's `Ninaivu Lite` folder now shows only
  **`Ninaivu Lite.exe`**, which opens the Control Panel, and, once it has run, its log
  `Ninaivu Lite.log` (older logs go to `data\logs`). The program (the private Python, the
  packages, `README-PORTABLE.txt`) is in an `app` folder, and the family's settings, index,
  previews and backups stay in the `data` folder as before. `Ninaivu Lite Control Panel.vbs`
  is gone. To move to this layout, extract the new zip to a new folder and copy the old
  `data` folder into its `Ninaivu Lite` folder, beside `Ninaivu Lite.exe`. The exe is not
  code-signed: unblock the zip before extracting it (*Properties → Unblock*), or Windows
  asks once (*More info → Run anyway*). For a forgotten password the command is now
  `app\Python\python.exe -m ninaivu_lite --data "<folder>\data" --reset-password NAME`.

## 1.10.0 — 2026-10-08

The index is unchanged (version 9), so 1.10.0, 1.9.0, 1.8.0 and 1.7.0 can open each other's
data folder: going back needs no backup restore. Stop Ninaivu Lite before installing 1.10.0
(in the Control Panel press **Stop**, or **Get ready to update**); the photos, people,
settings, index and backups are kept. The user guides' line on going back to an earlier
version, garbled in 1.9.0, is corrected.


- **A sharp top bar on iPhones (M15).** The console's top bar (the name, *Family app*, the
  language, search, theme, profile and sign-out buttons) could look soft and out of focus
  in Safari on a phone, because the bar's own words and icons were drawn into its
  frosted-glass blur layer. The blur and tint now sit on a layer behind the bar, in the
  console and the family app alike, so what is in the bar is drawn as sharply as the rest
  of the page; what scrolls behind it is still frosted.

Ninaivu Lite uses more of a computer with cores to spare, always leaving one core for the
gallery and never touching the photographs (parallel work review, A123-A127; the index is
unchanged):

- **Scan work follows the computer (A123).** Thumbnails, header reading and the face check
  run on every core but one, no longer at most three: an eight-core computer makes seven
  at a time, a Raspberry Pi still three, a one- or two-core computer still one. Each worker
  is given 128 MB and no more than half the memory is used, so a small computer gets fewer;
  very large pictures are still made one at a time.
- **Photos on a network drive, a USB stick or a hard disk are indexed faster (A124).** When
  reading a new file's details is slow (2 ms or more each), several are read at once: about
  2.5 times faster on a drive that answers in 2 to 5 ms. On a fast disk they are still read
  one by one, which is quicker there.
- **The face check for sideways photographs is about three times faster (A125)** on a
  four-core computer (with the optional OpenCV installed): 60 large photographs took 65 s
  and now take 23 s.
- **Imports, audits and copies to a drive read ahead (A126).** The next megabyte is read
  while the last one is checked and written, so the source, the processor and the
  destination are all kept busy; each file is still read once, in order. Importing from a
  fast drive was about 1.6 times faster in testing.
- **Video thumbnails are about twice as fast (A127).** ffmpeg now hands over a frame
  already shrunk (at most 1280 pixels across) rather than a full 4K picture, and when
  several videos are done at once each ffmpeg gets its share of the cores: 12 4K videos
  took 4.1 s and now take 2.2 s.

Jobs running at the same time get on better (A128-A129):

- **Background work gives way to the gallery (A128).** The scan, its workers, an import, a
  copy to a drive, a phone import and the daily backup run at a lower priority than the
  gallery (and on Windows, so does the ffmpeg a scan starts), so when the computer is busy
  the family's and guests' pages are served first. With the computer fully loaded by a
  scan and an import at once, gallery work took 16-17 ms instead of 19-21 ms.
- **An import no longer holds up thumbnails (A129).** An import into a library folder asked
  the library to look again every 500 files, and each look started the scan over from its
  walk. A resumed import steps over thousands of files a second, so the scan kept walking
  and made no thumbnails until the import ended. It now asks at most once a minute, and only
  when something new was copied, plus once at the end.

## 1.9.0 — 2026-10-08

The index is unchanged (version 9), so 1.9.0, 1.8.0 and 1.7.0 can open each other's data
folder: going back to 1.8.0 or 1.7.0 needs no backup restore. Stop Ninaivu Lite before
installing 1.9.0 (in the Control Panel press **Stop**, or **Get ready to update**); the
photos, people, settings, index and backups are kept.


Updates are put in by hand, and safely:

- **No update check.** The Control Panel no longer asks GitHub (or anywhere) whether a newer
  version is out: the *Check now* button, the daily-check box and the code behind them
  (`updates.py`) are gone, and the `update-check.json` they kept in the data folder is
  removed when the panel opens. Ninaivu Lite makes no request outside the house. Its
  *Updating* card says how to put a newer setup file in, and **Get ready to update** stops
  Ninaivu Lite and closes the panel, without opening any page.
- **A copy of the index before it is brought forward.** The first start after an update
  that changes the index keeps it, as the earlier version left it, in `backups` as
  `before-update-from-index-<n>-<time>.zip` (restorable with `--restore`), then carries it
  forward one step at a time as before. Copies taken before a change are now kept by when
  they were taken: by name, older "before" copies of one kind could push out newer ones of
  another.
- **Every installer says to stop Ninaivu Lite before updating.** The Windows installer, over
  a running copy, says it is best stopped first and offers to stop it (OK) or change nothing
  (Cancel); a silent install still stops and restarts it. The Linux installer says so and,
  if a copy it cannot stop is still running (started in a terminal), changes nothing rather
  than replacing its program under it. The Mac disk image carries a *Before updating - read
  me.txt* (English and Tamil), and the Control Panel says when the server still running is
  the version from before an update, with **Restart** to run the new one. The release notes,
  README, `README-PORTABLE.txt` and both user guides say the same; the photos, people,
  settings, index and backups are never touched by an update.
- **The Windows installer gives a stopping Ninaivu Lite a moment.** It waits up to 15
  seconds for the old program to let go of its files before asking to Retry, and a silent
  install (`/S`) that cannot replace them stops with an error instead of waiting for ever
  on a question nobody can answer.
- The Windows release build now also installs over its own running copy and checks that the
  data folder is untouched and Ninaivu Lite is started again.

Sudar:

- **Dragging Sudar's Compare divider no longer selects the picture on a desktop.** In Safari
  (and any browser on an iPhone or iPad) moving the divider also started a selection that
  painted the edited half blue; the press now belongs to the divider alone, and the
  Before/After bar's words and both pictures can no longer be selected or dragged out. (M14)

## 1.8.0 — 2026-10-08

The index is unchanged (version 9), so 1.8.0 and 1.7.0 can open each other's data folder:
going back to 1.7.0 needs no backup restore.

Fixes for the performance review of 2026-10-08 (A120-A122):

- **The first scan of a library is about 2.7 times faster on a computer with four cores**
  (a Raspberry Pi, most laptops): thumbnails are made three at a time, with one core always
  left for the gallery. A one- or two-core computer makes them one at a time as before, and
  a very large PNG or TIFF is always made alone so memory stays low. (A120)
- **The gallery opens faster when nothing has changed.** The library's counts, its years,
  folders and cameras, and the grid's first page are kept until the index changes, so
  opening the gallery again on 100,000 photos answers in milliseconds instead of a fifth of
  a second each. Anything that changes the index (a scan, a favourite, a visibility change)
  shows at once. (A121)
- **Phones no longer ask for a dozen scripts again on every visit.** Each page lists every
  script, and the Tamil translations, at an address that changes only with the file, so a
  phone keeps them until the next upgrade. (A122)

Phone layout fixes (M01-M13), checked on 360, 375, 390 and 412 px wide phones upright and on
their side, in English and Tamil:

- **The console's top bar fits a 390-440 px phone.** Sign out was off the right edge and the
  whole console scrolled sideways; the buttons take their own row there now. (M01)
- **Visibility no longer scrolls sideways on a phone**: the whole-library note wraps. (M02)
- **Dialogs fit a phone on its side.** The first-day walk-through's Back and Next, and the
  second choice when a drive is plugged in, were below the card; a dialog taller than the
  screen scrolls inside its card. (M03)
- **The sign-in, setup and "who's watching" screens scroll on a phone on its side**; the
  button was cut off with no way to reach it. (M13)
- **Sudar on a phone**: undo, redo, reset, the views and zoom have a row of their own instead
  of a strip cut off beside Close (M04); the looks, sliders and prompt are no longer hidden
  behind the Save and Download bar (M05); the format list and the download note are not cut
  off (M06).
- **The photo's details panel** no longer has its heading over the date and size. (M07)
- **Share**: the password field is full width and its hint is not cut off. (M08)
- **Search on a phone** shows what you type (only the last two letters showed), and its
  suggestions are as wide as the screen instead of the box. (M09, M10)
- **Overview, Library**: the folder path sits under its label instead of breaking after every
  few letters in a narrow column. (M11)
- The viewer's More menu keeps within the visible screen when the browser shows its bars. (M12)

## 1.7.0 — 2026-10-07

Fixes for the external attack surface review of 2026-10-07 (A116-A119):

- **Ninaivu Lite answers only the home network.** A request from an internet address, through
  a port forwarded on the router or a tunnel (ngrok, Cloudflare Tunnel, Tailscale Funnel), is
  refused with "Ninaivu Lite answers only the home network." The same network, private VPNs
  such as Tailscale and this computer are answered as before. `"allow_internet": true` in
  `settings.json` (or `NINAIVU_ALLOW_INTERNET=1`; Docker `ALLOW_INTERNET=1`) allows it on
  purpose. (A116)
- **A share link's password can be tried 60 times a day at most**, as a PIN can, not nearly a
  thousand. (A117)
- **Signing in on a browser ends the session it had before** (another profile), instead of
  leaving it valid for 30 days. (A118)
- **The health check tells other devices only that the server is up**; the version and data
  folder are told to this computer only. (A119)

Fixes for the 51 findings of the safety, flows and hand-offs audit of 2026-10-07 (A65-A115).
What you may notice:

- **Removing a library folder keeps what was decided for its photos.** Who sees what,
  favourites, albums and share links come back when the same folder is added again, and a
  copy of the index is taken just before (`backups/before-removing-folder-*.zip`). The
  console now asks before removing. (A65; `db.py`, index version 9)
- **Moved?** beside each library folder points it at its new place (a new drive letter, a new
  computer) and keeps everything. `--restore` lists restored folders that are not there. (A66)
- **A Hidden photograph cannot be shared or made a profile picture**, and an administrator's
  one-photo link stops when its photo is set to Hidden. Share pages no longer show the file
  name. (A78, A82, A86)
- **A Hidden photo renamed or moved on disk stays Hidden**, with its favourites, albums, links
  and rotation. (A79)
- **Restoring a backup signs everyone out and remakes the thumbnails**, so no photo is shown
  with another's thumbnail. An empty, foreign or damaged backup is refused with a reason, a
  bare `.db` backup from before 1.6.0 is accepted, and `--restore` refuses while any server
  holds the data folder. (A67-A69, A71, A74)
- **Daily backups keep 7 days, one a week for 4 weeks and one a month for 3 months.** The
  console lists them and says when the last one failed. (A73)
- **An import that loses its drive or fills its destination ends as "Not finished"**, not
  "Finished", and says how to finish it. Imported photos appear in the gallery as they are
  copied. Import messages are in Tamil too. (A87, A88, A98)
- **A copy to a pendrive is flushed and ends with "Eject the drive before you unplug it."**
  Reloading the console shows a copy or phone import that is still running. (A95, A96)
- **"Already running" now means a server on the same data folder.** A second copy (installed
  beside a portable one) takes the next free port instead of opening the other library, and
  two servers on one data folder are refused. (A103, A105)
- **Start at sign-in works with non-English folder names on Windows** (the script is written
  in UTF-16). (A104)
- **Upgrades start Ninaivu Lite again** if it was running, and a failed Linux upgrade puts the
  old copy back. Uninstallers say what was kept. (A110, A115)
- **The Control Panel asks before stopping an import or a copy**, and says why a start failed.
  (A70, A111)
- A folder named on the command line or in the Linux service is no longer added back after the
  administrator removed it. (A107)

Going back to 1.6.0 after this version means restoring a backup: 1.6.0 refuses the newer index.

## 1.6.0 — 2026-10-06

Fixes for all ten findings of the project audit of 2026-10-05 (A01-A10). Three of them change
what you may notice:

- On a computer without ffmpeg (the installers do not include it), guests and share links are
  told a video cannot be shared, rather than being sent a file that may say where it was
  shot. Settings has a new switch to send such videos as they are.
- Installed as root on Linux, Ninaivu Lite now runs as its own `ninaivu-lite` account. The
  installer names any photo folder that account cannot read, with the command to allow it.
- The daily backups in `backups/` are now zips holding the index, the settings and profile
  pictures, the same as the download.

- **A share link ends with what it points at.** Removing a photograph from the index (for
  example when its library folder is taken out) or deleting an album now deletes its share
  links. Before, the index could hand the same id to a different photograph or album, and an
  old link would then show something nobody shared. Links already pointing at nothing are
  dropped on upgrade. (`db.py`, index version 7)
- **Damaged or missing settings no longer empty the library.** When `settings.json` is
  missing, unreadable, not valid JSON, or has no folder list, the library folders are taken
  back from the index and the settings are written out again, instead of starting with no
  folders and letting the next scan delete every favourite, album and visibility choice. The
  damaged file is still kept as `settings.json.damaged` (older ones renamed by date). (`config.py`)
- **Import, run again, checks what was done before.** A file is stepped over only if it is
  still the same file (size and modified time) and its archived copy is still there. A
  changed source is imported again beside the earlier copy; a lost copy is copied again.
  The phone import deletes a fetched copy only on the same check. (`importer.py`, `phones.py`)
- **A duplicate needs a real archived copy.** A new file is left out as a duplicate only
  when the archive holds a copy that is on disk now with the same bytes, read from the disk
  rather than taken from the stored hash. (`importer.py`)
- **A video is never sent to a guest or a share link with its location by accident.** When
  ffmpeg is missing or cannot remove a video's metadata, a guest or someone with a link is
  now told the video cannot be shared, instead of receiving the original file (which can say
  where it was shot). An administrator can choose otherwise in Settings (*Send videos to
  guests and share links as they are when their location cannot be removed*); such a file
  then carries `X-Ninaivu-Metadata: original`. The family's own view is unchanged.
  (`api_gallery.py`, `config.py`, `static/js/admin.js`)
- **Every pending thumbnail is made in one pass.** The thumbnail queue moved on by an offset
  while finished rows left it, so a large scan skipped work (70 of 120 in the audit's
  reproduction). It now continues after the last row it tried. (`scanner.py`)
- **Backups hold everything a restore needs.** The daily copy in `backups/` is now the same
  zip as the download, and both carry the index, `settings.json` and people's profile
  pictures (`avatars/`). Older index-only daily copies are pruned with the new ones.
  (`backups.py`)
- **A folder that cannot be read is not a folder emptied.** Photographs under a subfolder
  the scan could not read (permissions, a flaky disk) keep their place instead of being
  marked missing, and the console says some folders could not be read. (`scanner.py`)
- **The Linux system service no longer runs as root.** Installed as root, Ninaivu Lite now
  runs as its own `ninaivu-lite` account that owns `/var/lib/ninaivu-lite`, with systemd's
  privilege restrictions on, and the installer names any photo folder that account cannot
  read. Where no account can be made, no system service is set up. (`installers/linux/install.sh`)
- **A release runs the tests first.** The release workflow runs the whole test suite on
  Windows, macOS and Linux for the exact commit it publishes, for a tag and for "Run
  workflow" alike, and publishes nothing unless it passes. (`.github/workflows/`)


Fixes for the critical and the eight high findings of the project audit of 2026-10-06
(A11-A19). Three of them change what you may notice:

- Ninaivu Lite now answers only to this computer's own addresses and network name. If you
  reach it through a name of your own (a reverse proxy, a name set in your router), add that
  name to `allowed_hosts` in `settings.json` (or, with Docker, to `ALLOWED_HOSTS`).
- A backup is put back with `ninaivu-lite --restore <zip>` (with Ninaivu Lite stopped).
- If the settings are missing and the index cannot be read either, Ninaivu Lite now refuses
  to start and says why, instead of starting with no library folders.

- **A retried import never writes over an older photo.** After a failed attempt at a file
  (a read error on a card, say), the next run could put the new file in place of the
  archived copy of whatever had been at that path before, such as a 2019 photo replaced by a
  2024 one from a reused card. Only a copy that the audit (Verify) found damaged is now
  replaced; anything else goes beside the earlier copy. (`importer.py`)
- **A web page cannot borrow this computer's address.** A page on the internet could point a
  name it owns at the home computer (DNS rebinding) and then read Family photos through any
  profile without a PIN, or, before setup, become the administrator. Requests that arrive
  under any other name than this computer's addresses, its network name or `localhost` are
  refused. (`app.py`, `config.py`)
- **Restoring a backup puts back the backup.** The new `--restore` command checks the zip
  (a whole index, from this version or an older one), moves what is there aside into a
  `before-restore-…` folder instead of deleting it, and removes the index's `-wal` and
  `-shm` files, which SQLite would otherwise replay over the restored index and so bring back
  what was being undone. It will not run while Ninaivu Lite is running. The note inside each
  backup says how, by command or by hand. (`backups.py`, `__main__.py`)
- **An index that cannot be read no longer empties the library.** The 1.6.0 recovery of
  missing settings read the index through a `file:` address, which a data folder on a network
  share cannot have; failing that, it went on with no folders, and the first scan removed
  them all. The index is now read by its path, and if it still cannot be read, nothing is
  saved or scanned and the server does not start. (`config.py`, `__main__.py`)
- **The Linux uninstaller removes only what it installed.** Installed with `--prefix /opt` or
  a shared folder, `uninstall` used to delete that whole folder. It now removes its own
  files and then the folder only if it is empty. (`installers/linux/install.sh`)
- **The Windows uninstaller stops Ninaivu Lite and removes start-at-sign-in.** It asked the
  program to do this after the program's packages were already removed, so a running server
  was left running and the sign-in shortcut stayed behind, showing an error at every sign-in.
  This now happens first, and the shortcut is also removed by name.
  (`installers/windows/ninaivu-lite.nsi`)
- **A release is always a new version.** Before anything is built or sent for signing, the
  release workflow checks that the version is not already tagged or released, that a tag
  matches `version.py`, and that this changelog has a section for it. "Run workflow" with an
  unchanged version used to rebuild and replace the last release's files.
  (`.github/workflows/release.yml`, `tools/check-release-version.sh`)
- **An upgrade can no longer leave the app stuck on "Opening your library".** The offline
  worker kept serving the old copies of the app's scripts after an upgrade, so a new `app.js`
  could meet an old `api.js` and never start. The worker's version now follows the app's
  files, and scripts come from the server whenever it answers. (`static/sw.js`, `pages.py`)
- **The first-day guide fits in Tamil.** On the import step, the longer Tamil text spilled
  over the footer and covered *Start the import*. The step now scrolls inside its card.
  (`static/css/admin.css`)

Fixes for the remaining 45 findings of the project audit of 2026-10-06 (A20-A64). What you may
notice:

- A PIN set or removed by an administrator signs that person out everywhere, and someone
  signed in with a password an administrator set can look but change nothing until they
  choose their own.
- Guests no longer see camera models. While two videos are being prepared for guests or
  share links, a third is asked to try again in a moment.
- On Linux and macOS the data folder is readable only by its owner. A system-wide (root)
  Linux install has no Control Panel; it is run with `systemctl`, and its `ninaivu-lite`
  command always runs as the service's account.
- Choosing Export (or a Windows phone) from a drive prompt link now asks first.
- Starting `ninaivu-lite <folder>` while Ninaivu Lite is running refuses and says to add
  the folder on the Admin page. An index made by a newer version is refused at start.
- A browser too old to run Ninaivu says so, in English and Tamil, instead of loading forever.
- "Run workflow" publishes a release only from main.

### Server and security

- **A new photograph never shows an old one's thumbnail.** When the index hands a removed
  photograph's id to a new one, the thumbnail on disk is served only once the index says it
  belongs to the new photograph. Before, a guest could see a removed Hidden photograph in its
  place. (`api_gallery.py`)
- **Undo stays with its own folder.** A library folder's visibility history is removed with
  the folder, so undoing an old change can no longer put a removed folder's rules on the next
  folder added. History left from folders already removed is dropped on upgrade.
  (`db.py`, index version 8)
- **Wrong guesses no longer lock the owner out.** The username sign-in and the profile picker
  count against one limit per account, a day's limit (60 tries) keeps a 4-digit PIN out of
  reach, nobody on the network can use the limit to keep this computer itself out, and failed
  sign-ins are written to the log. (`api_auth.py`)
- **A drive link asks before it copies.** A console link with `?drive=…&do=export` now shows
  the drive question instead of starting to copy the library onto that drive.
  (`static/js/drives.js`)
- **Guests' videos can't tie up the server.** Only two videos are prepared for guests and
  share links at once, each video once, and its temporary file can no longer be deleted by a
  second request. The viewing copies in `views/` are kept under 2 GB.
  (`api_gallery.py`, `media.py`)
- **Camera models are for the family.** Guests no longer see camera names in the filters,
  suggestions or search. (`api_gallery.py`)
- **Changing a PIN signs that person out, and a temporary password must be replaced.** The
  server refuses changes while a password set by an administrator is still in use. The
  Control Panel opens an update link only if it is on GitHub. (`auth.py`, `app.py`,
  `panel.py`)
- **The console points out Family profiles without a PIN.** With open browsing off,
  Settings names any Family profile anyone on the home network can still tap into.
  (`api_admin.py`, `static/js/admin.js`)
- **Writes from another page are refused, even with no origin.** A signed-in change that
  doesn't say which page sent it (or says "null") must be JSON or carry the `X-Ninaivu`
  header, which only this site's own scripts send. (`app.py`)

### Library, import and drives

- **The data folder is private on Linux and macOS.** It is created readable only by its
  owner (an existing one is tightened), and the index is readable only by its owner. The
  Linux installer makes it 0700 too, owned by the service account on a system-wide install, and
  the service writes with `UMask=0027`. (`config.py`, `db.py`, `installers/linux/install.sh`)
- **A changed file whose copy was stopped is copied next time.** Its new size and time are
  saved only with the copy's result. (`importer.py`)
- **A phone copy that may be cut short is never taken as imported (Windows).** A copy that
  does not finish in 15 minutes is deleted and fetched again next time, a file whose size the
  phone does not report is never marked as done, and the result says how many did not
  finish. (`phones.py`)
- **The Linux system service sees the desktop's drives and phones.** Installed as root, it
  now looks in every user's `/media` and `/run/media` folder and phone folders (gvfs), where
  their permissions let it. (`drives.py`)
- **A photo and a video of the same moment are filed on the same day.** A video's UTC time
  (and a Google Takeout time) is read on the clock of the nearest photo in the same folder
  that records its time zone. (`dates.py`, `media.py`, `importer.py`)
- **One bad file no longer stops the scan.** An error reading one file is logged against it
  and the rest of the library is still indexed. (`scanner.py`, `media.py`, `dates.py`,
  `takeout.py`)
- **A linked folder on an unplugged drive keeps its photos.** They are treated as unreadable,
  not deleted. (`scanner.py`)
- **Folders named while Ninaivu Lite is running are not lost.** The command now says to add
  the folder on the Admin page and changes nothing, instead of saving a list the server then
  wrote over. (`__main__.py`)
- **Copying to a pendrive keeps every file.** On FAT and exFAT drives, names that differ only
  in case get separate names instead of overwriting each other, and folders or files that
  cannot be read are counted as errors instead of being skipped silently. (`drives.py`)
- **A dry run keeps the record of earlier imports.** (`importer.py`)
- **Scanning and importing survive a busy index.** The scanner tries again a minute later if
  the index cannot be opened, an import that cannot open it says why, and a passing lock no
  longer switches the index out of WAL mode. (`scanner.py`, `importer.py`, `db.py`)
- **Index updates are safe with two programs open at once, and a newer index is refused.**
  Each update takes the write lock first. Every damaged `settings.json` is now kept, older
  copies renamed by date. (`db.py`, `config.py`, `__main__.py`)

### Web pages

- **The viewer forgets the last person.** When the person viewing changes, the viewer closes
  and is cleared with the gallery, and shortcuts do nothing behind the sign-in screen.
  (`static/js/app.js`)
- **Old browsers are told, and older iPads stay readable.** A browser too old to run Ninaivu
  says so instead of loading forever; tinted backgrounds fall back to plain colours, so the
  sign-in screen is no longer see-through and the home's name no longer invisible on iOS 15;
  the share page works on iOS 14. (`templates/`, `static/css/`, `static/js/share.js`)
- **A refused video says why.** The share page and the guest viewer show the server's reason
  instead of "This media could not be displayed." (`static/js/api.js`)
- **Sudar keeps full size and format.** HEIC and TIFF are edited from a full-size copy, a
  JPEG comes back as a JPEG, and a failed edit is reported once instead of being run again on
  the page. (`api_sudar.py`, `static/js/sudar/`)
- **A long gallery doesn't stop at one failed page.** It tries again after a growing pause
  and shows a *Try again* row; a failed status check says the server could not be reached.
  (`static/js/app.js`)
- **More in Tamil.** The server's error messages, the iPhone install banner, the share page's
  errors, the offline page and a few console labels left in English. (`static/i18n/ta.json`,
  `static/sw.js`)
- **Dialogs act like dialogs.** The page behind them can't be reached with Tab or a screen
  reader, focus moves in when one opens and back when it closes. (`static/js/dialogs.js`)
- **Every sentence is in both languages.** `en.json` has the 32 keys it was missing, and a
  new test checks every key, its placeholders and every server message's Tamil.
  (`tests/test_language.py`)

### Installers, releases and tests

- **A system-wide Linux install is run with `systemctl`.** It no longer installs a Control
  Panel that could not work, its command always runs as the service's account even with
  `--no-service`, and the advice for unreadable photo folders covers the folders above them
  and photos added later. (`installers/linux/install.sh`)
- **Bundled Python is checked.** The Linux and macOS builds check python-build-standalone
  against hashes in `installers/PYTHON_SHA256SUMS`, cached copies included; the Windows build
  checks the embedded Python is signed by the Python Software Foundation; pynsist and NSIS
  are pinned.
- **The release workflow's third-party actions are pinned to exact commits.**
- **"Run workflow" publishes only from main.** On another branch it only builds and tests the
  installers.
- **Sign-in is tested over HTTP.** Passwords, PINs, their limits, the console's
  administrators-only check and password changes. (`tests/test_auth_api.py`)
- **The setup code is in the Control Panel and the log.** So it can be found when Ninaivu Lite
  was started from the Control Panel or as a service. (`__main__.py`, `control.py`,
  `panel.py`)
- **A release fails if an installer is missing,** installers are sent for signing only after
  the tests pass, and the notes say the Mac app is notarised only when it was signed.
- **Upgrading on Linux restarts the service on the new version.**
- **Windows installs and upgrades are gentler.** An upgrade remembers that start-at-sign-in
  was off, installing into a folder that holds other programs no longer deletes its Python
  or bin folders, and the installed, portable and from-source copies each get their own
  sign-in file. (`installers/windows/`)
- **Every release runs what it ships:** the Mac app from its disk image, the Raspberry Pi
  installer on an arm64 machine, a root install as a service with an upgrade, and the Docker
  image.
- **ruff and the test Python are pinned.**

### Documentation

- The README, user guides (English and Tamil) and installer notes now cover the portable
  zip's data and upgrades, a Linux install with `sudo` (its data folder, service account,
  `setfacl` and firewall), restoring a backup, resetting a password for each kind of install,
  where to find the setup code, video sharing without ffmpeg, and the names Ninaivu Lite
  answers to (`allowed_hosts`).
- Statements that no longer matched are corrected (releases are unsigned until the SignPath
  application is approved, the Windows installer may ask for administrator permission, where
  Lite writes files, when CI runs), and the guides use the exact on-screen labels in both
  languages.

## 1.5.1 — 2026-10-04

- **A portable Windows zip, nothing to install.** Each release also carries
  `Ninaivu-Lite-<version>-windows-x64-portable.zip`: the same program as the installer, in one
  folder that runs from wherever it is extracted (a folder on the computer, or a pendrive).
  Double-click *Ninaivu Lite Control Panel.vbs*; settings, people, the index and previews stay
  in a `data` folder beside it, and nothing goes into the Start menu, the registry or AppData.
  A note in English and Tamil inside says how to unblock, move, upgrade and remove it. Like the
  installer it is not code-signed; the release workflow extracts it to a path with a space and
  Tamil letters, opens the Control Panel through the launcher, and starts and stops it there.
  (`installers/windows/portable/`, `.github/workflows/release.yml`)

## 1.5.0 — 2026-10-03

Also carries everything in 1.4.2, which was not released on its own.

- **A pendrive or an external drive plugged in is asked about.** The console, and the Control
  Panel when it is open, ask in Tamil and English: *Import media from this drive* (the Import
  page opens with the drive as its source), *Export media to this drive* (the library's photos
  and videos are copied into a `Ninaivu Lite` folder on it, only what is new, never
  overwriting), or *Not now*. Found with the standard library alone: on Windows, removable
  drives and USB, SD and FireWire hard drives (the computer's own disks are never asked about);
  on macOS `/Volumes`; on Linux `/media` and `/run/media`. (`drives.py`, `api_drives.py`,
  `static/js/drives.js`)
- **A phone on a USB cable is asked about too**, with *Import media from this phone* (copying
  the library onto a phone is not offered). On Windows a phone has no drive letter (MTP), so
  it is found through the Windows shell, with PowerShell asked only when the set of portable
  devices changes; Import fetches the camera folders (DCIM, Pictures, Movies) over the cable,
  runs them through the ordinary Import, and deletes the temporary copies that arrived safely.
  Next time only new photos cross the cable. On Linux a phone the desktop has opened
  (`/run/user/<uid>/gvfs/mtp:…`) goes straight to the Import page. Not on macOS, which has no
  built-in way to read an Android phone as files. (`phones.py`)

## 1.4.2 — 2026-10-02

A sweep for faults, by review and by driving every screen in a browser.

- **Windows installer shows the logo.** `ninaivu-lite.ico` held PNG-compressed images, which
  the installer builder (NSIS) cannot read, so the installer, the uninstaller and the
  Control Panel's icon came out blank. The icon is now plain bitmaps at every size, and a
  test keeps it so.
- **Windows upgrade starts clean.** The installer now removes the previous program (the private
  Python, the packages, the commands) before writing the new files, so nothing of an old
  version is left among them. Only the installation goes: the family's data
  (`%LOCALAPPDATA%\Ninaivu-lite`: people, settings, index, previews) and the photographs are
  not touched.
- **Server, tightened.** Request bodies are capped at 100 MB before they are read and JSON at
  1 MB; idle connections are dropped after a minute; a Permissions-Policy header denies the
  camera, microphone, location and payment. A flood of failed sign-ins under made-up names
  no longer wipes the lockouts protecting the administrator's password and the family's PINs.
- **The scan no longer spins.** A photograph whose drive went away between the thumbnail
  passes used to keep the finishing pass at full CPU until Rescan; each row is now tried once
  per pass. A file that is merely away keeps its place in the queue instead of being written
  off without a thumbnail, and one that comes back is asked for again. Two threads making the
  same thumbnail no longer share a temporary file.
- **Shared and guest copies the right way up.** A share link or a guest preview of a turned
  photograph came out sideways; the turn is baked in now.
- **A second dry run agrees with the first** instead of planning every file under a `_1` name.
- **Phones can select.** A long press on a tile starts selecting (the only way in once
  *Select all* waits for selection mode); a plain tap then adds. The viewer keeps its arrows
  for videos on touch screens, where a swipe would start on the player; the date rail, a hover
  affordance, is hidden there.
- **The viewer forgets stale copies** after a bulk favourite or visibility change, so a photo
  reopened shows the change. The gallery stops polling behind the sign-in gate. The console's
  Import page is ready before the first refresh.
- **Tamil everywhere.** Three dozen messages were English on a Tamil page (selection counts,
  album dialogs, download and visibility toasts, the offline banner, the viewer's rotation
  note, scan progress); they are translated now.
- Removing a library folder now says it also forgets that folder's favourites, album places
  and per-photo visibility.

## 1.4.1 — 2026-10-02

- **The earlier look is back.** 1.4.0's finish (rounder tiles, the count pill, gradient
  buttons, glass toasts, the larger filmstrip, lifting sign-in tiles) is removed; the fixes
  stay: the badges' corners, the viewer and phone changes, the Favourites count and the
  lighter loading.

## 1.4.0 — 2026-10-02

- **Lighter over the wire.** The page, scripts, styles, Tamil strings and large API answers
  are sent gzipped (a fifth of their size); static files whose address carries their hash are
  cached for a year; and English, whose strings are the keys themselves, no longer fetches a
  quarter-megabyte of them. A phone's first load is about a megabyte lighter.
- **The viewer, tidied.** The everyday five tools stay on the bar and the rest sit behind ⋮
  with their names, at every width. On a phone the header stays on one line, the round arrows
  make way for swiping, and a toast no longer sits on the filmstrip.
- **The grid, on a phone.** A row of years above the photographs, one tap each, where there
  is no sidebar; *Select all* appears only once a long press starts selecting.
- **Finish.** Rounder tiles with a hairline edge, the day's count in a quiet pill, gradient
  primary buttons, glass toasts, a larger ringed filmstrip, and sign-in tiles that lift. A
  video's sign and the selection mark each have a corner of their own now.

## 1.3.10 — 2026-10-02

- **The Favourites count follows your favourites.** The sidebar's number only changed on a
  reload: after a favourite, the quick status refresh left the counts out. They are asked for
  again now, so the number moves as soon as a photograph is favourited or unfavourited.

## 1.3.9 — 2026-10-02

- **The update check asks only when you do.** The Control Panel no longer asks GitHub by
  itself: *Check now* asks once, and the daily check runs only after you tick *Tell me when a
  new version is available*, which now starts unticked. The privacy statement in the code
  signing policy says so, in place of "no update checks", which stopped being true in 1.3.4.

## 1.3.8 — 2026-10-02

- **The panel's tick boxes match their text.** 1.3.7 made them a whole line tall, too big on a
  sharp screen; they are now the font's own size, the height of its capitals, at any density.
- **"Sign out", with the door symbol, everywhere.** The gallery's menu item and the profile
  sheet's button said *Switch profile* beside a sign-out symbol; both now say *Sign out*, as the
  console does.

## 1.3.7 — 2026-10-02

- **Upgrading on Windows no longer fails with a write error.** Windows cannot replace a
  program that is in use, and a running Ninaivu Lite or an open Control Panel kept the old
  one in use. The installer now asks the server to stop and then, while anything is still in
  use, asks you to press *Stop* and close the Control Panel, with *Retry*, instead of failing
  half-way. The uninstaller does the same. The panel says so in the update line itself, and
  *Download* offers to stop Ninaivu Lite and close the panel for you before you run the
  installer.
- **A replaced profile picture is a new file**, never written over the old one, which a
  browser may still be reading; that failed on Windows in 1.3.6.
- **The panel's tick boxes are as tall as their text.** They were drawn a fixed few pixels,
  tiny on a sharp screen; now they follow the font, with a white tick on blue when on.

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
