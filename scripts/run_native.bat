@echo off
cd /d D:\projects\mrp\mrp-app
copy /Y .env.windows .env >nul
echo Stopping Docker Desktop to free RAM...
taskkill /IM "Docker Desktop.exe" /F >nul 2>&1
wsl --shutdown >nul 2>&1
echo Starting Flask on http://localhost:5001 ...
python run.py