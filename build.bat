@echo off
rem Build desktop pet into exe (requires: pip install pyinstaller)
rem Output: dist\XiaoheiPet\  (zip this folder to share)
setlocal
set "PYEX=C:\Users\Matebook 14\.workbuddy\binaries\python\envs\pet\Scripts\python.exe"
"%PYEX%" -m PyInstaller --noconsole --onedir --name XiaoheiPet --add-data "assets;assets" --clean pet.py
echo.
echo Build done. Output in dist\XiaoheiPet\
endlocal
