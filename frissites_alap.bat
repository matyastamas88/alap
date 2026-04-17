@echo off
title Bot Frissites - Alaprol
color 0B
echo.
echo ============================================================
echo   Bot Frissites - Alap repobol
echo   https://github.com/matyastamas88/alap.git
echo ============================================================
echo.

:: BOT_DIR = a bat fajl mappaja, zaro \ nelkul
set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"

if not exist "%BOT_DIR%\.git" (
    echo HIBA: Nem talalhato git repo ebben a mappaban: %BOT_DIR%
    pause
    exit /b 1
)

:: Atcsatolas az alap repora ha szukseges
git -C "%BOT_DIR%" remote set-url origin https://github.com/matyastamas88/alap.git
echo [+] Repo: https://github.com/matyastamas88/alap.git

echo.
echo Frissites ellenorzese...
git -C "%BOT_DIR%" fetch origin >nul 2>&1

echo Valtoztatasok listaja:
git -C "%BOT_DIR%" log HEAD..origin/master --oneline

echo.
echo Frissites letoltese... (Alap repo verzio mindig elsodleges)
git -C "%BOT_DIR%" reset --hard origin/master

echo.
echo ============================================================
echo   KESZ! Inditsd ujra a botot!
echo ============================================================
echo.
pause
