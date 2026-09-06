@echo off
rem Build desktop pet into exe (requires: pip install pyinstaller)
rem Output: dist\XiaoheiPet\  (zip this folder to share)
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYEX=%~dp0.venv\Scripts\python.exe"
) else (
    set "PYEX=python"
)
"%PYEX%" -m PyInstaller --noconsole --onedir --name XiaoheiPet --add-data "assets;assets" --clean pet.py
echo.
echo Build done. Output in dist\XiaoheiPet\
endlocal
