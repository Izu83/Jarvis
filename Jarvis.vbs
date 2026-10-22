' Double-click to start Jarvis in the background: no console window, just a tray icon
' under the ^ arrow in the taskbar (right-click it to quit). Works from wherever this file lives.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
pyw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
sh.Run """" & pyw & """ -m jarvis", 0, False
