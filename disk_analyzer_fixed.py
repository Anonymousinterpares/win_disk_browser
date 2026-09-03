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
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

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

from windows_scanner import list_directory_entries, should_skip_recurse, SKIP_DIRS_SCAN
from mmap_snapshot import (
    CACHE_FORMAT_MMAP,
    save_mmap_snapshot,
    load_mmap_snapshot,
    snapshot_file_path,
    legacy_mmap_path,
    find_snapshot_for_drive,
    has_mmap_snapshot,
)
from normalized_cache import (
    ensure_cache_schema,
    save_normalized_tree,
    load_normalized_tree,
    load_pickle_tree,
    get_cache_meta,
    get_snapshot_path,
    upsert_scan_cache_meta,
    has_normalized_cache,
    _configure_cache_connection,
    CACHE_FORMAT_NORMALIZED,
)
from usn_journal import (
    query_usn_journal,
    read_usn_changes,
    resolve_changed_directories,
    MAX_USN_PATHS_BEFORE_FULL_SCAN,
    UsnJournalState,
)
from mft_scanner import scan_drive_mft, can_use_mft_scan


# Performance constants
WORKER_THREADS = min(16, os.cpu_count() * 2)  # Reduced for better control
BATCH_SIZE = 500  # Smaller batches for more frequent updates
CACHE_SIZE = 50000  # Reasonable cache size
PROGRESS_UPDATE_INTERVAL = 0.1  # Update UI every 100ms
MAX_DEPTH = 20  # Maximum recursion depth
CACHE_SAVE_DEBOUNCE_SEC = 30.0  # Debounce full snapshot writes
CACHE_METADATA_DEBOUNCE_SEC = 5.0  # Debounce live metadata-only DB updates
USE_PARALLEL_SCAN = True  # Parallel BFS on Windows when pywin32 is available
USE_MFT_SCAN = True  # NTFS MFT fast path when elevated (WizTree-class)

# Legacy alias — extended skip list lives in windows_scanner.py

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

    @staticmethod
    def finalize_dir_size(node: 'FileNode') -> int:
        """Bottom-up size aggregation; stores result in _calculated_size."""
        if not node.is_dir:
            node._calculated_size = node.size
            return node.size
        if node._calculated_size is not None:
            return node._calculated_size
        total = node.size
        for child in node.children:
            if child.is_dir:
                total += FileNode.finalize_dir_size(child)
            else:
                child._calculated_size = child.size
                total += child.size
        node._calculated_size = total
        return total

    @staticmethod
    def ensure_tree_sizes(root: Optional['FileNode']) -> None:
        """Precompute folder sizes for an entire tree (e.g. after cache load)."""
        if root and root._calculated_size is None:
            FileNode.finalize_dir_size(root)
    
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


class DebouncedCacheSaver:
    """Coalesces cache writes — metadata-only for live updates, full snapshot on scan/exit."""

    def __init__(self, scanner: 'FixedDiskScanner', debounce_sec: float = CACHE_SAVE_DEBOUNCE_SEC):
        self.scanner = scanner
        self.debounce_sec = debounce_sec
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._pending_root: Optional[FileNode] = None
        self._pending_usn: Optional[int] = None
        self._pending_usn_next: Optional[int] = None
        self._pending_full_snapshot = True

    def schedule_save(
        self,
        root_node: FileNode,
        usn: Optional[int] = None,
        usn_next: Optional[int] = None,
        full_snapshot: bool = False,
    ) -> None:
        debounce = self.debounce_sec if full_snapshot else CACHE_METADATA_DEBOUNCE_SEC
        with self._lock:
            self._pending_root = root_node
            if usn is not None:
                self._pending_usn = usn
            if usn_next is not None:
                self._pending_usn_next = usn_next
            if full_snapshot:
                self._pending_full_snapshot = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            root = self._pending_root
            usn = self._pending_usn
            usn_next = self._pending_usn_next
            full_snapshot = self._pending_full_snapshot
            self._pending_root = None
            self._pending_usn = None
            self._pending_usn_next = None
            self._pending_full_snapshot = True
            self._timer = None
        if not root:
            return
        try:
            state = query_usn_journal(root.path)
            if usn is None and state:
                usn = state.journal_id
            if usn_next is None and state:
                usn_next = state.next_usn
            if full_snapshot:
                self.scanner.save_to_cache(root, usn or 0, usn_next or 0)
                logging.info("Debounced full cache save completed")
            else:
                self.scanner.save_cache_metadata(root, usn or 0, usn_next or 0)
                logging.info("Debounced metadata-only cache update completed")
        except Exception as e:
            logging.error(f"Debounced cache save failed: {e}")

    def flush_now(self, full_snapshot: bool = True) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if full_snapshot:
                self._pending_full_snapshot = True
        self._flush()


class FixedDiskScanner:
    """Fixed disk scanner with proper size calculation and caching"""
    
    def __init__(self, db_path: str = 'disk_cache_fixed.db'):
        self.db_path = db_path
        self.progress_callback = None
        self.total_items = 0
        self.processed_items = 0
        self.last_update_time = 0
        self.event_queue = queue.Queue()
        self.cache_saver = DebouncedCacheSaver(self)
        self.root_node: Optional[FileNode] = None
        
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
                ensure_cache_schema(conn)
                conn.commit()
        except Exception as e:
            print(f"Database initialization error: {e}")

    def _resolve_drive_keys(self, path: str) -> List[str]:
        normalized = os.path.abspath(path)
        keys = [normalized]
        if len(normalized) >= 2 and normalized[1] == ':':
            alt = normalized.rstrip('\\') + '\\'
            if alt != normalized:
                keys.append(alt)
        return keys

    def _get_usn_state(self, path: str) -> Optional[UsnJournalState]:
        return query_usn_journal(path)

    def get_current_usn(self, path: str) -> int:
        """Backward-compatible: returns journal NextUsn (not journal ID)."""
        state = self._get_usn_state(path)
        return state.next_usn if state else 0
    
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
    
    def _mft_progress(self, current_path: str, items_scanned: int) -> None:
        self.processed_items = items_scanned
        self.update_progress(current_path)

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

            root_node: Optional[FileNode] = None
            if USE_MFT_SCAN and can_use_mft_scan(path):
                logging.info(f"Attempting MFT fast scan for {path}")
                root_node = scan_drive_mft(path, progress_callback=self._mft_progress)

            if not root_node:
                if USE_PARALLEL_SCAN and HAS_WIN32:
                    root_node = self._scan_directory_parallel(path)
                else:
                    root_node = self._scan_directory_recursive(path)
            
            if root_node:
                FileNode.finalize_dir_size(root_node)
                scan_time = time.time() - start_time
                total_size = root_node.get_size()
                
                logging.info(f"Scan completed in {scan_time:.2f}s")
                logging.info(f"Total size: {self.format_size(total_size)}")
                logging.info(f"Items processed: {self.processed_items:,}")
                
                # Save to cache
                try:
                    usn_state = self._get_usn_state(path)
                    journal_id = usn_state.journal_id if usn_state else 0
                    usn_next = usn_state.next_usn if usn_state else 0
                    self.save_to_cache(root_node, journal_id, usn_next)
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

    def _scan_directory_parallel(self, path: str) -> Optional[FileNode]:
        """Breadth-first parallel scan using fast Win32 directory listing."""
        root_node = FileNode(
            path=path,
            name=os.path.basename(path) or path,
            is_dir=True,
        )
        dir_queue: deque = deque([(path, root_node, 0)])
        pending: Dict = {}

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            while dir_queue or pending:
                while dir_queue and len(pending) < WORKER_THREADS * 2:
                    dir_path, parent_node, depth = dir_queue.popleft()
                    if depth > MAX_DEPTH:
                        continue
                    future = executor.submit(self._list_dir_worker, dir_path)
                    pending[future] = (parent_node, dir_path, depth)

                if not pending:
                    break

                done, _ = wait(pending.keys(), timeout=0.05, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    parent_node, dir_path, depth = pending.pop(future)
                    try:
                        entries = future.result()
                    except Exception as exc:
                        logging.debug(f"Parallel scan worker failed for {dir_path}: {exc}")
                        continue

                    if entries is None:
                        continue

                    self.update_progress(dir_path)
                    self.processed_items += 1

                    for entry in entries:
                        entry_path = os.path.join(dir_path, entry.name)
                        if entry.is_dir:
                            if entry.skip_recurse:
                                dir_size = self._get_directory_size_fast(entry_path)
                                skip_node = FileNode(
                                    path=entry_path,
                                    name=entry.name,
                                    size=dir_size,
                                    is_dir=True,
                                    parent=parent_node,
                                )
                                parent_node.children.append(skip_node)
                                parent_node.dir_count += 1
                            else:
                                child_node = FileNode(
                                    path=entry_path,
                                    name=entry.name,
                                    is_dir=True,
                                    mtime=entry.mtime,
                                    parent=parent_node,
                                )
                                parent_node.children.append(child_node)
                                parent_node.dir_count += 1
                                if depth + 1 <= MAX_DEPTH:
                                    dir_queue.append((entry_path, child_node, depth + 1))
                        else:
                            file_node = FileNode(
                                path=entry_path,
                                name=entry.name,
                                size=entry.size,
                                is_dir=False,
                                mtime=entry.mtime,
                                parent=parent_node,
                            )
                            parent_node.children.append(file_node)
                            parent_node.file_count += 1

        return root_node

    def _list_dir_worker(self, path: str):
        return list_directory_entries(path)

    def _rescan_directory(self, path: str) -> Optional[FileNode]:
        """Rescan a single directory subtree (used for incremental USN refresh)."""
        if USE_PARALLEL_SCAN and HAS_WIN32:
            return self._scan_directory_parallel(path)
        return self._scan_directory_recursive(path)

    def _apply_rescan_to_tree(self, root: FileNode, dir_path: str, fresh_node: FileNode) -> None:
        """Replace a directory node in the tree with freshly scanned data."""
        target = self.find_node_by_path(root, dir_path)
        if not target or not target.is_dir:
            return
        target.children = fresh_node.children
        target.file_count = fresh_node.file_count
        target.dir_count = fresh_node.dir_count
        target.size = fresh_node.size
        target.mtime = fresh_node.mtime
        for child in target.children:
            child.parent = target
        target.invalidate_size_cache()

    def _incremental_refresh(
        self,
        root_node: FileNode,
        path: str,
        cached_journal_id: int,
        cached_next_usn: int,
        persist_snapshot: bool = True,
    ) -> FileNode:
        state = self._get_usn_state(path)
        if not state:
            logging.info("USN journal unavailable; using cached tree")
            return root_node

        if cached_journal_id and state.journal_id != cached_journal_id:
            logging.info("USN journal recreated; performing full rescan")
            return self.scan_directory(path, use_cache=False)

        if cached_next_usn and state.next_usn <= cached_next_usn:
            logging.info("No USN changes since last cache save")
            return root_node

        changed_names, _latest = read_usn_changes(path, cached_next_usn, state.journal_id)
        if not changed_names:
            logging.info("USN advanced but no relevant records; using cached tree")
            return root_node

        dirs_to_rescan = resolve_changed_directories(
            path,
            changed_names,
            self.find_node_by_path,
            root_node,
        )

        if not dirs_to_rescan or len(dirs_to_rescan) > MAX_USN_PATHS_BEFORE_FULL_SCAN:
            logging.info(
                f"USN refresh: {len(dirs_to_rescan)} dirs changed — performing full rescan"
            )
            return self.scan_directory(path, use_cache=False)

        logging.info(f"USN incremental refresh for {len(dirs_to_rescan)} directories")
        for dir_path in sorted(dirs_to_rescan, key=len):
            self.update_progress(dir_path)
            fresh = self._rescan_directory(dir_path)
            if fresh:
                self._apply_rescan_to_tree(root_node, dir_path, fresh)

        FileNode.finalize_dir_size(root_node)
        if persist_snapshot:
            self.save_to_cache(root_node, state.journal_id, state.next_usn)
        else:
            self.save_cache_metadata(root_node, state.journal_id, state.next_usn)
        return root_node
    
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
    
    def save_cache_metadata(self, root_node: FileNode, usn_journal_id: int = 0, usn_next: int = 0):
        """Update SQLite metadata only — fast path for live monitor (no snapshot rewrite)."""
        drive = root_node.path
        snapshot_path = find_snapshot_for_drive(self.db_path, drive) or snapshot_file_path(self.db_path, drive)
        try:
            with sqlite3.connect(self.db_path) as conn:
                ensure_cache_schema(conn)
                upsert_scan_cache_meta(
                    conn,
                    drive,
                    CACHE_FORMAT_MMAP,
                    usn_journal_id,
                    usn_next,
                    snapshot_path,
                )
                conn.commit()
        except Exception as e:
            logging.error(f'Cache metadata save error: {e}')

    def save_to_cache(self, root_node: FileNode, usn_journal_id: int = 0, usn_next: int = 0):
        """Save scan results to mmap snapshot (fast load) plus SQLite metadata."""
        drive = root_node.path
        snapshot_path = snapshot_file_path(self.db_path, drive)
        try:
            save_mmap_snapshot(root_node, snapshot_path, usn_journal_id, usn_next)
            with sqlite3.connect(self.db_path) as conn:
                ensure_cache_schema(conn)
                conn.execute('DELETE FROM cache_nodes WHERE drive = ?', (drive,))
                upsert_scan_cache_meta(
                    conn,
                    drive,
                    CACHE_FORMAT_MMAP,
                    usn_journal_id,
                    usn_next,
                    snapshot_path,
                )
                conn.commit()
            logging.info(f'Saved mmap cache for {drive}')
        except Exception as e:
            logging.error(f'Mmap cache save error: {e}')
            try:
                with sqlite3.connect(self.db_path) as conn:
                    ensure_cache_schema(conn)
                    save_normalized_tree(conn, root_node, usn_journal_id, usn_next)
                    conn.commit()
                logging.info(f'Saved normalized SQLite fallback cache for {drive}')
            except Exception as fallback_error:
                logging.error(f'Cache save fallback error: {fallback_error}')
    
    def cache_exists(self, path: str) -> bool:
        """Check if a cache entry exists without deserializing scan data."""
        try:
            for key in self._resolve_drive_keys(path):
                if find_snapshot_for_drive(self.db_path, key):
                    return True
            with sqlite3.connect(self.db_path) as conn:
                ensure_cache_schema(conn)
                for key in self._resolve_drive_keys(path):
                    stored = get_snapshot_path(conn, key)
                    if stored and has_mmap_snapshot(stored):
                        return True
                    if has_normalized_cache(conn, key):
                        return True
                    cursor = conn.execute(
                        'SELECT 1 FROM scan_cache WHERE drive = ? AND data IS NOT NULL LIMIT 1',
                        (key,),
                    )
                    if cursor.fetchone():
                        return True
        except Exception as e:
            logging.error(f"Cache existence check error: {e}")
        return False

    def list_cached_drives(self) -> List[str]:
        """Return drive paths that have cached scan data (metadata only)."""
        try:
            drives: List[str] = []
            seen: Set[str] = set()
            with sqlite3.connect(self.db_path) as conn:
                ensure_cache_schema(conn)
                cursor = conn.execute(
                    'SELECT drive, snapshot_path, cache_format FROM scan_cache ORDER BY timestamp DESC'
                )
                for drive, snapshot_path, cache_format in cursor.fetchall():
                    if drive in seen:
                        continue
                    if snapshot_path and has_mmap_snapshot(snapshot_path):
                        drives.append(drive)
                        seen.add(drive)
                        continue
                    if int(cache_format or 0) == CACHE_FORMAT_MMAP and has_mmap_snapshot(
                        snapshot_file_path(self.db_path, drive)
                    ):
                        drives.append(drive)
                        seen.add(drive)
                        continue
                    if has_normalized_cache(conn, drive):
                        drives.append(drive)
                        seen.add(drive)
                        continue
                    row = conn.execute(
                        'SELECT 1 FROM scan_cache WHERE drive = ? AND data IS NOT NULL',
                        (drive,),
                    ).fetchone()
                    if row:
                        drives.append(drive)
                        seen.add(drive)
            return drives
        except Exception as e:
            logging.error(f"Cache list error: {e}")
            return []

    def load_from_cache(self, path: str) -> Optional[FileNode]:
        """Load scan results — mmap snapshot first, then SQLite/pickle fallbacks."""
        try:
            start = time.time()
            for key in self._resolve_drive_keys(path):
                snapshot_candidates: List[str] = []
                existing = find_snapshot_for_drive(self.db_path, key)
                if existing:
                    snapshot_candidates.append(existing)
                snapshot_candidates.append(snapshot_file_path(self.db_path, key))
                legacy = legacy_mmap_path(self.db_path, key)
                if legacy not in snapshot_candidates:
                    snapshot_candidates.append(legacy)
                with sqlite3.connect(self.db_path) as conn:
                    _configure_cache_connection(conn)
                    ensure_cache_schema(conn)
                    stored = get_snapshot_path(conn, key)
                    if stored and stored not in snapshot_candidates:
                        snapshot_candidates.insert(0, stored)

                for snapshot_path in snapshot_candidates:
                    if not has_mmap_snapshot(snapshot_path):
                        continue
                    root = load_mmap_snapshot(snapshot_path)
                    if not root:
                        continue
                    elapsed = time.time() - start
                    logging.info(f'Loaded snapshot cache for {key} in {elapsed:.2f}s')
                    if snapshot_path.endswith('.mmap'):
                        logging.info(f'Migrating legacy mmap cache to compressed .snap for {key}')
                        with sqlite3.connect(self.db_path) as conn:
                            _configure_cache_connection(conn)
                            ensure_cache_schema(conn)
                            meta = get_cache_meta(conn, key)
                        journal_id = meta[1] if meta else 0
                        usn_next = meta[2] if meta else 0
                        state = self._get_usn_state(key)
                        if state:
                            journal_id = state.journal_id
                            usn_next = state.next_usn
                        self.save_to_cache(root, journal_id, usn_next)
                    return root

            with sqlite3.connect(self.db_path) as conn:
                _configure_cache_connection(conn)
                ensure_cache_schema(conn)
                for key in self._resolve_drive_keys(path):
                    root = load_normalized_tree(conn, key)
                    if root:
                        elapsed = time.time() - start
                        logging.info(
                            f"Loaded normalized cache for {key} in {elapsed:.2f}s "
                            f"({len(root.children)} top-level entries)"
                        )
                        return root

                    root = load_pickle_tree(conn, key)
                    if root:
                        logging.info(f"Migrating pickle cache to mmap format for {key}")
                        meta = get_cache_meta(conn, key)
                        journal_id = meta[1] if meta else 0
                        usn_next = meta[2] if meta else 0
                        state = self._get_usn_state(key)
                        if state:
                            journal_id = state.journal_id
                            usn_next = state.next_usn
                        self.save_to_cache(root, journal_id, usn_next)
                        return root
        except Exception as e:
            logging.error(f"Cache load error: {e}")
        
        return None
    
    def sync_loaded_tree_from_usn(self, root_node: FileNode, path: str) -> FileNode:
        """Apply USN changes to an in-memory tree without rewriting the snapshot file."""
        path = os.path.abspath(path)
        if not HAS_WIN32 or not self._is_admin():
            return root_node
        try:
            with sqlite3.connect(self.db_path) as conn:
                ensure_cache_schema(conn)
                meta = None
                for key in self._resolve_drive_keys(path):
                    meta = get_cache_meta(conn, key)
                    if meta:
                        break
            cached_journal_id = meta[1] if meta else 0
            cached_next_usn = meta[2] if meta else 0
            return self._incremental_refresh(
                root_node,
                path,
                cached_journal_id,
                cached_next_usn,
                persist_snapshot=False,
            )
        except Exception as e:
            logging.error(f'In-memory USN sync failed: {e}')
            return root_node

    def refresh_from_cache(self, path: str) -> Optional[FileNode]:
        """Load from cache and incrementally refresh changed directories via USN."""
        try:
            path = os.path.abspath(path)
            root_node = self.load_from_cache(path)
            if not root_node:
                logging.info("No cache found, performing full scan")
                return self.scan_directory(path, use_cache=False)

            if HAS_WIN32 and self._is_admin():
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        ensure_cache_schema(conn)
                        meta = None
                        for key in self._resolve_drive_keys(path):
                            meta = get_cache_meta(conn, key)
                            if meta:
                                break
                    cached_journal_id = meta[1] if meta else 0
                    cached_next_usn = meta[2] if meta else 0
                    return self._incremental_refresh(
                        root_node, path, cached_journal_id, cached_next_usn
                    )
                except Exception as e:
                    logging.debug(f"USN refresh failed: {e}")

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
        """Find a node by path using breadth-first search."""
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

    def _find_node_by_path(self, target_path: str) -> Optional[FileNode]:
        """Find a node in the currently loaded tree (used by live update system)."""
        if not self.root_node:
            return None
        return self.find_node_by_path(self.root_node, target_path)


def main():
    """Main entry point - scanner functionality only"""
    print("DiskInsight Pro - Core Scanner")
    print(f"Worker threads: {WORKER_THREADS}")
    print("Note: This module provides scanning functionality.")
    print("For the visual interface, use visual_analyzer.py")


if __name__ == "__main__":
    main()