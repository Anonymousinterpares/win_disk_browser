@echo off
REM ============================================================================
REM == This script launches the Visual Analyzer with Administrator privileges ==
REM == which are REQUIRED for the high-speed USN Journal cache refresh.      ==
REM ============================================================================

REM Check for Admin privileges
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

REM If we get here, we have Admin rights.
echo Running with Administrator privileges.

REM Find the Python executable in a robust way
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not in the PATH. Please run this script from an environment where 'python' is available.
    pause
    exit /b
)

REM Get the directory of this batch file
set SCRIPT_DIR=%~dp0

REM Run the main Python application
echo Starting DiskInsight Pro...
python "%SCRIPT_DIR%visual_analyzer.py"

echo.
echo Application has closed.
pause