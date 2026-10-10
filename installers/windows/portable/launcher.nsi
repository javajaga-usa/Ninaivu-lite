; Ninaivu Lite (portable): the one program at the top of the extracted folder.
; Double-clicked, it opens the Control Panel with no console window, on the data
; folder beside it. Everything else is out of the way: the program in app\, the
; family's settings, index and previews in data\, and the log in Ninaivu Lite.log.
;
;   makensis /DVERSION=1.12.0 "/DOUTFILE=<folder>\Ninaivu Lite.exe" launcher.nsi
;
; Built by build-portable.ps1. It installs nothing: it starts the private Python
; in app\ and is gone.

Unicode true
!ifndef VERSION
  !error "VERSION is needed: makensis /DVERSION=1.2.3"
!endif
!ifndef OUTFILE
  !define OUTFILE "Ninaivu Lite.exe"
!endif

Name "Ninaivu Lite"
OutFile "${OUTFILE}"
Icon "..\ninaivu-lite.ico"
RequestExecutionLevel user
SilentInstall silent
ManifestDPIAware true
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Ninaivu Lite"
VIAddVersionKey "FileDescription" "Ninaivu Lite (portable)"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "Jagadeesh Rajendran"
VIAddVersionKey "LegalCopyright" "Copyright 2026 Jagadeesh Rajendran"

Section
  IfFileExists "$EXEDIR\app\Python\pythonw.exe" whole
    MessageBox MB_OK|MB_ICONEXCLAMATION "This folder is not a whole Ninaivu Lite.$\r$\n$\r$\nExtract every file from the zip (right-click it, Extract All), then open this again."
    Quit
  whole:
  ; The program folder as the working folder, as the installed copy runs.
  SetOutPath "$EXEDIR\app"
  ClearErrors
  Exec '"$EXEDIR\app\Python\pythonw.exe" -m ninaivu_lite.panel --data "$EXEDIR\data"'
  IfErrors 0 +2
    MessageBox MB_OK|MB_ICONSTOP "Ninaivu Lite could not start its Control Panel from $EXEDIR\app."
SectionEnd
