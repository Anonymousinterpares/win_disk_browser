"""
DiskInsight Pro - FIXED VERSION
Corrected implementation with proper size calculation and caching
"""

import logging
import os
import struct
import sys
import time
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque
from typing import Dict, List, Tuple, Optional, Set
import queue
import pickle
import hashlib
import multiprocessing
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import queue
import win32file
import win32api
import winioctlcon
import pywintypes
from dataclasses import dataclass, field

# Import shared live update system
try:
    from live_update_system import LiveUpdateManager
    HAS_LIVE_UPDATES = True
except ImportError:
    HAS_LIVE_UPDATES = False
    print("Live update system not available")

# Try to import Windows-specific modules for faster scanning
try:
    import win32file
    import win32api
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("pywin32 not installed. Using standard Python methods.")
    print("Install with: pip install pywin32 for better performance")


# Performance constants
WORKER_THREADS = min(16, os.cpu_count() * 2)  # Reduced for better control
BATCH_SIZE = 500  # Smaller batches for more frequent updates
CACHE_SIZE = 50000  # Reasonable cache size
PROGRESS_UPDATE_INTERVAL = 0.1  # Update UI every 100ms
MAX_DEPTH = 20  # Maximum recursion depth

# Directories to skip for performance (but still count their size)
SKIP_DIRS_SCAN = {
    '$RECYCLE.BIN', 'System Volume Information', 'Recovery', 
    'Windows.old', 'Config.Msi'
}

@dataclass
class FileNode:
    """File/Directory node with proper size tracking"""
    path: str
    name: str
    size: int = 0
    is_dir: bool = False
    mtime: float = 0.0
    file_count: int = 0
    dir_count: int = 0
    children: List['FileNode'] = field(default_factory=list)
    parent: Optional['FileNode'] = None
    _calculated_size: Optional[int] = None
    frn: int = 0

    def get_size(self) -> int:
        """Get the total size including all children using a safe, iterative method."""
        if not self.is_dir:
            return self.size
        
        if self._calculated_size is not None:
            return self._calculated_size
        
        # Use iterative approach to avoid stack overflow
        total_size = self.size
        stack = list(self.children)
        
        while stack:
            node = stack.pop()
            if node.is_dir:
                total_size += node.size  # Add directory's own size
                stack.extend(node.children)
            else:
                total_size += node.size
        
        self._calculated_size = total_size
        return total_size
    
    def invalidate_size_cache(self):
        """Invalidate the cached size calculation"""
        self._calculated_size = None
        # Propagate up to parent
        if self.parent:
            self.parent.invalidate_size_cache()
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            'path': self.path,
            'name': self.name,
            'size': self.size,
            'is_dir': self.is_dir,
            'mtime': self.mtime,
            'file_count': self.file_count,
            'dir_count': self.dir_count,
            'children': [child.to_dict() for child in self.children],
            'frn': self.frn
        }
    
    @classmethod
    def from_dict(cls, data: dict, parent=None) -> 'FileNode':
        """Create from dictionary"""
        node = cls(
            path=data['path'],
            name=data['name'],
            size=data['size'],
            is_dir=data['is_dir'],
            mtime=data['mtime'],
            file_count=data['file_count'],
            dir_count=data['dir_count'],
            parent=parent,
            frn=data.get('frn', 0)
        )
        
        # Recursively create children
        for child_data in data['children']:
            child = cls.from_dict(child_data, parent=node)
            node.children.append(child)
        
        return node


class FixedDiskScanner:
    """Fixed disk scanner with proper size calculation and caching"""
    
    def __init__(self, db_path: str = 'disk_cache_fixed.db'):
        self.db_path = db_path
        self.progress_callback = None
        self.total_items = 0
        self.processed_items = 0
        self.last_update_time = 0
        self.event_queue = queue.Queue()
        
        # Initialize database
        self.init_database()
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filename='analyzer.log',
            filemode='w'
        )
    
    def init_database(self):
        """Initialize the SQLite database for caching"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS scan_cache (
                        drive TEXT PRIMARY KEY,
                        data BLOB,
                        timestamp INTEGER,
                        usn_journal_id INTEGER
                    )
                ''')
                conn.commit()
        except Exception as e:
            print(f"Database initialization error: {e}")
    
    def set_progress_callback(self, callback):
        """Set callback for progress updates"""
        self.progress_callback = callback
    
    def update_progress(self, current_path: str):
        """Update progress if callback is set"""
        if self.progress_callback:
            current_time = time.time()
            if current_time - self.last_update_time >= PROGRESS_UPDATE_INTERVAL:
                self.progress_callback(current_path, self.processed_items)
                self.last_update_time = current_time
    
    def scan_directory(self, path: str, use_cache: bool = True) -> Optional[FileNode]:
        """
        Scan directory with proper error handling and caching
        
        Args:
            path: Root path to scan
            use_cache: Whether to use cached data if available
            
        Returns:
            FileNode representing the directory tree
        """
        try:
            # Normalize path
            path = os.path.abspath(path)
            
            # Try cache first if requested
            if use_cache:
                cached_data = self.load_from_cache(path)
                if cached_data:
                    logging.info(f"Using cached data for {path}")
                    return cached_data
            
            logging.info(f"Starting fresh scan of {path}")
            start_time = time.time()
            
            # Reset counters
            self.processed_items = 0
            self.total_items = 0
            
            # Perform scan
            root_node = self._scan_directory_recursive(path)
            
            if root_node:
                scan_time = time.time() - start_time
                total_size = root_node.get_size()
                
                logging.info(f"Scan completed in {scan_time:.2f}s")
                logging.info(f"Total size: {self.format_size(total_size)}")
                logging.info(f"Items processed: {self.processed_items:,}")
                
                # Save to cache
                try:
                    usn = self.get_current_usn(path)
                    self.save_to_cache(root_node, usn)
                    logging.info("Data saved to cache")
                except Exception as e:
                    logging.warning(f"Failed to save cache: {e}")
                
                return root_node
            
        except Exception as e:
            logging.error(f"Scan error for {path}: {e}", exc_info=True)
            return None
    
    def _scan_directory_recursive(self, path: str, depth: int = 0) -> Optional[FileNode]:
        """Recursive directory scanning with proper error handling"""
        try:
            if depth > MAX_DEPTH:
                logging.warning(f"Maximum depth reached at {path}")
                return None
            
            # Create node for this directory
            node = FileNode(
                path=path,
                name=os.path.basename(path) or path,
                is_dir=True
            )
            
            self.update_progress(path)
            self.processed_items += 1
            
            try:
                # Get directory entries
                with os.scandir(path) as entries:
                    for entry in entries:
                        try:
                            entry_path = entry.path
                            
                            if entry.is_file(follow_symlinks=False):
                                # File handling
                                try:
                                    stat_result = entry.stat()
                                    file_node = FileNode(
                                        path=entry_path,
                                        name=entry.name,
                                        size=stat_result.st_size,
                                        is_dir=False,
                                        mtime=stat_result.st_mtime,
                                        parent=node
                                    )
                                    node.children.append(file_node)
                                    node.file_count += 1
                                    
                                except OSError:
                                    # Skip files we can't access
                                    continue
                                    
                            elif entry.is_dir(follow_symlinks=False):
                                # Directory handling
                                dir_name = entry.name
                                
                                # Skip certain directories for performance
                                if dir_name in SKIP_DIRS_SCAN:
                                    # Still count the size but don't recurse
                                    try:
                                        dir_size = self._get_directory_size_fast(entry_path)
                                        skip_node = FileNode(
                                            path=entry_path,
                                            name=dir_name,
                                            size=dir_size,
                                            is_dir=True,
                                            parent=node
                                        )
                                        node.children.append(skip_node)
                                        node.dir_count += 1
                                    except:
                                        continue
                                else:
                                    # Recursively scan subdirectory
                                    child_node = self._scan_directory_recursive(
                                        entry_path, depth + 1
                                    )
                                    if child_node:
                                        child_node.parent = node
                                        node.children.append(child_node)
                                        node.dir_count += 1
                                        
                        except (PermissionError, FileNotFoundError, OSError):
                            # Skip entries we can't access
                            continue
                            
            except (PermissionError, FileNotFoundError, OSError) as e:
                logging.warning(f"Cannot access directory {path}: {e}")
                return node  # Return partial node
            
            return node
            
        except Exception as e:
            logging.error(f"Error scanning {path}: {e}")
            return None
    
    def _get_directory_size_fast(self, path: str) -> int:
        """Get directory size quickly without full recursion"""
        total_size = 0
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total_size += entry.stat().st_size
                        elif entry.is_dir(follow_symlinks=False):
                            # Quick size estimate for subdirectories
                            total_size += self._estimate_dir_size(entry.path)
                    except:
                        continue
        except:
            pass
        return total_size
    
    def _estimate_dir_size(self, path: str) -> int:
        """Quick directory size estimation"""
        total = 0
        try:
            count = 0
            with os.scandir(path) as entries:
                for entry in entries:
                    if count > 100:  # Limit for speed
                        break
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat().st_size
                        count += 1
                    except:
                        continue
        except:
            pass
        return total
    
    def save_to_cache(self, root_node: FileNode, usn_journal_id: int = 0):
        """Save scan results to cache"""
        try:
            # Serialize the tree
            data = pickle.dumps(root_node)
            timestamp = int(time.time())
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO scan_cache (drive, data, timestamp, usn_journal_id) VALUES (?, ?, ?, ?)',
                    (root_node.path, data, timestamp, usn_journal_id)
                )
                conn.commit()
                
        except Exception as e:
            logging.error(f"Cache save error: {e}")
    
    def cache_exists(self, path: str) -> bool:
        """Check if a cache entry exists without deserializing scan data."""
        try:
            normalized = os.path.abspath(path)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    'SELECT 1 FROM scan_cache WHERE drive = ? LIMIT 1',
                    (normalized,)
                )
                if cursor.fetchone():
                    return True
                # Legacy rows may use alternate path forms (e.g. C:\ vs C:)
                if len(normalized) >= 2 and normalized[1] == ':':
                    alt = normalized.rstrip('\\') + '\\'
                    if alt != normalized:
                        cursor = conn.execute(
                            'SELECT 1 FROM scan_cache WHERE drive = ? LIMIT 1',
                            (alt,),
                        )
                        return cursor.fetchone() is not None
        except Exception as e:
            logging.error(f"Cache existence check error: {e}")
        return False

    def list_cached_drives(self) -> List[str]:
        """Return drive paths that have cached scan data (metadata only)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('SELECT drive FROM scan_cache ORDER BY timestamp DESC')
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Cache list error: {e}")
            return []

    def load_from_cache(self, path: str) -> Optional[FileNode]:
        """Load scan results from cache"""
        try:
            normalized = os.path.abspath(path)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    'SELECT data FROM scan_cache WHERE drive = ?',
                    (normalized,)
                )
                result = cursor.fetchone()
                if not result and len(normalized) >= 2 and normalized[1] == ':':
                    alt = normalized.rstrip('\\') + '\\'
                    if alt != normalized:
                        cursor = conn.execute(
                            'SELECT data FROM scan_cache WHERE drive = ?',
                            (alt,),
                        )
                        result = cursor.fetchone()

                if result:
                    return pickle.loads(result[0])
                    
        except Exception as e:
            logging.error(f"Cache load error: {e}")
        
        return None
    
    def get_current_usn(self, path: str) -> int:
        """Get current USN journal ID for a drive (Windows specific)"""
        try:
            if not HAS_WIN32:
                return 0
                
            drive_letter = path[0] if len(path) > 0 else 'C'
            volume_path = f"\\\\.\\{drive_letter}:"
            
            handle = win32file.CreateFile(
                volume_path,
                win32file.GENERIC_READ,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            
            try:
                # Query USN Journal
                usn_info = win32file.DeviceIoControl(
                    handle,
                    winioctlcon.FSCTL_QUERY_USN_JOURNAL,
                    None,
                    1024
                )
                
                if len(usn_info) >= 8:
                    return struct.unpack('<Q', usn_info[:8])[0]
                    
            finally:
                win32file.CloseHandle(handle)
                
        except Exception as e:
            logging.debug(f"USN query failed: {e}")
            
        return 0
    
    def refresh_from_cache(self, path: str) -> Optional[FileNode]:
        """Load from cache or perform incremental refresh"""
        try:
            # First try to load from cache
            root_node = self.load_from_cache(path)
            if not root_node:
                logging.info("No cache found, performing full scan")
                return self.scan_directory(path, use_cache=False)
            
            # Try USN-based refresh if we have admin rights and it's Windows
            if HAS_WIN32 and self._is_admin():
                try:
                    current_usn = self.get_current_usn(path)
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.execute(
                            'SELECT usn_journal_id FROM scan_cache WHERE drive = ?',
                            (path,)
                        )
                        result = cursor.fetchone()
                        cached_usn = result[0] if result else 0
                    
                    if current_usn > cached_usn:
                        logging.info("Changes detected via USN journal, refreshing cache")
                        return self.scan_directory(path, use_cache=False)
                    else:
                        logging.info("No changes detected, using cached data")
                        return root_node
                        
                except Exception as e:
                    logging.debug(f"USN refresh failed: {e}")
            
            # Fallback: return cached data
            logging.info("Using cached data (no USN refresh available)")
            return root_node
            
        except Exception as e:
            logging.error(f"Cache refresh error: {e}")
            return None
    
    def _is_admin(self) -> bool:
        """Check if running with administrator privileges"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    
    def format_size(self, size_bytes: int) -> str:
        """Format size in human readable format"""
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
    
    def add_event_to_queue(self, path: str):
        """Add file system event to queue for processing"""
        self.event_queue.put(path)
    
    def find_node_by_path(self, root: FileNode, target_path: str) -> Optional[FileNode]:
        """Find a node by path using breadth-first search"""
        if not root:
            return None
            
        # Normalize paths for comparison
        target_path = os.path.normpath(target_path).lower()
        
        q = deque([root])
        while q:
            current = q.popleft()
            if os.path.normpath(current.path).lower() == target_path:
                return current
            # Optimization: only traverse children if the path could be inside
            if target_path.startswith(os.path.normpath(current.path).lower()):
                for child in current.children:
                    q.append(child)
        return None


def main():
    """Main entry point - scanner functionality only"""
    print("DiskInsight Pro - Core Scanner")
    print(f"Worker threads: {WORKER_THREADS}")
    print("Note: This module provides scanning functionality.")
    print("For the visual interface, use visual_analyzer.py")


if __name__ == "__main__":
    main()