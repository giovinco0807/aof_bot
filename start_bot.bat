@echo off
title AoF Bot Launcher
echo ========================================
echo   AoF Bot - Starting all components...
echo ========================================
echo.

REM 1. Solver API server (background window)
echo [1/2] Starting GTO Solver API on port 8080...
start "AoF Solver API" cmd /k "cd /d D:\aof_bot\solver && .\target\release\api_server.exe --port 8080"

REM Wait for solver to initialize
timeout /t 2 /nobreak > nul

REM 2. Main GUI (foreground, runs as admin via self-elevation in gui.py)
echo [2/2] Starting AoF Bot GUI...
python D:\aof_bot\automation\gui.py

pause
