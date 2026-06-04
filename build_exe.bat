@echo off
setlocal
cd /d "%~dp0"
set APP_NAME=wz_auto_tool

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller is not installed.
  echo Run install_build_deps.bat first, then run this script again.
  pause
  exit /b 1
)

if exist build rmdir /s /q build
if exist "dist\%APP_NAME%" rmdir /s /q "dist\%APP_NAME%"

python pack_templates.py
if errorlevel 1 (
  echo Failed to pack templates.
  pause
  exit /b 1
)

python -m PyInstaller --noconfirm --clean --onedir --windowed --name "%APP_NAME%" wz_auto_desktop.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

copy /y config.yaml "dist\%APP_NAME%\" >nul
copy /y README_RELEASE.md "dist\%APP_NAME%\README.md" >nul
copy /y VERSION.txt "dist\%APP_NAME%\" >nul
copy /y RELEASE_NOTES.md "dist\%APP_NAME%\" >nul
copy /y templates.dat "dist\%APP_NAME%\" >nul
if not exist "dist\%APP_NAME%\captures" mkdir "dist\%APP_NAME%\captures"

echo.
echo Build complete:
echo %cd%\dist\%APP_NAME%\%APP_NAME%.exe
echo.
echo Copy the whole folder dist\%APP_NAME% to another Windows computer.
pause
