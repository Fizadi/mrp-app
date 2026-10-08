@echo off
wsl bash -c "cd ~/projects/mrp/mrp-app && docker compose down"
wsl --shutdown
taskkill /IM "Docker Desktop.exe" /F >nul 2>&1
echo Docker stopped.