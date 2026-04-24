@echo off
title Telegram Csoport ID Lekerdezes
cd /d %~dp0
echo.
echo ============================================================
echo   Telegram Csoport ID lekereso
echo   Listazza az osszes csoportot ahol bent vagy
echo ============================================================
echo.
python get_chat_id.py
echo.
pause
