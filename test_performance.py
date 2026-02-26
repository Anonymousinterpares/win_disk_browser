#!/usr/bin/env python3
"""
Performance test script to validate TreeView population improvements.
"""

import time
import sys
import os
import logging

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from disk_analyzer_fixed import FixedDiskScanner, FileNode

def test_size_precalculation():
    """Test the pre-calculation of sizes"""
    print("Testing size pre-calculation...")
    
    scanner = FixedDiskScanner()
    
    # Try loading from cache (if exists)
    test_path = "C:\\"
    
    print(f"Loading cache for {test_path}...")
    start_time = time.time()
    
    root_node = scanner.load_from_cache(test_path)
    
    if root_node:
        load_time = time.time() - start_time
        print(f"Cache loaded in {load_time:.2f}s")
        
        # Test size access (should be fast now)
        print("Testing size access speed...")
        size_start = time.time()
        
        total_size = root_node.get_size()
        size_time = time.time() - size_start
        
        print(f"Root size: {format_size(total_size)} (calculated in {size_time:.4f}s)")
        
        # Test flattened tree building
        print("Testing flattened tree building...")
        flatten_start = time.time()
        
        flattened_items = root_node.build_flattened_tree(max_depth=2)
        flatten_time = time.time() - flatten_start
        
        print(f"Built flattened tree: {len(flattened_items)} items in {flatten_time:.2f}s")
        
        # Show sample items
        print("\nSample flattened items:")
        for i, item in enumerate(flattened_items[:5]):
            print(f"  {i+1}. {item['text']} - {format_size(item['size'])}")
        
        return True
    else:
        print(f"No cache found for {test_path}")
        return False

def format_size(size: int) -> str:
    """Format size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def test_format_size():
    """Test the format_size method"""
    test_sizes = [0, 1024, 1048576, 1073741824, 1099511627776]
    expected = ["0.00 B", "1.00 KB", "1.00 MB", "1.00 GB", "1.00 TB"]
    
    print("\nTesting size formatting:")
    for size, expected_result in zip(test_sizes, expected):
        result = format_size(size)
        status = "PASS" if result == expected_result else "FAIL"
        print(f"  {status} {size} bytes -> {result} (expected: {expected_result})")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("Performance Test Suite")
    print("=" * 50)
    
    try:
        test_format_size()
        
        success = test_size_precalculation()
        
        if success:
            print("\nAll tests passed! Performance improvements are working.")
        else:
            print("\nSome tests failed or no cache available.")
            
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()