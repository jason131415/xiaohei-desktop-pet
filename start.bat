@echo off
rem Desktop pet launcher (double-click to run)
setlocal
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0pet.py"
) else (
    start "" python "%~dp0pet.py"
)
endlocal
