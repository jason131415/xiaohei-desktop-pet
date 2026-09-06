@echo off
rem Build desktop pet into exe (requires: pip install pyinstaller)
rem Output: dist\XiaoheiPet\  (zip this folder to share)
setlocal
where python >nul 2>nul
if %errorlevel%==0 (
    python -m PyInstaller --noconsole --onedir --name XiaoheiPet --add-data "assets;assets" --clean pet.py
    echo.
    echo Build done. Output in dist\XiaoheiPet\
) else (
    echo Python not found in PATH. Please install Python 3.10+ and add to PATH.
    pause
)
endlocal
