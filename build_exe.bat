@echo off
title AoF Bot - Build EXE
echo ========================================
echo   Building AoF Bot EXE...
echo ========================================
echo.

REM Install PyInstaller if needed
pip show pyinstaller >nul 2>&1 || (
    echo Installing PyInstaller...
    pip install pyinstaller
)

cd /d D:\aof_bot

REM Build the EXE
pyinstaller ^
    --name "AoF_Bot" ^
    --windowed ^
    --onedir ^
    --icon=NONE ^
    --add-data "hook/packet_capture.py;hook" ^
    --add-data "hook/pppoker_hook.js;hook" ^
    --add-data "automation/gto_lookup.py;." ^
    --add-data "automation/config.py;." ^
    --add-data "automation/pc_input.py;." ^
    --add-data "automation/card_reader.py;." ^
    --add-data "automation/card_recognizer.py;." ^
    --add-data "solver/data/charts_rb50;solver/data/charts_rb50" ^
    --add-data "automation/assets;assets" ^
    --add-data "automation/data/gui_config.json;data" ^
    --hidden-import=frida ^
    --hidden-import=cv2 ^
    --hidden-import=pyautogui ^
    --hidden-import=pygetwindow ^
    --collect-all frida ^
    automation/gui.py

echo.
if exist dist\AoF_Bot (
    echo ========================================
    echo   Build complete!
    echo   Output: dist\AoF_Bot\AoF_Bot.exe
    echo ========================================
) else (
    echo BUILD FAILED - check errors above
)
pause
