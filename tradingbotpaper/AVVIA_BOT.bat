@echo off
cd /d "%~dp0"

echo ===============================
echo      AVVIO TRADING BOT
echo ===============================

call venv\Scripts\activate

python bot.py

echo.
echo Bot terminato.
pause