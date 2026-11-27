@echo off
cd /d "%~dp0"
python bot.py --live-demo >> run.log 2>&1
exit /b %errorlevel%
