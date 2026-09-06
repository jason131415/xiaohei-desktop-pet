@echo off
rem WorkBuddy 桌面宠物启动脚本（双击即可召唤小黑）
setlocal
set "PYEX=C:\Users\Matebook 14\.workbuddy\binaries\python\envs\pet\Scripts\pythonw.exe"
set "SCRIPT=C:\Users\Matebook 14\WorkBuddy\2026-09-04-23-55-11\pet\pet.py"
start "" "%PYEX%" "%SCRIPT%"
endlocal