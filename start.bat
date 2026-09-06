@echo off
rem Desktop pet launcher (double-click to run)
setlocal
set "SCRIPT=%~dp0pet.py"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%SCRIPT%"
) else (
    where pythonw >nul 2>nul
    if %errorlevel%==0 (
        start "" pythonw "%SCRIPT%"
    ) else (
        start "" python "%SCRIPT%"
    )
)
endlocal
