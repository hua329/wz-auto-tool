@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set APP_NAME=王者荣耀自动练级工具
if not exist "dist\%APP_NAME%\%APP_NAME%.exe" (
  echo dist\%APP_NAME%\%APP_NAME%.exe not found.
  echo Run build_exe.bat first.
  pause
  exit /b 1
)
if exist "%APP_NAME%_v0.0.2.zip" del "%APP_NAME%_v0.0.2.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\%APP_NAME%\*' -DestinationPath '%APP_NAME%_v0.0.2.zip' -Force"
echo Created %cd%\%APP_NAME%_v0.0.2.zip
pause
