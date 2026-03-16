@echo off
echo Starting AoF Bot (2-Table Mode)...
cd /d "%~dp0"
python automation\gui_2table.py %*
pause
