@echo off
:: build.cmd - Build Block Search Tool into a standalone Windows executable
:: Usage: build.cmd [--clean] [--zip]
::   --clean   Delete previous build/dist folders before building
::   --zip     Also create a portable ZIP archive in dist/
::
:: Requirements:
::   .venv with PyInstaller, PySide6, ezdxf, rapidfuzz installed

setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "PYINSTALLER=%ROOT%\.venv\Scripts\pyinstaller.exe"
set "DIST_DIR=%ROOT%\dist"
set "ZIP_OUT=%ROOT%\dist\BlockSearchTool-portable.zip"
set "DO_CLEAN=0"
set "DO_ZIP=0"

:: -- Parse args --------------------------------------------------------------
:parse_args
if "%~1"=="" goto check_deps
if /i "%~1"=="--clean" ( set "DO_CLEAN=1" & shift & goto parse_args )
if /i "%~1"=="--zip"   ( set "DO_ZIP=1"   & shift & goto parse_args )
echo Unknown argument: %~1
goto :eof

:: -- Sanity checks -----------------------------------------------------------
:check_deps
echo.
echo ========================================
echo   Block Search Tool - Build Script
echo ========================================
echo.

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found at %PYTHON%
    echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
if not exist "%PYINSTALLER%" (
    echo [ERROR] PyInstaller not found.
    echo Run: .venv\Scripts\pip install pyinstaller
    exit /b 1
)

:: -- Step 1: Clean -----------------------------------------------------------
if "%DO_CLEAN%"=="1" (
    echo [1/5] Cleaning previous build...
    if exist "%ROOT%\build" ( rmdir /s /q "%ROOT%\build" && echo   Removed build\ )
    if exist "%ROOT%\dist"  ( rmdir /s /q "%ROOT%\dist"  && echo   Removed dist\  )
) else (
    echo [1/5] Skipping clean  (use --clean to remove previous build^)
)

:: -- Step 2: Generate ICO ----------------------------------------------------
echo [2/5] Generating icon.ico from icon.svg...
"%PYTHON%" "%ROOT%\make_ico.py"
if errorlevel 1 (
    echo [WARNING] ICO generation failed - building without custom icon
) else (
    echo   icon.ico ready
)

:: -- Step 3: PyInstaller -----------------------------------------------------
echo [3/5] Running PyInstaller...
cd /d "%ROOT%"
"%PYINSTALLER%" build.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    exit /b 1
)
echo   Build complete: %DIST_DIR%

:: -- Step 4: Verify output ---------------------------------------------------
echo [4/5] Verifying output...
set "EXE=%DIST_DIR%\BlockSearchTool.exe"
if not exist "%EXE%" (
    echo [ERROR] Expected executable not found: %EXE%
    exit /b 1
)
echo   BlockSearchTool.exe found OK

:: -- Step 5: Portable ZIP ----------------------------------------------------
if "%DO_ZIP%"=="1" (
    echo [5/5] Creating portable ZIP...
    if exist "%ZIP_OUT%" del /f /q "%ZIP_OUT%"
    powershell -NoProfile -Command "Compress-Archive -Path '%EXE%' -DestinationPath '%ZIP_OUT%'"
    if errorlevel 1 (
        echo [ERROR] ZIP creation failed.
        exit /b 1
    )
    echo   Created: %ZIP_OUT%
) else (
    echo [5/5] Skipping ZIP  (use --zip to create a portable archive^)
)

echo.
echo ========================================
echo   Done!  Output: dist\BlockSearchTool.exe
echo   Run:   dist\BlockSearchTool.exe
echo ========================================
echo.
endlocal
