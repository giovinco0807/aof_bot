@echo off
echo Running pc_allin_bot.py as Administrator...
powershell -Command "Start-Process python -ArgumentList 'd:\aof_bot\automation\pc_allin_bot.py' -Verb RunAs"
pause
