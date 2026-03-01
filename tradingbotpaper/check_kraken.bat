@echo off
cd /d "%~dp0"

echo ===============================
echo        CHECK KRAKEN
echo ===============================

call venv\Scripts\activate

python check_kraken.py

echo.
pause