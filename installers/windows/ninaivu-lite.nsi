; pynsist's own template with five additions: a "start at sign-in" box (left
; as it was on an upgrade), the Control Panel on the Desktop, the Control
; Panel opened when the installer finishes, advice to stop a running Ninaivu
; Lite before an upgrade (it offers to do it), and a tidy stop before an upgrade
; or an uninstall (the uninstall's in a section of its own that runs before
; the packages go), with the old program removed before an upgrade. Everything else is pynsist's; see
; https://github.com/takluyver/pynsist/blob/master/nsist/pyapp.nsi
[% extends "pyapp.nsi" %]

[% block ui_pages %]
  ; A running Ninaivu Lite, or an open Control Panel, holds pythonw.exe (and
  ; python.exe, and the DLLs they loaded) open, and Windows will not let the
  ; installer write over a file in use: the upgrade used to stop with a write
  ; error. So, before any file is written: ask the server to stop, then, while
  ; either program is still in use, ask the person to stop it and close the
  ; panel, with Retry, rather than fail half-way.
  !macro WaitUntilNotInUse
    ; One id for this insertion's labels (the macro is used twice).
    !define U ${__COUNTER__}
    IfFileExists "$INSTDIR\Python\pythonw.exe" 0 not_in_use_${U}
    ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --stop'
    check_${U}:
      ClearErrors
      FileOpen $0 "$INSTDIR\Python\pythonw.exe" a
      IfErrors in_use_${U}
      FileClose $0
      IfFileExists "$INSTDIR\Python\python.exe" 0 not_in_use_${U}
      FileOpen $0 "$INSTDIR\Python\python.exe" a
      IfErrors in_use_${U}
      FileClose $0
      Goto not_in_use_${U}
    in_use_${U}:
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
        "Ninaivu Lite is still running, so its files cannot be replaced.$\r$\n$\r$\nIn the Ninaivu Lite Control Panel press Stop (or close the window Ninaivu Lite was started from), then close the Control Panel itself, and press Retry." \
        IDRETRY check_${U}
      Abort "Ninaivu Lite is still running. Stop it and close the Control Panel, then run this installer again."
    not_in_use_${U}:
    !undef U
  !macroend

  ; What Windows shows under Properties, Details, and what the code-signing
  ; policy promises: the product is "Ninaivu Lite", at this version, every build.
  VIProductVersion "[[ ib.version ]].0"
  VIAddVersionKey "ProductName" "Ninaivu Lite"
  VIAddVersionKey "ProductVersion" "[[ ib.version ]]"
  VIAddVersionKey "FileVersion" "[[ ib.version ]]"
  VIAddVersionKey "FileDescription" "Ninaivu Lite installer"
  VIAddVersionKey "CompanyName" "Jagadeesh Rajendran"
  VIAddVersionKey "LegalCopyright" "(c) 2026 Jagadeesh Rajendran. MIT licence."
  ; "1" once the install folder is found to hold a Ninaivu Lite already: an upgrade.
  Var nl_ours
  ; "1" when the earlier version was running when the upgrade began: it is
  ; started again at the end, so phones do not lose the library until
  ; somebody presses Start (with /S nobody would).
  Var nl_was_running
  ; A components page, so "Start Ninaivu Lite at sign-in" can be unticked; on
  ; an upgrade it starts as the person left it (RememberStartAtSignIn).
  !define MUI_PAGE_CUSTOMFUNCTION_PRE RememberStartAtSignIn
  !insertmacro MUI_PAGE_COMPONENTS
  ; The last page offers to open the Control Panel (ticked): from there
  ; Ninaivu Lite is started, and the console walks through the first day.
  ; Through a function: the shortcut's parameters carry their own quotes,
  ; which MUI_FINISHPAGE_RUN_PARAMETERS cannot hold.
  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_TEXT "Open the Ninaivu Lite Control Panel"
  !define MUI_FINISHPAGE_RUN_FUNCTION OpenControlPanel
  [[ super() ]]
[% endblock %]

[% block install_pkgs %]
  ; An upgrade: nothing is written while the old one is in use, then the old
  ; program is removed whole, so no file of an earlier version is left among the
  ; new ones. Only the installed program goes (the private Python, the packages,
  ; the commands, the files beside them): the family's data
  ; (%LOCALAPPDATA%\Ninaivu-lite: people, settings, index, previews) lives
  ; elsewhere and is not touched, and the photographs never are. Only when the
  ; folder holds a Ninaivu Lite already: a first install into a folder that
  ; has other things in it (a Python, a bin folder of its own) removes nothing,
  ; and only these named folders are removed, never the whole install folder,
  ; in case it was chosen to be a shared one.
  StrCpy $nl_ours ""
  IfFileExists "$INSTDIR\pkgs\ninaivu_lite\__init__.py" 0 nl_first_install
  StrCpy $nl_ours "1"
  StrCpy $nl_was_running ""
  IfFileExists "$INSTDIR\Python\pythonw.exe" 0 nl_not_running
    ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --running' $0
    StrCmp $0 "0" 0 nl_not_running
    StrCpy $nl_was_running "1"
    ; Said before anything is stopped: the person may be in the middle of
    ; something, and should know the library is safe. Not asked with /S.
    IfSilent nl_not_running
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
      "Ninaivu Lite is running. It is best to stop it before updating: in the Ninaivu Lite Control Panel press Stop, then close the Control Panel.$\r$\n$\r$\nPress OK to let this installer stop it now and start it again when the update is done, or Cancel to stop it yourself first and run this installer again.$\r$\n$\r$\nYour photographs, people, settings, index and backups are kept either way." \
      IDOK nl_not_running
    Abort "Nothing was changed. Stop Ninaivu Lite and close the Control Panel, then run this installer again."
  nl_not_running:
  !insertmacro WaitUntilNotInUse
  DetailPrint "Removing the previous Ninaivu Lite program files..."
  RMDir /r "$INSTDIR\Python"
  RMDir /r "$INSTDIR\pkgs"
  RMDir /r "$INSTDIR\bin"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\_system_path.py"
  nl_first_install:
  [[ super() ]]
[% endblock %]

[% block install_shortcuts %]
  [[ super() ]]
  [% for scname, sc in ib.shortcuts.items() %][% if loop.first %]
  CreateShortCut "$DESKTOP\Ninaivu Lite Control Panel.lnk" "[[ sc['target'] ]]" \
    '[[ sc['parameters'] ]]' "$INSTDIR\[[ sc['icon'] ]]"
  [% endif %][% endfor %]
[% endblock %]

[% block uninstall_shortcuts %]
  [[ super() ]]
  Delete "$DESKTOP\Ninaivu Lite Control Panel.lnk"
[% endblock %]


[% block sections %]
  ; A signed uninstaller. NSIS writes the uninstaller on the person's computer,
  ; where nothing can sign it, so the build makes it first: built with
  ; /DMAKE_UNINSTALLER this is a small program that only writes uninstall.exe
  ; beside itself and stops; that file is signed, and the real installer,
  ; built with /DSIGNED_UNINSTALLER=<file>, carries it (build.ps1).
  !ifdef MAKE_UNINSTALLER
    OutFile "make-uninstaller.exe"
    Section -MakeUninstaller
      WriteUninstaller "$EXEDIR\uninstall.exe"
      Quit
    SectionEnd
  !endif
  ; Before pynsist's own "Uninstall" section (uninstaller sections run in the
  ; order they are written), which removes the packages first: stop Ninaivu
  ; Lite and stop starting it while the program is still there to ask, and
  ; wait until nothing of it is in use, as an upgrade does. The sign-in
  ; shortcut is then removed by name too, so even a failed ask leaves no
  ; shortcut pointing at a removed program. The data folder
  ; (%LOCALAPPDATA%\Ninaivu-lite: settings, index, previews) is left alone,
  ; and so, always, are the photographs.
  Section "un.Stop Ninaivu Lite"
    SetShellVarContext current
    IfFileExists "$INSTDIR\Python\pythonw.exe" 0 +2
      ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --stop --autostart off'
    Delete "$SMSTARTUP\Ninaivu Lite.vbs"
    !insertmacro WaitUntilNotInUse
  SectionEnd
  [[ super() ]]
  !ifdef SIGNED_UNINSTALLER
    Section -SignedUninstaller
      ; Over the one just written, whatever its date.
      SetOutPath "$INSTDIR"
      SetOverwrite on
      File "/oname=uninstall.exe" "${SIGNED_UNINSTALLER}"
      SetOverwrite ifnewer
    SectionEnd
  !endif
  Function OpenControlPanel
    [% for scname, sc in ib.shortcuts.items() %][% if loop.first %]
    Exec '"[[ sc['target'] ]]" [[ sc['parameters'] ]]'
    [% endif %][% endfor %]
  FunctionEnd

  Section "Start Ninaivu Lite at sign-in" sec_autostart
    ; The same switch as the box in the Control Panel. An upgrade keeps the
    ; choice made since: the box starts unticked when it was turned off
    ; (RememberStartAtSignIn), and with /S, where no page is shown, it is
    ; left off here.
    IfSilent 0 nl_autostart_on
    StrCmp $nl_ours "1" 0 nl_autostart_on
    ReadEnvStr $R9 APPDATA
    IfFileExists "$R9\Microsoft\Windows\Start Menu\Programs\Startup\Ninaivu Lite.vbs" nl_autostart_on
    Goto nl_autostart_done
    nl_autostart_on:
    ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --autostart on'
    nl_autostart_done:
  SectionEnd

  Section "-Start again after an upgrade"
    StrCmp $nl_was_running "1" 0 nl_restart_done
    DetailPrint "Starting Ninaivu Lite again, as it was running before the upgrade..."
    Exec '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --start'
    nl_restart_done:
  SectionEnd

  ; Last of the uninstaller: what was kept, and where.
  Section "un.Say what was kept"
    DetailPrint "Your library's settings, people and index are kept in $LOCALAPPDATA\Ninaivu-lite. Delete that folder to remove them. Your photographs were not touched."
    IfSilent +2
    MessageBox MB_OK|MB_ICONINFORMATION "Ninaivu Lite was removed. Your photographs were not touched.$\r$\n$\r$\nIts settings, people and index are kept in $LOCALAPPDATA\Ninaivu-lite, for a later install. Delete that folder to remove them too."
  SectionEnd

  ; Before the components page: over an earlier install whose sign-in
  ; shortcut was turned off, the box starts unticked. (The shortcut is the
  ; signed-in person's, so it is found through APPDATA, as control.py does.)
  Function RememberStartAtSignIn
    IfFileExists "$INSTDIR\pkgs\ninaivu_lite\__init__.py" 0 nl_remember_done
    ReadEnvStr $R9 APPDATA
    IfFileExists "$R9\Microsoft\Windows\Start Menu\Programs\Startup\Ninaivu Lite.vbs" nl_remember_done
    SectionSetFlags ${sec_autostart} 0
    nl_remember_done:
  FunctionEnd
[% endblock %]
