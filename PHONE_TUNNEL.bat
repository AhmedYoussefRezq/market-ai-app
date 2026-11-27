@echo off
cd /d "%~dp0"
echo Secure HTTPS tunnel for Market AI (port 8002)...
echo The https://... URL below is temporary and changes each run.
echo Keep this window open.
echo.
if not exist cloudflared.exe (
  echo Downloading cloudflared...
  curl.exe -L --fail -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)
cloudflared.exe tunnel --url http://127.0.0.1:8002
pause
