; pynsist's own template with four additions: a "start at sign-in" box, the
; Control Panel on the Desktop, the Control Panel opened when the installer
; finishes, and a tidy stop before an upgrade or an uninstall. Everything else
; is pynsist's; see https://github.com/takluyver/pynsist/blob/master/nsist/pyapp.nsi
[% extends "pyapp.nsi" %]

[% block ui_pages %]
  ; A components page, so "Start Ninaivu Lite at sign-in" can be unticked.
  !insertmacro MUI_PAGE_COMPONENTS
  ; The last page offers to open the Control Panel (ticked): from there
  ; Ninaivu Lite is started, and the console walks through the first day.
  [% for scname, sc in ib.shortcuts.items() %][% if loop.first %]
  !define MUI_FINISHPAGE_RUN "[[ sc['target'] ]]"
  !define MUI_FINISHPAGE_RUN_PARAMETERS '[[ sc['parameters'] ]]'
  !define MUI_FINISHPAGE_RUN_TEXT "Open the Ninaivu Lite Control Panel"
  [% endif %][% endfor %]
  [[ super() ]]
[% endblock %]

[% block install_files %]
  ; An upgrade: ask a running Ninaivu Lite to stop first, so its files can be
  ; replaced. Nothing happens on a first install (there is no Python yet).
  IfFileExists "$INSTDIR\Python\pythonw.exe" 0 +2
    ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --stop'
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

[% block uninstall_files %]
  ; Stop it and stop starting it, while the program is still there to ask.
  ; The data folder (%LOCALAPPDATA%\Ninaivu-lite: settings, index, previews)
  ; is left alone, and so, always, are the photographs.
  ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --stop --autostart off'
  [[ super() ]]
[% endblock %]

[% block sections %]
  [[ super() ]]
  Section "Start Ninaivu Lite at sign-in" sec_autostart
    ; The same switch as the box in the Control Panel.
    ExecWait '"$INSTDIR\Python\pythonw.exe" -m ninaivu_lite.control --autostart on'
  SectionEnd
[% endblock %]
