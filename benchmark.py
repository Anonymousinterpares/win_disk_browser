"""
Performance Comparison Script
Compare original vs optimized disk scanner performance
"""

import os
import time
import sys
import tempfile
import shutil
from pathlib import Path

def create_test_directory(base_path: str, num_dirs: int = 100, files_per_dir: int = 50):
    """Create a test directory structure for benchmarking"""
    print(f"Creating test structure: {num_dirs} dirs, {files_per_dir} files each...")
    
    test_dir = os.path.join(base_path, "benchmark_test")
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    
    os.makedirs(test_dir)
    
    # Create directory structure
    for i in range(num_dirs):
        dir_path = os.path.join(test_dir, f"dir_{i:03d}")
        os.makedirs(dir_path)
        
        # Create files in each directory
        for j in range(files_per_dir):
            file_path = os.path.join(dir_path, f"file_{j:03d}.txt")
            with open(file_path, 'w') as f:
                f.write("x" * 1024)  # 1KB file
                
        # Create subdirectories
        if i % 10 == 0:
            for k in range(5):
                subdir = os.path.join(dir_path, f"subdir_{k}")
                os.makedirs(subdir)
                for l in range(10):
                    file_path = os.path.join(subdir, f"subfile_{l}.txt")
                    with open(file_path, 'w') as f:
                        f.write("y" * 512)
    
    print(f"Test directory created: {test_dir}")
    return test_dir

def benchmark_original_scanner(test_path: str):
    """Benchmark the original scanner"""
    try:
        from disk_analyzer import DiskScanner
        
        print("\n" + "="*50)
        print("Benchmarking ORIGINAL Scanner")
        print("="*50)
        
        scanner = DiskScanner()
        
        start_time = time.time()
        result = scanner.scan_directory(test_path, use_cache=False)
        end_time = time.time()
        
        elapsed = end_time - start_time
        
        print(f"Original Scanner Results:")
        print(f"  Time: {elapsed:.2f} seconds")
        print(f"  Items scanned: {scanner.scanned_items}")
        print(f"  Total size: {scanner.total_size:,} bytes")
        if elapsed > 0:
            print(f"  Speed: {scanner.scanned_items / elapsed:.0f} items/second")
        
        return elapsed, scanner.scanned_items
        
    except ImportError:
        print("Original scanner not found")
        return None, None

def benchmark_optimized_scanner(test_path: str):
    """Benchmark the optimized scanner"""
    try:
        from disk_analyzer_optimized import OptimizedDiskScanner
        
        print("\n" + "="*50)
        print("Benchmarking OPTIMIZED Scanner")
        print("="*50)
        
        scanner = OptimizedDiskScanner()
        
        start_time = time.time()
        result = scanner.scan_directory_parallel(test_path)
        end_time = time.time()
        
        elapsed = end_time - start_time
        
        print(f"Optimized Scanner Results:")
        print(f"  Time: {elapsed:.2f} seconds")
        print(f"  Items scanned: {scanner.scanned_items}")
        print(f"  Total size: {scanner.total_size:,} bytes")
        if elapsed > 0:
            print(f"  Speed: {scanner.scanned_items / elapsed:.0f} items/second")
        print(f"  Worker threads used: {scanner.executor._max_workers}")
        
        return elapsed, scanner.scanned_items
        
    except ImportError as e:
        print(f"Optimized scanner not found: {e}")
        return None, None

def benchmark_real_directory(path: str):
    """Benchmark on a real directory"""
    print(f"\nBenchmarking on real directory: {path}")
    print("="*60)
    
    # Count items first
    total_items = sum(1 for _ in Path(path).rglob("*"))
    print(f"Directory contains approximately {total_items:,} items")
    
    # Benchmark original
    orig_time, orig_items = benchmark_original_scanner(path)
    
    # Benchmark optimized
    opt_time, opt_items = benchmark_optimized_scanner(path)
    
    # Compare results
    if orig_time and opt_time:
        print("\n" + "="*50)
        print("PERFORMANCE COMPARISON")
        print("="*50)
        
        speedup = orig_time / opt_time
        print(f"Original time: {orig_time:.2f}s")
        print(f"Optimized time: {opt_time:.2f}s")
        print(f"SPEEDUP: {speedup:.1f}x faster!")
        
        if orig_items and opt_items:
            orig_speed = orig_items / orig_time if orig_time > 0 else 0
            opt_speed = opt_items / opt_time if opt_time > 0 else 0
            print(f"\nScanning speed:")
            print(f"  Original: {orig_speed:.0f} items/second")
            print(f"  Optimized: {opt_speed:.0f} items/second")
            print(f"  Improvement: {(opt_speed/orig_speed):.1f}x faster")

def main():
    """Main benchmark function"""
    print("="*60)
    print("DiskInsight Pro - Performance Benchmark")
    print("="*60)
    
    # Check if modules are available
    print("\nChecking available modules...")
    
    try:
        import win32file
        print("✓ pywin32 installed - Maximum optimization available")
    except:
        print("✗ pywin32 not installed - Using standard Python methods")
        print("  Install with: pip install pywin32")
    
    try:
        import customtkinter
        print("✓ customtkinter installed")
    except:
        print("✗ customtkinter not installed")
    
    # Create test directory
    print("\n" + "="*60)
    print("Test 1: Synthetic Benchmark")
    print("="*60)
    
    temp_dir = tempfile.gettempdir()
    test_path = create_test_directory(temp_dir, num_dirs=50, files_per_dir=20)
    
    try:
        # Run benchmarks
        orig_time, orig_items = benchmark_original_scanner(test_path)
        opt_time, opt_items = benchmark_optimized_scanner(test_path)
        
        # Compare
        if orig_time and opt_time:
            print("\n" + "="*50)
            print("SYNTHETIC BENCHMARK RESULTS")
            print("="*50)
            speedup = orig_time / opt_time
            print(f"Speedup: {speedup:.1f}x faster")
            
            if speedup > 10:
                print("🚀 MASSIVE performance improvement!")
            elif speedup > 5:
                print("⚡ Excellent performance improvement!")
            elif speedup > 2:
                print("✓ Good performance improvement!")
            else:
                print("✓ Performance improved")
    
    finally:
        # Cleanup
        if os.path.exists(test_path):
            shutil.rmtree(test_path)
    
    # Test on real directory
    print("\n" + "="*60)
    print("Test 2: Real Directory Benchmark")
    print("="*60)
    
    # Test on Python installation directory (usually has many files)
    python_dir = os.path.dirname(sys.executable)
    lib_dir = os.path.join(python_dir, "Lib")
    
    if os.path.exists(lib_dir):
        print(f"Testing on Python Lib directory: {lib_dir}")
        benchmark_real_directory(lib_dir)
    else:
        # Test on current directory
        current_dir = os.getcwd()
        print(f"Testing on current directory: {current_dir}")
        benchmark_real_directory(current_dir)
    
    print("\n" + "="*60)
    print("Benchmark Complete!")
    print("="*60)
    print("\nTo run the optimized version:")
    print("  run_optimized.bat")
    print("\nTo run the original version:")
    print("  run.bat")
    
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
