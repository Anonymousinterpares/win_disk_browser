"""
Test script for DiskInsight Pro
Verifies installation and basic functionality
"""

import sys
import os

def test_imports():
    """Test if required modules can be imported"""
    print("Testing Python installation and imports...")
    print(f"Python version: {sys.version}")
    print(f"Python path: {sys.executable}\n")
    
    # Test standard library imports
    required_modules = [
        'tkinter',
        'sqlite3',
        'threading',
        'pathlib',
        'json',
        'queue',
        'collections'
    ]
    
    all_ok = True
    for module in required_modules:
        try:
            __import__(module)
            print(f"✓ {module} - OK")
        except ImportError as e:
            print(f"✗ {module} - FAILED: {e}")
            all_ok = False
    
    print("\nTesting optional modules...")
    
    # Test optional modules
    try:
        import customtkinter
        version = getattr(customtkinter, '__version__', 'unknown')
        print(f"✓ customtkinter - OK (version: {version})")
        print("  Modern UI will be available")
    except ImportError:
        print("✗ customtkinter - Not installed")
        print("  Application will use basic UI")
        print("  Install with: pip install customtkinter")
    
    return all_ok

def test_disk_scanner():
    """Test basic disk scanner functionality"""
    print("\n" + "="*50)
    print("Testing DiskScanner functionality...")
    
    try:
        from disk_analyzer import DiskScanner, FileSystemNode
        print("✓ Successfully imported DiskScanner classes")
        
        # Create scanner instance
        scanner = DiskScanner("test_cache.db")
        print("✓ Created DiskScanner instance")
        
        # Test database initialization
        if os.path.exists("test_cache.db"):
            print("✓ Database created successfully")
            os.remove("test_cache.db")  # Clean up test database
        
        # Test FileSystemNode
        node = FileSystemNode("C:\\", size=1024, is_dir=True)
        print(f"✓ Created FileSystemNode: {node.name}")
        
        print("\nBasic functionality tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Error testing DiskScanner: {e}")
        return False

def test_gui_creation():
    """Test if GUI can be created (won't actually show it)"""
    print("\n" + "="*50)
    print("Testing GUI creation...")
    
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()  # Hide window
        print("✓ Tkinter window created successfully")
        root.destroy()
        
        # Test if main app can be imported
        from disk_analyzer import DiskAnalyzerGUI
        print("✓ DiskAnalyzerGUI imported successfully")
        
        print("\nGUI tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Error testing GUI: {e}")
        return False

def check_drives():
    """Check available drives on the system"""
    print("\n" + "="*50)
    print("Checking available drives...")
    
    drives = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
            try:
                # Try to get basic info
                total_size = os.path.getsize(drive)
                print(f"✓ {drive} - Accessible")
            except:
                print(f"✓ {drive} - Found (may require admin rights for full access)")
    
    if not drives:
        print("⚠ No drives found (this is unusual)")
    else:
        print(f"\nFound {len(drives)} drive(s): {', '.join(drives)}")
    
    return drives

def main():
    """Run all tests"""
    print("="*50)
    print("DiskInsight Pro - Installation Test")
    print("="*50 + "\n")
    
    # Run tests
    imports_ok = test_imports()
    scanner_ok = test_disk_scanner()
    gui_ok = test_gui_creation()
    drives = check_drives()
    
    # Summary
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    
    if imports_ok and scanner_ok and gui_ok and drives:
        print("✅ All tests passed!")
        print("\nYou can now run the application with:")
        print("  python disk_analyzer.py")
        print("\nOr simply double-click: run.bat")
    else:
        print("⚠ Some tests failed. Please check the errors above.")
        if not imports_ok:
            print("\n- Fix import issues first")
        if not scanner_ok:
            print("\n- Check disk_analyzer.py is in the same directory")
        if not gui_ok:
            print("\n- Tkinter might not be installed with your Python")
        if not drives:
            print("\n- No drives detected (unusual)")
    
    print("\nPress Enter to exit...")
    input()

if __name__ == "__main__":
    main()
