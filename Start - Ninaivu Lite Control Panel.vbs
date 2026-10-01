' Ninaivu Lite Control Panel - double-click to open it, with no console window.
' It runs the Control Panel with the pythonw in this folder's .venv, which
' start.cmd creates the first time it runs.
Option Explicit
Dim shell, files, folder, python
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(folder, ".venv\Scripts\pythonw.exe")
If Not files.FileExists(python) Then
    MsgBox "Ninaivu Lite is not set up in this folder yet." & vbCrLf & vbCrLf & _
           "Double-click start.cmd once to set it up, then open the Control Panel again.", 48, "Ninaivu Lite Control Panel"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & python & Chr(34) & " -m ninaivu_lite.panel", 1, False
