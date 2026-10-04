' Ninaivu Lite (portable) - double-click to open the Control Panel, with no console window.
' Everything stays in this folder: the program beside this file, and the family's
' settings, index and previews in its "data" folder. The photographs stay where they are.
Option Explicit
Dim shell, files, folder, python, data
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(folder, "Python\pythonw.exe")
data = files.BuildPath(folder, "data")
If Not files.FileExists(python) Then
    MsgBox "This folder is not a whole Ninaivu Lite." & vbCrLf & vbCrLf & _
           "Extract every file from the zip (right-click it, Extract All), then open this again.", 48, "Ninaivu Lite"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & python & Chr(34) & " -m ninaivu_lite.panel --data " & Chr(34) & data & Chr(34), 1, False
