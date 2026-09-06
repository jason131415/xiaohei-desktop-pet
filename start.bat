@echo off
rem Desktop pet launcher (double-click to run)
setlocal
set "PYEX=C:\Users\Matebook 14\.workbuddy\binaries\python\envs\pet\Scripts\pythonw.exe"
set "SCRIPT=%~dp0pet.py"
start "" "%PYEX%" "%SCRIPT%"
endlocal
