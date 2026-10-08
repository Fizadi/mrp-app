@echo off
REM ============================================================
REM  Docker mode: Flask + SQL Server in containers
REM  Save as: D:\projects\mrp\mrp-app\run_docker.bat
REM ============================================================

cd /d D:\projects\mrp\mrp-app

echo [1/5] Switching .env to Docker mode...
copy /Y .env.docker .env >nul

echo [2/5] Ensuring Docker Desktop is running...
wsl docker ps >nul 2>&1
if errorlevel 1 (
    echo    Starting Docker Desktop...
    start "" "C:\Users\lenovo\AppData\Local\Programs\DockerDesktop\frontend\Docker Desktop.exe"
    echo    Waiting 90 seconds for Docker daemon...
    timeout /t 90 /nobreak >nul
)

echo [3/5] Stopping any native Flask on port 5001...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5001 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo [4/5] Bringing up the stack...
wsl bash -c "cd ~/projects/mrp/mrp-app && docker compose up -d"

echo [5/5] Status:
wsl bash -c "cd ~/projects/mrp/mrp-app && docker compose ps"

echo.
echo    Login page:  http://localhost:5001/auth/login
echo    Logs:        run_stop_docker.bat then check logs, or:
echo                 wsl bash -c \"cd ~/projects/mrp/mrp-app ^&^& docker compose logs --tail=40 web\"
echo.
echo    Wait 2-4 minutes for sqlserver to become healthy, then open the URL.
echo.