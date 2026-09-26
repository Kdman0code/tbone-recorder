' Double-click launcher for tbone-rec with no console window.
' Runs tbone-rec.bat hidden; see %LOCALAPPDATA%\tbone-recorder\logs\tbone-rec.log
' for output, or double-click tbone-rec.bat directly to watch it run.
Dim fso, scriptDir, batPath
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptDir, "tbone-rec.bat")
CreateObject("WScript.Shell").Run """" & batPath & """", 0, False
