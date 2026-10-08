@echo off
REM  Save as: D:\projects\mrp\mrp-app\status.bat

echo === .env currently in use ===
findstr /i "DB_SERVER DB_PORT DB_NAME" .env

echo.
echo === Native SQL Server Express ===
sc query MSSQL$SQLEXPRESS | findstr /i "STATE"

echo.
echo === Native Flask on port 5001? ===
netstat -an | findstr :5001 | findstr LISTENING

echo.
echo === Docker Desktop running? ===
tasklist | findstr /i "Docker Desktop"

echo.
echo === WSL distros ===
wsl -l -v

echo.
echo === Docker containers ===
wsl docker ps 2>nul