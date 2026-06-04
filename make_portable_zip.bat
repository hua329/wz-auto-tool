@echo off
setlocal
cd /d "%~dp0"
set APP_NAME=wz_auto_tool
if not exist "dist\%APP_NAME%\%APP_NAME%.exe" (
  echo dist\%APP_NAME%\%APP_NAME%.exe not found.
  echo Run build_exe.bat first.
  pause
  exit /b 1
)
if exist "%APP_NAME%_portable.zip" del "%APP_NAME%_portable.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\%APP_NAME%\*' -DestinationPath '%APP_NAME%_portable.zip' -Force"
echo Created %cd%\%APP_NAME%_portable.zip
pause
