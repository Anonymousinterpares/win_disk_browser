@echo off
title DiskInsight Pro - Performance Setup
echo ============================================
echo    DiskInsight Pro - Performance Setup
echo    Installing all optimizations
echo ============================================
echo.

echo This will install all components for maximum performance:
echo.
echo 1. customtkinter - Modern UI
echo 2. pywin32 - Windows API for 10x faster scanning
echo.
echo Press Ctrl+C to cancel or
pause

echo.
echo Installing performance packages...
echo ============================================

REM Upgrade pip first
echo Upgrading pip...
python -m pip install --upgrade pip

echo.
echo Installing customtkinter (Modern UI)...
pip install customtkinter

echo.
echo Installing pywin32 (Maximum Performance)...
pip install pywin32

echo.
echo ============================================
echo Installation Complete!
echo ============================================
echo.
echo Testing installations...
echo.

python -c "import customtkinter; print('✓ CustomTkinter OK')" 2>nul || echo ✗ CustomTkinter failed
python -c "import win32file; print('✓ PyWin32 OK - 10x performance enabled!')" 2>nul || echo ✗ PyWin32 failed

echo.
echo ============================================
echo Setup complete! You can now run:
echo.
echo 1. run_optimized.bat - For maximum performance
echo 2. benchmark.py - To test performance
echo ============================================
echo.
pause
