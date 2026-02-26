@echo off
title DiskInsight Pro - Launcher

REM ============================================================================
REM == This script launches the main DiskInsight Pro GUI with Administrator   ==
REM == privileges. This is REQUIRED for features like the high-speed USN      ==
REM == Journal cache refresh in the Visual Analyzer.                          ==
REM ============================================================================

REM --- Check for Administrator Privileges ---
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    REM Relaunch this same script as an Administrator and exit the current one.
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)


REM --- If we get here, we have Admin rights. ---
cls
echo ============================================
echo    DiskInsight Pro - FIXED Version
echo    (Running with Administrator privileges)
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    echo Please run this script from an environment where 'python' is available.
    pause
    exit /b 1
)

echo Starting Visual Analyzer with:
echo - Web-based interface
echo - High-speed USN Journal cache refresh enabled
echo - TreeView as default mode
echo.

REM Get the directory of this batch file to ensure scripts are found correctly
set SCRIPT_DIR=%~dp0

REM Run the visual analyzer directly using the full path
python "%SCRIPT_DIR%visual_analyzer.py"

if %errorlevel% neq 0 (
    echo.
    echo Error running the application. Please check the console for messages.
)

echo.
echo Application has closed.
pause