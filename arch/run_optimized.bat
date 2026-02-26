@echo off
title DiskInsight Pro - OPTIMIZED Ultra-Fast Version
echo ============================================
echo    DiskInsight Pro - OPTIMIZED VERSION
echo    Ultra-Fast Disk Scanner
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher from python.org
    pause
    exit /b 1
)

echo Checking and installing required packages...
echo.

REM Check and install customtkinter
python -c "import customtkinter" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing CustomTkinter for modern UI...
    pip install customtkinter
    echo.
)

REM Check and install pywin32 for performance
python -c "import win32file" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing pywin32 for MAXIMUM PERFORMANCE...
    echo This enables 5-10x faster scanning!
    pip install pywin32
    echo.
    if %errorlevel% neq 0 (
        echo Warning: Could not install pywin32.
        echo The app will work but scanning will be slower.
        echo.
    )
) else (
    echo ✓ pywin32 found - Maximum performance enabled!
)

echo.
echo ============================================
echo Starting DiskInsight Pro OPTIMIZED Version
echo.
echo Performance Features Enabled:
echo ✓ Multi-threaded parallel scanning
echo ✓ In-memory caching
echo ✓ Batch database operations
echo ✓ Smart directory filtering
echo ✓ Windows API optimization (if pywin32 installed)
echo ============================================
echo.

REM Run the optimized application
python disk_analyzer_optimized.py

if %errorlevel% neq 0 (
    echo.
    echo Error running the application.
    echo Please check the error messages above.
    pause
)
