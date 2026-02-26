@echo off
title DiskInsight Pro - Launcher
color 0A
cls

:menu
echo ============================================
echo       DiskInsight Pro - Main Menu
echo ============================================
echo.
echo   1. Run FIXED Version (Correct Sizes)
echo   2. Run OPTIMIZED Version (Fast but buggy)
echo   3. Run Original Version
echo   4. Run Performance Benchmark
echo   5. Install Performance Components
echo   6. Test Installation
echo   7. Exit
echo.
echo ============================================
echo.

set /p choice="Select an option (1-7): "

if "%choice%"=="1" goto fixed
if "%choice%"=="2" goto optimized
if "%choice%"=="3" goto original
if "%choice%"=="4" goto benchmark
if "%choice%"=="5" goto install
if "%choice%"=="6" goto test
if "%choice%"=="7" goto exit

echo Invalid choice! Please select 1-7.
pause
cls
goto menu

:fixed
cls
echo Starting FIXED version (with correct size calculation)...
echo.
call run_fixed.bat
pause
cls
goto menu

:optimized
cls
echo Starting OPTIMIZED version (fast but has size calculation issues)...
echo.
call run_optimized.bat
pause
cls
goto menu

:original
cls
echo Starting original version...
echo.
call run.bat
pause
cls
goto menu

:benchmark
cls
echo Running performance benchmark...
echo.
python benchmark.py
pause
cls
goto menu

:install
cls
echo Installing performance components...
echo.
call install_performance.bat
pause
cls
goto menu

:test
cls
echo Testing installation...
echo.
python test_installation.py
pause
cls
goto menu

:exit
echo.
echo Thank you for using DiskInsight Pro!
timeout /t 2 >nul
exit
