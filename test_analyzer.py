#!/usr/bin/env python3
"""Test script to verify visual analyzer functionality"""

import os
import sys
import json
from disk_analyzer_fixed import FixedDiskScanner, FileNode

def test_basic_scan():
    """Test basic disk scanning"""
    print("Testing basic disk scan...")
    
    scanner = FixedDiskScanner()
    
    # Test with a small directory
    test_path = "E:\\AI\\AI\\projects\\win_disk_browser\\webview_ui"
    
    print(f"Scanning: {test_path}")
    root_node = scanner.scan_directory(test_path, use_cache=False)
    
    if root_node:
        print(f"✓ Scan successful!")
        print(f"  Root path: {root_node.path}")
        print(f"  Total size: {root_node.get_size():,} bytes")
        print(f"  Children: {len(root_node.children)}")
        
        # Test LOD view generation
        from visual_analyzer import Api, NaniteLODSystem
        
        api = Api()
        api._scan_result_root = root_node
        
        # Test adaptive LOD view
        lod_view = api.get_adaptive_lod_view(test_path, zoom=1.0)
        
        if lod_view:
            print("✓ LOD view generated successfully!")
            print(f"  LOD data keys: {lod_view.keys()}")
            
            # Convert to JSON to test serialization
            try:
                json_str = json.dumps(lod_view)
                print(f"✓ JSON serialization successful! ({len(json_str)} chars)")
            except Exception as e:
                print(f"✗ JSON serialization failed: {e}")
        else:
            print("✗ LOD view generation failed!")
            
    else:
        print("✗ Scan failed!")
        
    return root_node is not None

def test_sunburst_view():
    """Test Sunburst view generation"""
    print("\nTesting Sunburst view...")
    
    scanner = FixedDiskScanner()
    test_path = "E:\\AI\\AI\\projects\\win_disk_browser"
    
    print(f"Scanning: {test_path}")
    root_node = scanner.scan_directory(test_path, use_cache=False)
    
    if root_node:
        from visual_analyzer import Api
        
        api = Api()
        api._scan_result_root = root_node
        
        # Test sunburst view
        sunburst_view = api.get_sunburst_adaptive_view(test_path, max_depth=3)
        
        if sunburst_view:
            print("✓ Sunburst view generated successfully!")
            print(f"  Data keys: {sunburst_view.keys()}")
            if 'children' in sunburst_view:
                print(f"  Children count: {len(sunburst_view['children'])}")
        else:
            print("✗ Sunburst view generation failed!")
            
    return root_node is not None

if __name__ == "__main__":
    print("Visual Analyzer Test Suite")
    print("=" * 40)
    
    success = True
    
    try:
        success = test_basic_scan() and success
        success = test_sunburst_view() and success
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    print("\n" + "=" * 40)
    if success:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed!")
    
    sys.exit(0 if success else 1)
