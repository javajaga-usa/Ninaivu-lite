<#
Make the portable zip from an installed, stopped Ninaivu Lite folder.

    installers\windows\portable\build-portable.ps1 -Source <installed folder> -Zip <out.zip>

The zip holds one folder, "Ninaivu Lite", with one program at its top:

    Ninaivu Lite\
      Ninaivu Lite.exe      opens the Control Panel (launcher.nsi)
      app\                  the program: Python\, pkgs\, README-PORTABLE.txt, ...
    and, once started:
      Ninaivu Lite.log      the log
      data\                 the family's settings, index, previews and backups

The installed folder goes into app\ without its uninstaller. Needs NSIS
(makensis), as build.ps1 does. -Source is used up: it is moved, not copied.
#>
param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Zip)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here "..\..\..")).Path
$text = Get-Content (Join-Path $root "ninaivu_lite\version.py") -Raw
$version = [regex]::Match($text, '__version__\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw "no __version__ in ninaivu_lite\version.py" }

$makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $makensis) { $makensis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
if (-not (Test-Path $makensis)) { throw "makensis.exe not found (install NSIS)" }

$Source = (Resolve-Path $Source).Path
if (-not (Test-Path (Join-Path $Source "Python\pythonw.exe"))) { throw "not an installed Ninaivu Lite: $Source" }
Remove-Item (Join-Path $Source "uninstall.exe") -ErrorAction SilentlyContinue
Get-ChildItem $Source -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item (Join-Path $here "README-PORTABLE.txt") $Source

# One folder inside the zip, so Extract All gives "Ninaivu Lite" and not loose files.
$stage = Join-Path ([IO.Path]::GetTempPath()) ("ninaivu-portable-" + [guid]::NewGuid())
$top = Join-Path $stage "Ninaivu Lite"
New-Item -ItemType Directory $top | Out-Null
try {
    Move-Item $Source (Join-Path $top "app")
    & $makensis /V2 "/DVERSION=$version" "/DOUTFILE=$(Join-Path $top 'Ninaivu Lite.exe')" (Join-Path $here "launcher.nsi") | Out-Host
    if ($LASTEXITCODE) { throw "makensis (portable launcher) failed ($LASTEXITCODE)" }
    $loose = Get-ChildItem $top -File | Where-Object Name -ne "Ninaivu Lite.exe"
    if ($loose) { throw "loose files at the top of the portable folder: $($loose.Name -join ', ')" }
    if (Test-Path $Zip) { Remove-Item $Zip }
    Compress-Archive -Path $top -DestinationPath $Zip -CompressionLevel Optimal
} finally {
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}
Write-Host "$Zip $([math]::Round((Get-Item $Zip).Length / 1MB, 1)) MB"
