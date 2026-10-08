@echo off
REM  Usage:   switch_env.bat windows
REM           switch_env.bat docker
REM  Save as: D:\projects\mrp\mrp-app\switch_env.bat

if /i "%1"=="windows" (
    copy /Y .env.windows .env >nul
    echo .env now = WINDOWS (native SQLEXPRESS)
) else if /i "%1"=="docker" (
    copy /Y .env.docker .env >nul
    echo .env now = DOCKER (sqlserver container)
) else (
    echo Usage: switch_env.bat windows ^| docker
)
findstr /i "DB_SERVER DB_PORT" .env