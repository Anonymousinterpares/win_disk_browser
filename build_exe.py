"""
Build script for creating Windows executable of DiskInsight Pro
Uses PyInstaller to bundle the application with all dependencies
"""

import subprocess
import sys
import os
from pathlib import Path

def install_pyinstaller():
    """Install PyInstaller if not already installed"""
    try:
        import PyInstaller
        print("PyInstaller already installed")
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def build_executable():
    """Build the executable using PyInstaller"""
    
    # Define paths
    main_script = "visual_analyzer.py"
    dist_dir = "dist"
    build_dir = "build"
    
    # PyInstaller command with options
    cmd = [
        "pyinstaller",
        "--onefile",                    # Single executable file
        "--windowed",                   # No console window
        "--name=DiskInsightPro",        # Executable name
        "--icon=webview_ui/favicon.ico" if os.path.exists("webview_ui/favicon.ico") else "",
        "--add-data=webview_ui;webview_ui",  # Include web UI files
        "--add-data=disk_analyzer_fixed.py;.",  # Include local module as data
        "--add-data=live_update_system.py;.",   # Include live update module as data
        "--add-data=windows_scanner.py;.",
        "--add-data=normalized_cache.py;.",
        "--add-data=usn_journal.py;.",
        "--add-data=mft_scanner.py;.",
        "--hidden-import=disk_analyzer_fixed",  # Include local module
        "--hidden-import=live_update_system",   # Include live update module
        "--hidden-import=windows_scanner",
        "--hidden-import=normalized_cache",
        "--hidden-import=usn_journal",
        "--hidden-import=mft_scanner",
        "--hidden-import=sqlite3",              # SQLite database support
        "--hidden-import=win32file",
        "--hidden-import=win32api", 
        "--hidden-import=win32con",
        "--hidden-import=winioctlcon",
        "--hidden-import=pywintypes",
        "--hidden-import=customtkinter",
        "--hidden-import=watchdog",
        "--collect-submodules=webview",
        "--collect-submodules=customtkinter",
        main_script
    ]
    
    # Remove empty icon parameter if no icon file exists
    cmd = [arg for arg in cmd if arg]
    
    print(f"Building executable with command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print(f"\nBuild successful!")
        print(f"Executable location: {os.path.abspath(os.path.join(dist_dir, 'DiskInsightPro.exe'))}")
        print(f"File size: ~{get_file_size_mb(os.path.join(dist_dir, 'DiskInsightPro.exe')):.1f} MB")
        
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")
        return False
    
    return True

def get_file_size_mb(filepath):
    """Get file size in MB"""
    try:
        return os.path.getsize(filepath) / (1024 * 1024)
    except:
        return 0

def main():
    """Main build process"""
    print("DiskInsight Pro - Executable Builder")
    print("=" * 50)
    
    # Step 1: Install PyInstaller
    install_pyinstaller()
    
    # Step 2: Build executable
    if build_executable():
        print("\nNext Steps:")
        print("1. Find your executable in the 'dist' folder")
        print("2. Test the executable on your system")
        print("3. Distribute the .exe file - no Python installation required!")
        print("\nThe executable includes all dependencies and web UI files")
    else:
        print("\nBuild process failed. Check the error messages above.")

if __name__ == "__main__":
    main()