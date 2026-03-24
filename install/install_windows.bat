@echo off
REM Alien Node Plugin Installer — Windows
REM Run from the repo root directory

set GH_LIBRARIES=%APPDATA%\Grasshopper\Libraries
set ZIP_SOURCE=install\alien-plugin.zip

echo ════════════════════════════════════════════
echo  Alien Node Plugin Installer (Windows)
echo ════════════════════════════════════════════

REM Check if source exists
if not exist "%ZIP_SOURCE%" (
    echo [ERROR] Could not find %ZIP_SOURCE%
    echo         Make sure you're running this from the repo root.
    pause
    exit /b 1
)

REM Check if GH Libraries folder exists
if not exist "%GH_LIBRARIES%" (
    echo [ERROR] Grasshopper Libraries folder not found: %GH_LIBRARIES%
    echo         Is Rhino 8 installed? Open Grasshopper once to create the folder.
    pause
    exit /b 1
)

REM Check if Rhino is running
tasklist /FI "IMAGENAME eq Rhino.exe" 2>NUL | find /I /N "Rhino.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    echo [WARNING] Rhino is currently running.
    echo           Close Rhino before installing, or the file may be locked.
    echo.
    set /p CONTINUE="Continue anyway? (y/n): "
    if /i not "%CONTINUE%"=="y" exit /b 0
)

REM Extract zip
echo Extracting plugin files...
set TEMP_DIR=%TEMP%\alien-install-%RANDOM%
mkdir "%TEMP_DIR%" 2>NUL
powershell -Command "Expand-Archive -Force -Path '%ZIP_SOURCE%' -DestinationPath '%TEMP_DIR%'"

REM Copy files
echo Copying AlienNode.gha to %GH_LIBRARIES%...
copy /Y "%TEMP_DIR%\AlienNode.gha" "%GH_LIBRARIES%\AlienNode.gha"

echo Copying web UI files...
if not exist "%GH_LIBRARIES%\web" mkdir "%GH_LIBRARIES%\web"
copy /Y "%TEMP_DIR%\web\dashboard.html" "%GH_LIBRARIES%\web\dashboard.html"
copy /Y "%TEMP_DIR%\web\node-editor.html" "%GH_LIBRARIES%\web\node-editor.html"

if exist "%TEMP_DIR%\AlienNode.deps.json" copy /Y "%TEMP_DIR%\AlienNode.deps.json" "%GH_LIBRARIES%\AlienNode.deps.json"
if exist "%TEMP_DIR%\AlienNode.runtimeconfig.json" copy /Y "%TEMP_DIR%\AlienNode.runtimeconfig.json" "%GH_LIBRARIES%\AlienNode.runtimeconfig.json"

REM Unblock
echo Unblocking files...
powershell -Command "Get-ChildItem '%GH_LIBRARIES%\AlienNode*' | Unblock-File" 2>NUL
powershell -Command "Get-ChildItem '%GH_LIBRARIES%\web\*' | Unblock-File" 2>NUL

REM Cleanup
rmdir /S /Q "%TEMP_DIR%" 2>NUL

echo.
echo [DONE] Plugin installed.
echo        Start Rhino + Grasshopper and look for Alien in the Script tab.
echo.
pause
