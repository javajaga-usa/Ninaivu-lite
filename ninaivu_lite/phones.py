"""Phones on a USB cable, on Windows.

A phone set to *File transfer* (Android) or an iPhone that has been trusted
shows in Explorer under This PC, but it has no drive letter: Windows talks to
it over MTP, which ordinary file reading cannot reach. The Windows shell can,
and PowerShell, which every Windows 10 has, can drive the shell. So:

    listed()         the phones under This PC now. PowerShell is asked only when
                     the set of portable devices changes (one cheap native call
                     says so), never on every poll.
    PhoneImport      the phone's camera folders (DCIM, Pictures, Movies) copied
                     to a folder in Ninaivu Lite's data folder, then that folder
                     run through the ordinary Import into the archive, then the
                     copies that arrived safely deleted. A list of what was
                     imported is kept per phone, so next time only new photos
                     come across the cable.

Nothing on the phone is changed. Copying the library onto a phone is not
offered: a phone's storage is small, and over MTP Windows cannot tell what
is already there without reading it all back.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any

from . import parallel
from .drives import Drive, _drive_id, _say, is_within

log = logging.getLogger(__name__)

#: Portable devices (GUID_DEVINTERFACE_WPD): phones, cameras, and USB sticks too.
WPD_INTERFACE = "6ac27878-a6fa-4155-ba85-f98f491d4f33"
PHONE_FOLDERS = ("DCIM", "Pictures", "Movies")
MIRROR_DIR = "phone copies"
CREATE_NO_WINDOW = 0x08000000

LIST_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$shell = New-Object -ComObject Shell.Application
$out = @()
foreach ($i in $shell.NameSpace(17).Items()) {
  if ($i.IsFolder -and -not $i.IsFileSystem) {
    $out += [pscustomobject]@{ name = $i.Name; path = $i.Path }
  }
}
ConvertTo-Json -Compress -InputObject @($out)
"""

#: Prints one JSON object per line: {"total": n}, {"done": i}, {"finished": true},
#: or {"error": "gone" | "locked"}. Never a file name, so nothing needs escaping.
#: A copy that did not finish in time is deleted, said as {"done": i,
#: "failed": 1}, and its path written to NL_FAILED (the shell may still be
#: writing it, so it is deleted again before the import). A file whose size
#: the phone does not tell is taken when it stops growing, but cannot be known
#: to be whole: its path goes to NL_UNSURE, so it is never put on the list of
#: what came in, and is fetched again next time.
FETCH_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$shell = New-Object -ComObject Shell.Application
$phone = $null
foreach ($i in $shell.NameSpace(17).Items()) { if ($i.Path -eq $env:NL_PHONE) { $phone = $i } }
if (-not $phone) { '{"error":"gone"}'; exit }
$exts = $env:NL_EXTS.Split('|')
$tops = $env:NL_TOPS.Split('|')
$skip = @{}
if (Test-Path -LiteralPath $env:NL_SKIP) {
  foreach ($line in [IO.File]::ReadAllLines($env:NL_SKIP)) { $skip[$line] = 1 }
}
$todo = New-Object System.Collections.ArrayList
function NameOf($item) {
  $n = $item.ExtendedProperty('System.FileName')
  if (-not $n) { $n = $item.Name }
  return ($n -replace '[<>:"/\\|?*]', '_')
}
function Walk($folder, $rel) {
  foreach ($it in $folder.Items()) {
    $name = NameOf $it
    if ($it.IsFolder) { Walk $it.GetFolder "$rel\$name" }
    elseif ($exts -contains [IO.Path]::GetExtension($name).ToLower()) {
      [void]$todo.Add(@($it, $rel, $name))
    }
  }
}
$storages = @($phone.GetFolder.Items())
if ($storages.Count -eq 0) { '{"error":"locked"}'; exit }
foreach ($st in $storages) {
  if (-not $st.IsFolder) { continue }
  foreach ($top in $st.GetFolder.Items()) {
    if ($top.IsFolder -and ($tops -contains $top.Name)) { Walk $top.GetFolder "$(NameOf $st)\$($top.Name)" }
  }
}
"{""total"":$($todo.Count)}"
$n = 0
foreach ($t in $todo) {
  $it = $t[0]; $rel = $t[1]; $name = $t[2]; $n++
  $size = [int64]$it.Size
  $local = Join-Path $env:NL_DEST $rel
  $file = Join-Path $local $name
  $have = Test-Path -LiteralPath $file
  if ($skip.ContainsKey("$rel\$name") -or ($have -and $size -gt 0 -and (Get-Item -LiteralPath $file).Length -eq $size)) {
    "{""done"":$n,""skipped"":1}"; continue
  }
  # A copy left from before, of a size nobody can check, is fetched again.
  if ($have) { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
  [void][IO.Directory]::CreateDirectory($local)
  # 4 no progress box, 16 yes to all, 512 no folder question, 1024 no error box
  $shell.NameSpace($local).CopyHere($it, 1556)
  $deadline = (Get-Date).AddMinutes(15); $last = -1; $still = 0; $ok = $false
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 200
    if (-not (Test-Path -LiteralPath $file)) { continue }
    $len = (Get-Item -LiteralPath $file).Length
    if ($size -gt 0) { if ($len -eq $size) { $ok = $true; break } }
    elseif ($len -gt 0 -and $len -eq $last) { $still++; if ($still -ge 5) { $ok = $true; break } }
    else { $still = 0 }
    $last = $len
  }
  if (-not $ok) {
    Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    [IO.File]::AppendAllText($env:NL_FAILED, "$rel\$name`n", [Text.Encoding]::UTF8)
    "{""done"":$n,""failed"":1}"; continue
  }
  if ($size -le 0) { [IO.File]::AppendAllText($env:NL_UNSURE, "$rel\$name`n", [Text.Encoding]::UTF8) }
  "{""done"":$n}"
}
'{"finished":true}'
"""


def _powershell(script: str, env: dict[str, str] | None = None, **popen: Any):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})},
                            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                            **popen)


# --- which phones are plugged in ----------------------------------------------------------


def wpd_devices() -> tuple[str, ...]:
    """The portable devices present: one quick native call, so the shell is
    asked about phones only when this changes."""
    import ctypes
    import uuid

    cfgmgr = ctypes.WinDLL("cfgmgr32")
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(WPD_INTERFACE).bytes_le)
    size = ctypes.c_ulong(0)
    if cfgmgr.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(guid),
                                                 None, 0) != 0 or size.value <= 1:
        return ()
    buffer = ctypes.create_unicode_buffer(size.value)
    if cfgmgr.CM_Get_Device_Interface_ListW(ctypes.byref(guid), None, buffer, size.value, 0) != 0:
        return ()
    return tuple(sorted(p for p in buffer[:size.value].split("\0") if p))


_cache: dict[str, Any] = {"key": None, "phones": []}
_cache_lock = threading.Lock()


def _ask_shell() -> list[Drive]:
    process = _powershell(LIST_SCRIPT)
    try:
        out, _ = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        return []
    try:
        rows = json.loads(out.decode("utf-8", "replace").strip() or "[]")
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [Drive(_drive_id(r["path"], r["name"], "phone", 0), r["path"], r["name"], 0, 0,
                  kind="phone", shell=True)
            for r in rows if isinstance(r, dict) and r.get("path") and r.get("name")]


def listed() -> list[Drive]:
    """The phones under This PC; [] when they cannot be read."""
    try:
        key = wpd_devices()
    except Exception:  # noqa: BLE001
        log.exception("could not list the portable devices")
        return []
    with _cache_lock:
        if key == _cache["key"]:
            return list(_cache["phones"])
    phones = _ask_shell() if key else []
    with _cache_lock:
        _cache.update(key=key, phones=phones)
    return list(phones)


# --- bringing a phone's photos in ---------------------------------------------------------


def _name(drive: Drive) -> str:
    safe = re.sub(r"[^\w .-]", "_", drive.label).strip(" .") or "Phone"
    tag = hashlib.sha1(drive.path.encode("utf-8", "replace")).hexdigest()[:6]
    return f"{safe} {tag}"


def mirror_for(data_dir: str, drive: Drive) -> str:
    """Where the phone's photos land on their way to the archive: beside the
    data folder, not in it, since the Import never reads its own data folder."""
    data_dir = os.path.normpath(data_dir)
    return os.path.join(f"{data_dir} {MIRROR_DIR}", _name(drive))


def imported_list(data_dir: str, drive: Drive, kind: str = "imported") -> str:
    """What has already come in from this phone, one path per line. With
    *kind* "failed" or "unsure", the last fetch's copies that did not finish
    or whose size the phone did not tell."""
    return os.path.join(data_dir, MIRROR_DIR, f"{_name(drive)}.{kind}")


def _read_list(path: str) -> set[str]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return {line.strip("\r\n") for line in f if line.strip()}
    except OSError:
        return set()


class PhoneImport:
    """One phone at a time: fetch over the cable, import, tidy up."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.process: subprocess.Popen | None = None
        self.state: dict[str, Any] = {"running": False, "phase": "", "message": None}

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def progress(self, engine=None) -> dict[str, Any]:
        with self.lock:
            out = dict(self.state, running=self.running)
        if out.get("phase") == "importing" and engine is not None:
            p = engine.progress()
            out.update(done=p["processed"], total=p["total_files"])
        return out

    def _set(self, **values: Any) -> None:
        with self.lock:
            self.state.update(values)

    def start(self, drive: Drive, mirror: str, destination: str, engine, data_dir: str) -> None:
        if self.running:
            raise RuntimeError("busy")
        self.cancel.clear()
        self.state = {"running": True, "phase": "fetching", "phone": drive.label, "done": 0,
                      "total": 0, "destination": destination, "message": None,
                      "finished_at": None}
        self.thread = threading.Thread(
            target=self._run, args=(drive, mirror, destination, engine, data_dir),
            name="phone-import", daemon=True)
        self.thread.start()

    def stop(self, engine=None) -> None:
        self.cancel.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.kill()
        if engine is not None and self.progress().get("phase") == "importing":
            engine.stop()

    def wait(self, timeout: float = 60) -> None:
        if self.thread is not None:
            self.thread.join(timeout)

    def _run(self, drive: Drive, mirror: str, destination: str, engine, data_dir: str) -> None:
        self.data_dir = data_dir
        parallel.background()
        try:
            if not self._fetch(drive, mirror):
                return
            # What did not finish coming across is not imported: it would be
            # archived as it is, cut short.
            failed = _read_list(imported_list(data_dir, drive, "failed"))
            for rel in failed:
                try:
                    os.remove(os.path.join(mirror, rel))
                except OSError:
                    pass
            self._set(phase="importing", done=0, total=0)
            try:
                engine.start([mirror], destination, ["image", "video"], "copy")
            except ValueError:
                # The Import page started a run while the phone was copying.
                self._set(phase="failed", message=_say(
                    "The photos came across from the phone, but another import is running. "
                    "Wait for it to finish, then import from the phone again: nothing is "
                    "copied twice."))
                return
            while engine.running:
                engine.wait(0.5)
            if self.cancel.is_set():
                self._set(phase="stopped", message=_say(
                    "Stopped. Nothing on the phone was changed."))
                return
            ended = engine.progress()
            if ended.get("phase") != "done":
                # The archive step did not finish (no room, stopped from the
                # Import page): say what it said, not "done".
                self._set(phase="failed" if ended.get("phase") == "failed" else "stopped",
                          message=ended.get("job_said") or _say(
                              "The archive step stopped before the end. Import from the phone "
                              "again to finish: nothing is copied twice."))
                return
            added, already = self._tidy(mirror, data_dir, imported_list(data_dir, drive),
                                        _read_list(imported_list(data_dir, drive, "unsure")))
            counts = {"added": f"{added:,}", "already": f"{already:,}"}
            if failed:
                counts["failed"] = f"{len(failed):,}"
                key = ("{added} new photos and videos from the phone are in the archive. "
                       "{already} were there already. {failed} did not finish copying from "
                       "the phone; they will be copied again next time.")
            else:
                key = ("{added} new photos and videos from the phone are in the archive. "
                       "{already} were there already.")
            # Where they went, so nobody has to guess which folder to open.
            key += " They are in {folder}."
            counts["folder"] = destination
            self._set(phase="done", message=_say(key, **counts))
        except Exception:  # noqa: BLE001 — said, not raised
            log.exception("phone import failed")
            self._set(phase="failed", message=_say(
                "The phone could not be read. Keep it plugged in and unlocked, and try again."))
        finally:
            self._set(running=False, finished_at=time.time())

    def _fetch(self, drive: Drive, mirror: str) -> bool:
        """Copy over the cable into *mirror*; False when it went no further."""
        from . import media
        os.makedirs(mirror, exist_ok=True)
        exts = sorted(media.PHOTO_EXTS | media.VIDEO_EXTS)
        failed, unsure = (imported_list(self.data_dir, drive, k) for k in ("failed", "unsure"))
        os.makedirs(os.path.dirname(failed), exist_ok=True)
        for stale in (failed, unsure):
            try:
                os.remove(stale)
            except FileNotFoundError:
                pass
        env = {"NL_PHONE": drive.path, "NL_DEST": mirror, "NL_EXTS": "|".join(exts),
               "NL_TOPS": "|".join(PHONE_FOLDERS),
               "NL_SKIP": imported_list(self.data_dir, drive),
               "NL_FAILED": failed, "NL_UNSURE": unsure}
        self.process = _powershell(FETCH_SCRIPT, env)
        error = None
        finished = False
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            try:
                line = json.loads(raw.decode("utf-8", "replace").strip() or "{}")
            except ValueError:
                continue
            if "total" in line:
                self._set(total=int(line["total"]))
            elif "done" in line:
                self._set(done=int(line["done"]))
            elif "error" in line:
                error = line["error"]
            elif line.get("finished"):
                finished = True
        self.process.wait()
        self.process = None
        if self.cancel.is_set():
            self._set(phase="stopped", message=_say("Stopped. Nothing on the phone was changed."))
            return False
        if error == "gone":
            self._set(phase="failed", message=_say("That drive is no longer plugged in."))
            return False
        if error == "locked" or not finished:
            self._set(phase="failed", message=_say(
                "The phone did not share its photos. Unlock it, and choose File transfer "
                "(on an iPhone, Trust this computer), then try again."))
            return False
        return True

    def _tidy(self, mirror: str, data_dir: str, listing: str,
              unsure: set[str] = frozenset()) -> tuple[int, int]:
        """Each copy the import verified, or found already in the archive, is
        written down as imported and deleted from the data folder. A copy in
        *unsure* (the phone did not tell its size, so it may be cut short) is
        deleted but not written down, so the next fetch brings it again and
        the import keeps it only if it differs."""
        from . import db
        from .importer import still_done
        conn = db.connect(data_dir)
        try:
            rows = conn.execute(
                "SELECT source, status, destination, duplicate_of, size, mtime "
                "FROM import_files WHERE status IN ('verified', 'duplicate')"
            ).fetchall()
        finally:
            conn.close()
        added = already = 0
        done = []
        for row in rows:
            source, status = row["source"], row["status"]
            if not is_within(source, mirror):
                continue
            try:
                st = os.stat(source)
            except OSError:
                continue
            # Only a copy whose record describes this very file, with its
            # archived copy still there, is safe to let go of.
            if not still_done(row, st):
                continue
            rel = os.path.relpath(source, mirror)
            if rel not in unsure:
                done.append(rel)
            if status == "verified":
                added += 1
            else:
                already += 1
            try:
                os.remove(source)
            except OSError:
                pass
        if done:
            os.makedirs(os.path.dirname(listing), exist_ok=True)
            with open(listing, "a", encoding="utf-8") as f:
                f.write("".join(f"{rel}\n" for rel in done))
        for here, _dirs, _files in sorted(os.walk(mirror), key=lambda w: -len(w[0])):
            if here != mirror:
                try:
                    os.rmdir(here)
                except OSError:
                    pass
        return added, already
