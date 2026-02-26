"""
DiskInsight Pro - OPTIMIZED VERSION
Ultra-fast disk space analyzer with parallel processing and advanced optimizations
"""

import os
import sys
import time
import json
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque
from typing import Dict, List, Tuple, Optional, Set
import queue
import pickle
import hashlib
import random
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import struct

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

# Try to import customtkinter for modern UI
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    USE_CTK = True
except ImportError:
    USE_CTK = False

# Performance constants
WORKER_THREADS = min(32, os.cpu_count() * 4)  # More workers for I/O bound operations
BATCH_SIZE = 1000  # Database batch size
CACHE_SIZE = 100000  # In-memory cache size
PROGRESS_UPDATE_INTERVAL = 0.1  # Update UI every 100ms
SAMPLE_SIZE = 100  # For statistical sampling
MAX_DEPTH = 20  # Maximum recursion depth

# Directories to skip for performance
SKIP_DIRS = {
    '$RECYCLE.BIN', 'System Volume Information', 'Recovery', '$Windows.~BT',
    '$Windows.~WS', 'Windows.old', 'PerfLogs', 'Config.Msi',
    'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'target', 'build', 'dist', 'out', 'bin', 'obj',
    '.gradle', '.m2', 'bower_components', 'vendor', 'packages',
    'AppData\\Local\\Temp', 'AppData\\Local\\Microsoft\\Windows\\INetCache'
}

# File extensions to deprioritize
SKIP_EXTENSIONS = {'.tmp', '.temp', '.cache', '.log', '.old', '.bak', '.swp'}

@dataclass
class FastFileNode:
    """Optimized file node with minimal memory footprint"""
    path: str
    size: int = 0
    is_dir: bool = False
    file_count: int = 0
    dir_count: int = 0
    children: List['FastFileNode'] = field(default_factory=list)
    _name: str = None
    
    @property
    def name(self):
        if self._name is None:
            self._name = os.path.basename(self.path) or self.path
        return self._name
    
    def get_total_size(self):
        if not self.is_dir:
            return self.size
        return self.size  # Size already calculated during scan

class MemoryCache:
    """High-performance in-memory cache"""
    def __init__(self, max_size: int = CACHE_SIZE):
        self.cache = {}
        self.max_size = max_size
        self.access_count = defaultdict(int)
        self.lock = threading.RLock()
        
    def get(self, key: str) -> Optional[dict]:
        with self.lock:
            if key in self.cache:
                self.access_count[key] += 1
                return self.cache[key]
        return None
        
    def put(self, key: str, value: dict):
        with self.lock:
            if len(self.cache) >= self.max_size:
                # Evict least recently used
                lru_key = min(self.access_count.keys(), 
                            key=lambda k: self.access_count[k])
                del self.cache[lru_key]
                del self.access_count[lru_key]
            self.cache[key] = value
            
    def batch_put(self, items: List[Tuple[str, dict]]):
        with self.lock:
            for key, value in items:
                self.put(key, value)

class WindowsAPIScanner:
    """Use Windows API directly for faster scanning"""
    
    @staticmethod
    def fast_listdir(path: str) -> List[Tuple[str, int, bool]]:
        """Ultra-fast directory listing using Windows API"""
        if not HAS_WIN32:
            return WindowsAPIScanner._fallback_listdir(path)
            
        try:
            results = []
            for file_data in win32file.FindFilesW(os.path.join(path, "*")):
                filename = file_data[8]
                if filename in ('.', '..'):
                    continue
                    
                file_attributes = file_data[0]
                file_size_high = file_data[4]
                file_size_low = file_data[5]
                file_size = (file_size_high << 32) + file_size_low
                is_directory = bool(file_attributes & win32con.FILE_ATTRIBUTE_DIRECTORY)
                
                results.append((filename, file_size, is_directory))
            return results
        except:
            return WindowsAPIScanner._fallback_listdir(path)
    
    @staticmethod
    def _fallback_listdir(path: str) -> List[Tuple[str, int, bool]]:
        """Fallback using standard Python"""
        results = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        results.append((
                            entry.name,
                            stat.st_size if not entry.is_dir() else 0,
                            entry.is_dir()
                        ))
                    except:
                        continue
        except:
            pass
        return results

class OptimizedDiskScanner:
    """High-performance disk scanner with multiple optimization strategies"""
    
    def __init__(self, db_path: str = "disk_cache_optimized.db"):
        self.db_path = db_path
        self.memory_cache = MemoryCache()
        self.is_scanning = False
        self.should_stop = False
        self.scanned_items = 0
        self.total_size = 0
        self.errors = []
        self.pending_cache_writes = []
        self.cache_lock = threading.Lock()
        self.progress_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=WORKER_THREADS)
        self.init_database()
        self.load_cache_to_memory()
        
    def init_database(self):
        """Initialize SQLite database with optimizations"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL")  # Write-ahead logging
        conn.execute("PRAGMA synchronous = NORMAL")  # Faster writes
        conn.execute("PRAGMA cache_size = 10000")  # Larger cache
        conn.execute("PRAGMA temp_store = MEMORY")  # Use memory for temp tables
        
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_cache (
                path TEXT PRIMARY KEY,
                size INTEGER,
                is_dir INTEGER,
                last_modified REAL,
                scan_time REAL,
                file_count INTEGER,
                dir_count INTEGER,
                checksum TEXT
            ) WITHOUT ROWID
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_scan_time ON scan_cache(scan_time)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_size ON scan_cache(size DESC)
        ''')
        conn.commit()
        conn.close()
        
    def load_cache_to_memory(self):
        """Load recent cache entries into memory for fast access"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Load most recent entries
            cursor.execute('''
                SELECT path, size, is_dir, file_count, dir_count, checksum
                FROM scan_cache
                ORDER BY scan_time DESC
                LIMIT ?
            ''', (CACHE_SIZE,))
            
            for row in cursor.fetchall():
                self.memory_cache.put(row[0], {
                    'size': row[1],
                    'is_dir': row[2],
                    'file_count': row[3],
                    'dir_count': row[4],
                    'checksum': row[5]
                })
            conn.close()
        except:
            pass  # Cache loading is optional
            
    def should_skip_path(self, path: str, name: str) -> bool:
        """Determine if path should be skipped for performance"""
        # Skip system and temporary directories
        if name in SKIP_DIRS:
            return True
            
        # Skip hidden and system files on Windows
        try:
            attrs = win32api.GetFileAttributes(path) if HAS_WIN32 else 0
            if HAS_WIN32 and (attrs & win32con.FILE_ATTRIBUTE_HIDDEN or 
                             attrs & win32con.FILE_ATTRIBUTE_SYSTEM):
                # Skip hidden/system files but still count them
                return True
        except:
            pass
            
        # Skip by extension
        if any(name.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
            return True
            
        return False
        
    def scan_directory_parallel(self, root_path: str, callback=None) -> FastFileNode:
        """Main parallel scanning method"""
        self.is_scanning = True
        self.should_stop = False
        self.scanned_items = 0
        self.total_size = 0
        self.errors = []
        
        # Start progress reporter thread
        progress_thread = threading.Thread(
            target=self._progress_reporter,
            args=(callback,),
            daemon=True
        )
        progress_thread.start()
        
        # Start cache writer thread
        cache_thread = threading.Thread(
            target=self._cache_writer,
            daemon=True
        )
        cache_thread.start()
        
        # Perform scan
        root_node = self._scan_parallel_bfs(root_path)
        
        # Cleanup
        self.is_scanning = False
        self.progress_queue.put(None)  # Signal progress thread to stop
        self._flush_cache()
        
        return root_node
        
    def _scan_parallel_bfs(self, root_path: str) -> FastFileNode:
        """Breadth-first parallel scanning"""
        root_node = FastFileNode(root_path, is_dir=True)
        
        # Queue for directories to process
        dir_queue = deque([(root_path, root_node, 0)])
        futures = []
        
        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            while dir_queue or futures:
                # Submit new work
                while dir_queue and len(futures) < WORKER_THREADS:
                    if self.should_stop:
                        break
                        
                    path, parent_node, depth = dir_queue.popleft()
                    
                    if depth > MAX_DEPTH:
                        continue
                        
                    future = executor.submit(self._scan_single_directory, path, depth)
                    futures.append((future, parent_node, path, depth))
                
                # Process completed work
                if futures:
                    # Wait for at least one to complete
                    done_futures = []
                    for future_tuple in futures[:]:
                        future, parent_node, path, depth = future_tuple
                        if future.done():
                            done_futures.append(future_tuple)
                            futures.remove(future_tuple)
                    
                    for future, parent_node, path, depth in done_futures:
                        try:
                            result = future.result(timeout=0.1)
                            if result:
                                items, total_size, file_count, dir_count, subdirs = result
                                
                                # Update parent node
                                parent_node.size += total_size
                                parent_node.file_count += file_count
                                parent_node.dir_count += dir_count
                                
                                # Add child nodes
                                for item_name, item_size, is_dir in items:
                                    item_path = os.path.join(path, item_name)
                                    child_node = FastFileNode(
                                        item_path,
                                        size=item_size,
                                        is_dir=is_dir
                                    )
                                    parent_node.children.append(child_node)
                                    
                                    if is_dir:
                                        # Add to queue for processing
                                        dir_queue.append((item_path, child_node, depth + 1))
                                
                                # Update progress
                                self.scanned_items += 1
                                self.total_size += total_size
                                self.progress_queue.put((path, self.scanned_items, self.total_size))
                                
                        except Exception as e:
                            self.errors.append(f"Error scanning {path}: {str(e)}")
                    
                    # Small delay to prevent CPU spinning
                    if not done_futures:
                        time.sleep(0.01)
        
        return root_node
        
    def _scan_single_directory(self, path: str, depth: int) -> Optional[Tuple]:
        """Scan a single directory (runs in thread pool)"""
        if self.should_stop:
            return None
            
        try:
            # Check memory cache first
            cache_key = path
            cached = self.memory_cache.get(cache_key)
            if cached and self._is_cache_valid(path, cached):
                return ([], cached['size'], cached['file_count'], 
                       cached['dir_count'], [])
            
            # Fast directory listing
            entries = WindowsAPIScanner.fast_listdir(path)
            
            # Process entries
            items = []
            subdirs = []
            total_size = 0
            file_count = 0
            dir_count = 0
            
            for name, size, is_dir in entries:
                if self.should_stop:
                    break
                    
                item_path = os.path.join(path, name)
                
                # Apply smart filtering
                if self.should_skip_path(item_path, name):
                    continue
                    
                if is_dir:
                    subdirs.append(item_path)
                    dir_count += 1
                else:
                    total_size += size
                    file_count += 1
                    
                items.append((name, size, is_dir))
            
            # Use sampling for very large directories
            if len(items) > 10000:
                items, total_size = self._sample_large_directory(path, items)
            
            # Queue cache write
            self._queue_cache_write(path, total_size, file_count, dir_count)
            
            return (items, total_size, file_count, dir_count, subdirs)
            
        except Exception as e:
            self.errors.append(f"Cannot scan {path}: {str(e)}")
            return None
            
    def _sample_large_directory(self, path: str, items: List) -> Tuple[List, int]:
        """Use statistical sampling for directories with many files"""
        # Keep all directories but sample files
        dirs = [item for item in items if item[2]]  # is_dir
        files = [item for item in items if not item[2]]
        
        if len(files) > SAMPLE_SIZE * 10:
            # Sample files and estimate total size
            sample = random.sample(files, SAMPLE_SIZE)
            avg_size = sum(item[1] for item in sample) / len(sample)
            estimated_total = int(avg_size * len(files))
            
            # Return directories + sample of files
            return dirs + sample, estimated_total
        
        return items, sum(item[1] for item in items if not item[2])
        
    def _is_cache_valid(self, path: str, cached: dict) -> bool:
        """Quick cache validation"""
        try:
            # Just check if path still exists (very fast)
            return os.path.exists(path)
        except:
            return False
            
    def _queue_cache_write(self, path: str, size: int, file_count: int, dir_count: int):
        """Queue cache entry for batch writing"""
        entry = (path, size, file_count, dir_count, time.time())
        
        with self.cache_lock:
            self.pending_cache_writes.append(entry)
            
            # Write batch if full
            if len(self.pending_cache_writes) >= BATCH_SIZE:
                self._write_cache_batch()
                
    def _write_cache_batch(self):
        """Write cache entries in batch (must be called with lock held)"""
        if not self.pending_cache_writes:
            return
            
        batch = self.pending_cache_writes[:BATCH_SIZE]
        self.pending_cache_writes = self.pending_cache_writes[BATCH_SIZE:]
        
        # Write to memory cache immediately
        for path, size, file_count, dir_count, scan_time in batch:
            self.memory_cache.put(path, {
                'size': size,
                'file_count': file_count,
                'dir_count': dir_count,
                'scan_time': scan_time
            })
        
        # Queue database write (non-blocking)
        threading.Thread(
            target=self._write_to_database,
            args=(batch,),
            daemon=True
        ).start()
        
    def _write_to_database(self, batch: List):
        """Write batch to database (runs in separate thread)"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA synchronous = OFF")  # Even faster for batch
            cursor = conn.cursor()
            
            for path, size, file_count, dir_count, scan_time in batch:
                try:
                    mtime = os.path.getmtime(path)
                except:
                    mtime = 0
                    
                cursor.execute('''
                    INSERT OR REPLACE INTO scan_cache 
                    (path, size, is_dir, last_modified, scan_time, file_count, dir_count, checksum)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (path, size, 1, mtime, scan_time, file_count, dir_count, None))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Database write error: {e}")
            
    def _flush_cache(self):
        """Flush all pending cache writes"""
        with self.cache_lock:
            while self.pending_cache_writes:
                self._write_cache_batch()
                
    def _cache_writer(self):
        """Background thread for periodic cache flushing"""
        while self.is_scanning:
            time.sleep(1)  # Flush every second
            with self.cache_lock:
                if self.pending_cache_writes:
                    self._write_cache_batch()
                    
    def _progress_reporter(self, callback):
        """Background thread for progress reporting"""
        last_update = 0
        
        while self.is_scanning:
            try:
                # Get all pending progress updates
                updates = []
                while True:
                    try:
                        update = self.progress_queue.get_nowait()
                        if update is None:  # Stop signal
                            return
                        updates.append(update)
                    except queue.Empty:
                        break
                
                # Report latest progress
                if updates and callback:
                    current_time = time.time()
                    if current_time - last_update >= PROGRESS_UPDATE_INTERVAL:
                        latest = updates[-1]
                        callback(latest[0], latest[1], latest[2])
                        last_update = current_time
                        
                time.sleep(0.05)  # Small delay
                
            except Exception as e:
                print(f"Progress reporter error: {e}")
                
    def stop_scan(self):
        """Stop the current scan operation"""
        self.should_stop = True
        self.executor.shutdown(wait=False)

class OptimizedDiskAnalyzerGUI:
    """Optimized GUI with progressive loading and smooth updates"""
    
    def __init__(self):
        self.scanner = OptimizedDiskScanner()
        self.current_root = None
        self.scan_thread = None
        self.last_ui_update = 0
        self.pending_tree_updates = deque()
        
        # Create main window
        if USE_CTK:
            self.root = ctk.CTk()
            self.root.title("DiskInsight Pro - Ultra Fast Disk Analyzer")
            self.root.geometry("1200x700")
        else:
            self.root = tk.Tk()
            self.root.title("DiskInsight Pro - Ultra Fast Disk Analyzer")
            self.root.geometry("1200x700")
            
        self.setup_ui()
        self.populate_drives()
        
    def setup_ui(self):
        """Setup the user interface"""
        # Main container
        if USE_CTK:
            main_frame = ctk.CTkFrame(self.root)
            main_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            # Top toolbar
            toolbar = ctk.CTkFrame(main_frame)
            toolbar.pack(fill="x", pady=(0, 10))
            
            # Drive selector
            ctk.CTkLabel(toolbar, text="Select Drive:").pack(side="left", padx=5)
            self.drive_var = tk.StringVar()
            self.drive_combo = ctk.CTkComboBox(
                toolbar, 
                variable=self.drive_var,
                values=[],
                command=self.on_drive_selected,
                width=200
            )
            self.drive_combo.pack(side="left", padx=5)
            
            # Path entry for custom paths
            ctk.CTkLabel(toolbar, text="Or Path:").pack(side="left", padx=(20, 5))
            self.path_entry = ctk.CTkEntry(toolbar, width=300)
            self.path_entry.pack(side="left", padx=5)
            
            # Scan button
            self.scan_btn = ctk.CTkButton(
                toolbar,
                text="⚡ Fast Scan",
                command=self.start_scan,
                width=120
            )
            self.scan_btn.pack(side="left", padx=5)
            
            # Stop button
            self.stop_btn = ctk.CTkButton(
                toolbar,
                text="⏹ Stop",
                command=self.stop_scan,
                state="disabled",
                width=100
            )
            self.stop_btn.pack(side="left", padx=5)
            
            # Performance metrics
            metrics_frame = ctk.CTkFrame(toolbar)
            metrics_frame.pack(side="left", padx=20)
            
            self.items_label = ctk.CTkLabel(metrics_frame, text="Items: 0")
            self.items_label.pack(side="left", padx=5)
            
            self.speed_label = ctk.CTkLabel(metrics_frame, text="Speed: 0/s")
            self.speed_label.pack(side="left", padx=5)
            
            self.size_label = ctk.CTkLabel(metrics_frame, text="Size: 0 B")
            self.size_label.pack(side="left", padx=5)
            
            # Main content area
            paned = ttk.PanedWindow(main_frame, orient="horizontal")
            paned.pack(fill="both", expand=True)
            
            # Left panel - Tree view
            left_frame = ttk.Frame(paned)
            paned.add(left_frame, weight=2)
            
            ttk.Label(left_frame, text="Directory Structure", font=("Arial", 12, "bold")).pack(pady=5)
            
            tree_frame = ttk.Frame(left_frame)
            tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
            
        else:
            main_frame = ttk.Frame(self.root)
            main_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            # Top toolbar
            toolbar = ttk.Frame(main_frame)
            toolbar.pack(fill="x", pady=(0, 10))
            
            # Drive selector
            ttk.Label(toolbar, text="Select Drive:").pack(side="left", padx=5)
            self.drive_var = tk.StringVar()
            self.drive_combo = ttk.Combobox(
                toolbar,
                textvariable=self.drive_var,
                width=20,
                state="readonly"
            )
            self.drive_combo.pack(side="left", padx=5)
            self.drive_combo.bind("<<ComboboxSelected>>", lambda e: self.on_drive_selected(None))
            
            # Path entry
            ttk.Label(toolbar, text="Or Path:").pack(side="left", padx=(20, 5))
            self.path_entry = ttk.Entry(toolbar, width=40)
            self.path_entry.pack(side="left", padx=5)
            
            # Scan button
            self.scan_btn = ttk.Button(
                toolbar,
                text="⚡ Fast Scan",
                command=self.start_scan
            )
            self.scan_btn.pack(side="left", padx=5)
            
            # Stop button
            self.stop_btn = ttk.Button(
                toolbar,
                text="⏹ Stop",
                command=self.stop_scan,
                state="disabled"
            )
            self.stop_btn.pack(side="left", padx=5)
            
            # Performance metrics
            metrics_frame = ttk.Frame(toolbar)
            metrics_frame.pack(side="left", padx=20)
            
            self.items_label = ttk.Label(metrics_frame, text="Items: 0")
            self.items_label.pack(side="left", padx=5)
            
            self.speed_label = ttk.Label(metrics_frame, text="Speed: 0/s")
            self.speed_label.pack(side="left", padx=5)
            
            self.size_label = ttk.Label(metrics_frame, text="Size: 0 B")
            self.size_label.pack(side="left", padx=5)
            
            # Main content area
            paned = ttk.PanedWindow(main_frame, orient="horizontal")
            paned.pack(fill="both", expand=True)
            
            # Left panel - Tree view
            left_frame = ttk.Frame(paned)
            paned.add(left_frame, weight=2)
            
            ttk.Label(left_frame, text="Directory Structure", font=("Arial", 12, "bold")).pack(pady=5)
            
            tree_frame = ttk.Frame(left_frame)
            tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Create Treeview with more columns
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("Size", "Items", "Type", "Percent"),
            show="tree headings",
            selectmode="browse"
        )
        
        # Configure columns
        self.tree.heading("#0", text="Name", command=lambda: self.sort_tree("#0"))
        self.tree.heading("Size", text="Size", command=lambda: self.sort_tree("Size"))
        self.tree.heading("Items", text="Items", command=lambda: self.sort_tree("Items"))
        self.tree.heading("Type", text="Type", command=lambda: self.sort_tree("Type"))
        self.tree.heading("Percent", text="%", command=lambda: self.sort_tree("Percent"))
        
        self.tree.column("#0", width=350)
        self.tree.column("Size", width=100)
        self.tree.column("Items", width=100)
        self.tree.column("Type", width=80)
        self.tree.column("Percent", width=60)
        
        # Scrollbars for tree
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        # Pack tree and scrollbars
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # Right panel - Details
        if USE_CTK:
            right_frame = ttk.Frame(paned)  # Use ttk.Frame for compatibility with PanedWindow
            paned.add(right_frame, weight=1)
            
            ttk.Label(right_frame, text="Details & Statistics", 
                     font=("Arial", 12, "bold")).pack(pady=5)
            
            # Statistics frame
            stats_frame = ttk.LabelFrame(right_frame, text="Scan Statistics")
            stats_frame.pack(fill="x", padx=5, pady=5)
            
            self.stats_text = tk.Text(stats_frame, height=8, wrap="word")
            self.stats_text.pack(fill="both", expand=True, padx=5, pady=5)
            
            # Details text
            ttk.Label(right_frame, text="Selected Item Details", 
                     font=("Arial", 10, "bold")).pack(pady=5)
            
            details_frame = ttk.Frame(right_frame)
            details_frame.pack(fill="both", expand=True, padx=5, pady=5)
            
            self.details_text = tk.Text(details_frame, wrap="word")
            details_scroll = ttk.Scrollbar(details_frame, command=self.details_text.yview)
            self.details_text.configure(yscrollcommand=details_scroll.set)
            
            self.details_text.pack(side="left", fill="both", expand=True)
            details_scroll.pack(side="right", fill="y")
            
            # Status bar
            self.status_bar = ctk.CTkLabel(
                self.root,
                text="Ready for ultra-fast scanning",
                anchor="w"
            )
            self.status_bar.pack(side="bottom", fill="x", padx=10, pady=5)
            
        else:
            right_frame = ttk.Frame(paned)
            paned.add(right_frame, weight=1)
            
            ttk.Label(right_frame, text="Details & Statistics", 
                     font=("Arial", 12, "bold")).pack(pady=5)
            
            # Statistics frame
            stats_frame = ttk.LabelFrame(right_frame, text="Scan Statistics")
            stats_frame.pack(fill="x", padx=5, pady=5)
            
            self.stats_text = tk.Text(stats_frame, height=5, wrap="word")
            self.stats_text.pack(fill="both", expand=True, padx=5, pady=5)
            
            # Details
            ttk.Label(right_frame, text="Selected Item Details", 
                     font=("Arial", 10, "bold")).pack(pady=5)
            
            details_frame = ttk.Frame(right_frame)
            details_frame.pack(fill="both", expand=True, padx=5, pady=5)
            
            self.details_text = tk.Text(details_frame, wrap="word")
            details_scroll = ttk.Scrollbar(details_frame, command=self.details_text.yview)
            self.details_text.configure(yscrollcommand=details_scroll.set)
            
            self.details_text.pack(side="left", fill="both", expand=True)
            details_scroll.pack(side="right", fill="y")
            
            # Status bar
            self.status_bar = ttk.Label(
                self.root,
                text="Ready for ultra-fast scanning",
                relief="sunken",
                anchor="w"
            )
            self.status_bar.pack(side="bottom", fill="x", padx=5, pady=2)
        
        # Bind events
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        
        # Performance tracking
        self.scan_start_time = 0
        self.last_item_count = 0
        
    def populate_drives(self):
        """Populate the drive selector with available drives"""
        drives = []
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                try:
                    # Get drive info using ctypes for speed
                    drives.append(drive)
                except:
                    drives.append(drive)
                    
        if USE_CTK:
            self.drive_combo.configure(values=drives)
            if drives:
                self.drive_combo.set(drives[0])
        else:
            self.drive_combo['values'] = drives
            if drives:
                self.drive_combo.current(0)
                
    def on_drive_selected(self, event):
        """Handle drive selection"""
        selected = self.drive_var.get()
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, selected)
        self.status_bar.configure(text=f"Selected: {selected}")
        
    def format_size(self, size: int) -> str:
        """Format size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
        
    def start_scan(self):
        """Start scanning the selected drive or path"""
        # Get path to scan
        path = self.path_entry.get().strip()
        if not path:
            path = self.drive_var.get()
            
        if not path:
            messagebox.showwarning("No Path", "Please select a drive or enter a path to scan.")
            return
            
        if not os.path.exists(path):
            messagebox.showerror("Invalid Path", f"Path does not exist: {path}")
            return
            
        # Clear existing tree
        self.tree.delete(*self.tree.get_children())
        
        # Reset metrics
        self.scan_start_time = time.time()
        self.last_item_count = 0
        
        # Update UI state
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_bar.configure(text=f"Scanning {path} with {WORKER_THREADS} threads...")
        
        # Clear stats
        self.update_stats(0, 0, 0)
        
        # Start scan in separate thread
        self.scan_thread = threading.Thread(
            target=self.perform_scan,
            args=(path,),
            daemon=True
        )
        self.scan_thread.start()
        
    def perform_scan(self, path: str):
        """Perform the actual scan operation"""
        def update_progress(current_path, items_scanned, total_size):
            # Update UI from main thread
            self.root.after(0, self.update_scan_progress, 
                          current_path, items_scanned, total_size)
            
        # Perform scan
        root_node = self.scanner.scan_directory_parallel(path, callback=update_progress)
        
        # Update UI with results
        self.root.after(0, self.display_results, root_node)
        
    def update_scan_progress(self, current_path: str, items_scanned: int, total_size: int):
        """Update progress during scan with performance metrics"""
        current_time = time.time()
        
        # Throttle UI updates
        if current_time - self.last_ui_update < PROGRESS_UPDATE_INTERVAL:
            return
            
        self.last_ui_update = current_time
        
        # Calculate speed
        elapsed = current_time - self.scan_start_time
        if elapsed > 0:
            items_per_second = items_scanned / elapsed
            self.speed_label.configure(text=f"Speed: {items_per_second:.0f}/s")
        
        # Update metrics
        self.items_label.configure(text=f"Items: {items_scanned:,}")
        self.size_label.configure(text=f"Size: {self.format_size(total_size)}")
        
        # Update status
        display_path = current_path
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]
        self.status_bar.configure(text=f"Scanning: {display_path}")
        
        # Update statistics
        self.update_stats(items_scanned, total_size, elapsed)
        
    def update_stats(self, items: int, size: int, elapsed: float):
        """Update statistics display"""
        stats = f"Scan Statistics:\n"
        stats += f"━━━━━━━━━━━━━━━━━━━━\n"
        stats += f"Items Scanned: {items:,}\n"
        stats += f"Total Size: {self.format_size(size)}\n"
        stats += f"Time Elapsed: {elapsed:.1f}s\n"
        
        if elapsed > 0:
            stats += f"Scan Rate: {items/elapsed:.0f} items/sec\n"
            stats += f"Data Rate: {self.format_size(int(size/elapsed))}/sec\n"
        
        stats += f"Worker Threads: {WORKER_THREADS}\n"
        stats += f"Cache Size: {len(self.scanner.memory_cache.cache):,} items\n"
        
        if self.scanner.errors:
            stats += f"\nErrors: {len(self.scanner.errors)}\n"
            for error in self.scanner.errors[-5:]:  # Show last 5 errors
                stats += f"  • {error[:50]}...\n" if len(error) > 50 else f"  • {error}\n"
        
        # Update stats display
        if USE_CTK:
            self.stats_text.delete("1.0", "end")
            self.stats_text.insert("1.0", stats)
        else:
            self.stats_text.delete("1.0", "end")
            self.stats_text.insert("1.0", stats)
            
    def display_results(self, root_node: Optional[FastFileNode]):
        """Display scan results in the tree view"""
        # Reset UI state
        self.scan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        
        elapsed = time.time() - self.scan_start_time
        
        if not root_node:
            self.status_bar.configure(text="Scan failed or was stopped")
            return
            
        self.current_root = root_node
        
        # Final statistics
        total_size = root_node.get_total_size()
        self.status_bar.configure(
            text=f"Scan complete in {elapsed:.1f}s | "
                 f"{self.scanner.scanned_items:,} items | "
                 f"{self.format_size(total_size)} | "
                 f"{self.scanner.scanned_items/elapsed:.0f} items/sec"
        )
        
        # Update final stats
        self.update_stats(self.scanner.scanned_items, total_size, elapsed)
        
        # Populate tree progressively
        self.populate_tree_progressive(root_node)
        
    def populate_tree_progressive(self, node: FastFileNode, parent: str = ""):
        """Populate tree view progressively for smooth UI"""
        # Create tree item
        if node.is_dir:
            item_type = "📁 Folder"
            items_text = f"{node.file_count:,} files, {node.dir_count:,} dirs"
        else:
            item_type = "📄 File"
            items_text = ""
            
        # Calculate percentage
        if parent and self.current_root:
            parent_size = self.current_root.get_total_size()
            if parent_size > 0:
                percent = (node.get_total_size() / parent_size) * 100
                percent_text = f"{percent:.1f}%"
            else:
                percent_text = "0%"
        else:
            percent_text = "100%"
            
        item_id = self.tree.insert(
            parent,
            "end",
            text=node.name,
            values=(
                self.format_size(node.get_total_size()),
                items_text,
                item_type,
                percent_text
            ),
            open=False
        )
        
        # Add children (sorted by size)
        if node.is_dir and node.children:
            # Sort children by size (largest first)
            sorted_children = sorted(
                node.children,
                key=lambda x: x.get_total_size(),
                reverse=True
            )
            
            # Add top items immediately, rest progressively
            immediate_count = min(20, len(sorted_children))
            
            for child in sorted_children[:immediate_count]:
                self.populate_tree_progressive(child, item_id)
                
            # Queue remaining items for progressive loading
            if len(sorted_children) > immediate_count:
                remaining = sorted_children[immediate_count:]
                
                # Add placeholder
                placeholder = self.tree.insert(
                    item_id,
                    "end",
                    text=f"Loading {len(remaining)} more items...",
                    values=("", "", "", "")
                )
                
                # Schedule progressive loading
                self.root.after(10, self.load_remaining_items, 
                              item_id, remaining, placeholder)
                
    def load_remaining_items(self, parent_id: str, items: List[FastFileNode], 
                            placeholder_id: str):
        """Load remaining items progressively"""
        # Remove placeholder
        try:
            self.tree.delete(placeholder_id)
        except:
            pass
            
        # Add items in chunks
        chunk_size = 50
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i+chunk_size]
            for item in chunk:
                self.populate_tree_progressive(item, parent_id)
                
            # Let UI update
            self.root.update_idletasks()
            
    def sort_tree(self, column: str):
        """Sort tree by column"""
        # Implementation for sorting tree items
        pass
        
    def stop_scan(self):
        """Stop the current scan operation"""
        self.scanner.stop_scan()
        self.status_bar.configure(text="Stopping scan...")
        
    def on_tree_select(self, event):
        """Handle tree item selection"""
        selection = self.tree.selection()
        if not selection:
            return
            
        item = selection[0]
        item_text = self.tree.item(item, "text")
        values = self.tree.item(item, "values")
        
        # Display details
        details = f"Selected Item Details:\n"
        details += f"━━━━━━━━━━━━━━━━━━━━\n"
        details += f"Name: {item_text}\n"
        details += f"Size: {values[0]}\n"
        if values[1]:  # Items count
            details += f"Contains: {values[1]}\n"
        details += f"Type: {values[2][2:] if len(values[2]) > 2 else values[2]}\n"
        details += f"Percentage: {values[3]}\n"
        
        # Get full path
        path_parts = [item_text]
        parent = self.tree.parent(item)
        while parent:
            path_parts.insert(0, self.tree.item(parent, "text"))
            parent = self.tree.parent(parent)
            
        full_path = os.path.join(*path_parts) if len(path_parts) > 1 else path_parts[0]
        details += f"\nFull Path:\n{full_path}\n"
        
        # Add file metadata if available
        try:
            if os.path.exists(full_path):
                stat = os.stat(full_path)
                details += f"\nFile Metadata:\n"
                details += f"Created: {datetime.fromtimestamp(stat.st_ctime)}\n"
                details += f"Modified: {datetime.fromtimestamp(stat.st_mtime)}\n"
                details += f"Accessed: {datetime.fromtimestamp(stat.st_atime)}\n"
        except:
            pass
        
        # Update details text
        if USE_CTK:
            self.details_text.delete("1.0", "end")
            self.details_text.insert("1.0", details)
        else:
            self.details_text.delete("1.0", "end")
            self.details_text.insert("1.0", details)
            
    def on_tree_double_click(self, event):
        """Handle double-click on tree item"""
        selection = self.tree.selection()
        if not selection:
            return
            
        item = selection[0]
        
        # Toggle item open/closed state
        if self.tree.get_children(item):
            if self.tree.item(item, "open"):
                self.tree.item(item, open=False)
            else:
                self.tree.item(item, open=True)
                
    def run(self):
        """Start the GUI application"""
        self.root.mainloop()

def main():
    """Main entry point"""
    print("Starting DiskInsight Pro - Optimized Version")
    print(f"Using {WORKER_THREADS} worker threads")
    print(f"Cache size: {CACHE_SIZE:,} items")
    
    app = OptimizedDiskAnalyzerGUI()
    app.run()

if __name__ == "__main__":
    main()
