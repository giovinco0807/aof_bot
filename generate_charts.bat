@echo off
title Regenerate Rakeback Charts
echo ========================================
echo   Regenerating Nash Equilibrium Charts
echo   (0.6%% Effective Rake, 0.9BB Cap)
echo ========================================
echo.

cd /d D:\aof_bot\solver
echo Building solver...
cargo build --release --bin main
if %errorlevel% neq 0 (
    echo [Error] Failed to build the solver.
    pause
    exit /b %errorlevel%
)

echo.
echo Running solver for 100 MILLION iterations to generate charts (this WILL take a long time)...
.\target\release\main.exe solve-all -i 100000000 --rake 0.006 --rake-cap 0.9 -o data/charts_rakeback
if %errorlevel% neq 0 (
    echo [Error] Failed to generate charts.
    pause
    exit /b %errorlevel%
)

echo.
echo ========================================
echo   Done! Charts saved to:
echo   D:\aof_bot\solver\data\charts_rakeback
echo ========================================
pause
