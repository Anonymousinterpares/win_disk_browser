"""
Shared Live Update System
Unified file system monitoring and event processing for both main UI and visual analyzer
"""

import os
import time
import queue
import logging
import threading
from collections import deque
from typing import Optional, List, Set, Callable, TYPE_CHECKING, Any

# Always try to import, but handle gracefully if missing
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from disk_analyzer_fixed import FileNode
    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    FileNode = None
    WATCHDOG_AVAILABLE = False

if TYPE_CHECKING:
    if not WATCHDOG_AVAILABLE:
        from typing import Any as Observer
        from typing import Any as FileNode

# Patterns to ignore during live updates (reduce noise)
LIVE_UPDATE_IGNORE_PATTERNS = {
    "/$RECYCLE.BIN",
    "/System Volume Information",
    "/Windows/Prefetch",
    "/Windows/Temp",
    "/AppData/Local/Temp",
    "/AppData/Local/Microsoft/Edge/User Data",
    "/AppData/Local/Google/Chrome/User Data",
    "/AppData/Local/Mozilla/Firefox/Profiles",
    "/Program Files/WindowsApps",
    "/AppData/Roaming/Microsoft/Windows/Recent",
    "/Microsoft/EdgeUpdate", 
    "pagefile.sys",
    "swapfile.sys"
}

if WATCHDOG_AVAILABLE and FileSystemEventHandler:
    class LiveUpdateHandler(FileSystemEventHandler):
        """Enhanced event handler for file system changes"""
        
        def __init__(self, event_queue: queue.Queue):
            super().__init__()
            self.event_queue = event_queue

        def on_any_event(self, event):
            """Handle all file system events"""
            self.event_queue.put(event)
else:
    class LiveUpdateHandler:
        """Fallback event handler when watchdog is not available"""
        
        def __init__(self, event_queue: queue.Queue):
            self.event_queue = event_queue

        def on_any_event(self, event):
            """Handle all file system events"""
            self.event_queue.put(event)

class LiveUpdateManager:
    """
    Unified live update manager that can work with both main UI and visual analyzer
    """
    
    def __init__(self, scanner, ui_callback: Optional[Callable] = None, ignore_paths: Optional[Set[str]] = None):
        """
        Initialize the live update manager
        
        Args:
            scanner: FixedDiskScanner instance
            ui_callback: Callback function for UI updates (path_list) -> None
            ignore_paths: Additional paths to ignore (e.g., database, log files)
        """
        self.scanner = scanner
        self.ui_callback = ui_callback
        self.observer: Optional[Any] = None
        self.event_queue = queue.Queue()
        self.live_update_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self.ignore_paths = ignore_paths or set()
        
        # Add scanner's database path to ignore list
        if hasattr(scanner, 'db_path'):
            self.ignore_paths.add(os.path.abspath(scanner.db_path))
        
    def start_monitoring(self, path: str) -> bool:
        """
        Start monitoring a path for file system changes
        
        Args:
            path: Root path to monitor
            
        Returns:
            bool: True if monitoring started successfully
        """
        if self.observer and self.observer.is_alive():
            logging.warning("Live update monitoring is already running")
            return True
            
        if not hasattr(self.scanner, 'root_node') or not self.scanner.root_node:
            logging.warning("Cannot start live updates without loaded data")
            return False
            
        try:
            logging.info(f"Starting live update monitoring for: {path}")
            self._stop_flag.clear()
            
            # Create and start observer
            event_handler = LiveUpdateHandler(self.event_queue)
            self.observer = Observer()
            self.observer.schedule(event_handler, path, recursive=True)
            self.observer.start()
            
            # Start event processing thread
            self.live_update_thread = threading.Thread(
                target=self._process_event_queue, 
                daemon=True
            )
            self.live_update_thread.start()
            
            logging.info("Live update monitoring started successfully")
            return True
            
        except Exception as e:
            logging.error(f"Failed to start live update monitoring: {e}")
            return False
    
    def stop_monitoring(self) -> bool:
        """
        Stop monitoring file system changes
        
        Returns:
            bool: True if stopped successfully
        """
        try:
            if self.observer and self.observer.is_alive():
                self.observer.stop()
                self.observer.join(timeout=5.0)
                
            self._stop_flag.set()
            
            if self.live_update_thread and self.live_update_thread.is_alive():
                # Send sentinel to unblock the queue
                self.event_queue.put(None)
                self.live_update_thread.join(timeout=5.0)
                
            self.observer = None
            self.live_update_thread = None
            logging.info("Live update monitoring stopped")
            return True
            
        except Exception as e:
            logging.error(f"Error stopping live update monitoring: {e}")
            return False
    
    def _process_event_queue(self):
        """
        Process file system events in batches with intelligent filtering
        """
        logging.info(f"Live update processor started, ignoring changes to: {list(self.ignore_paths)}")
        
        while not self._stop_flag.is_set():
            try:
                # Get first event with timeout
                event = self.event_queue.get(timeout=1.0)
                if event is None:  # Sentinel for shutdown
                    break
                
                # Collect batch of events
                events_batch = [event]
                time.sleep(0.2)  # Wait for burst of events
                
                while not self.event_queue.empty():
                    try:
                        next_event = self.event_queue.get_nowait()
                        if next_event is None:
                            break
                        events_batch.append(next_event)
                    except queue.Empty:
                        break
                
                # Process the batch
                self._process_event_batch(events_batch)
                
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Error in live update event processing: {e}")
    
    def _process_event_batch(self, events: List):
        """
        Process a batch of file system events
        
        Args:
            events: List of watchdog events
        """
        if not events:
            return
            
        logging.info(f"Processing batch of {len(events)} file system events")
        affected_parents = set()
        
        for event in events:
            try:
                # Get paths to check
                paths_to_check = [event.src_path]
                if hasattr(event, 'dest_path') and event.dest_path:
                    paths_to_check.append(event.dest_path)
                
                # Skip ignored paths
                should_ignore = False
                for path in paths_to_check:
                    abs_path = os.path.abspath(path)
                    
                    # Check against ignore list
                    if abs_path in self.ignore_paths:
                        should_ignore = True
                        break
                        
                    # Check against pattern list
                    normalized_path = path.replace('\\', '/')
                    if any(pattern in normalized_path for pattern in LIVE_UPDATE_IGNORE_PATTERNS):
                        should_ignore = True
                        break
                
                if should_ignore:
                    continue
                
                # Process the event
                changed_parents = self._process_single_event(event)
                affected_parents.update(changed_parents)
                
            except Exception as e:
                logging.error(f"Error processing event {event.src_path}: {e}")
                continue
        
        # Notify UI of changes
        if affected_parents:
            self._notify_ui_changes(list(affected_parents))
    
    def _process_single_event(self, event) -> Set[str]:
        """
        Process a single file system event and update the in-memory tree
        
        Args:
            event: Watchdog event
            
        Returns:
            Set of parent paths that were affected
        """
        affected_parents = set()
        path = event.src_path
        parent_dir = os.path.dirname(path)
        
        # Find parent node in tree
        parent_node = self.scanner._find_node_by_path(parent_dir)
        if not parent_node:
            # Change outside our monitored tree
            return affected_parents
        
        # Find existing node
        existing_node = self.scanner._find_node_by_path(path)
        
        if event.event_type == 'deleted' and existing_node:
            # File/folder deleted
            if existing_node in parent_node.children:
                parent_node.children.remove(existing_node)
                parent_node.invalidate_size_cache()
                affected_parents.add(parent_dir)
                logging.debug(f"Live update: DELETED {path}")
                
        elif event.event_type == 'created':
            # File/folder created
            if os.path.exists(path):
                try:
                    stat = os.stat(path)
                    new_node = FileNode(
                        path=path,
                        name=os.path.basename(path),
                        size=stat.st_size,
                        is_dir=os.path.isdir(path),
                        mtime=stat.st_mtime,
                        parent=parent_node
                    )
                    parent_node.children.append(new_node)
                    parent_node.invalidate_size_cache()
                    affected_parents.add(parent_dir)
                    logging.debug(f"Live update: CREATED {path}")
                except OSError:
                    pass  # File might have been deleted before we could stat it
                    
        elif event.event_type == 'modified' and existing_node:
            # File/folder modified
            if os.path.exists(path):
                try:
                    stat = os.stat(path)
                    if not existing_node.is_dir and existing_node.size != stat.st_size:
                        existing_node.size = stat.st_size
                        existing_node.mtime = stat.st_mtime
                        parent_node.invalidate_size_cache()
                        affected_parents.add(parent_dir)
                        logging.debug(f"Live update: MODIFIED {path}")
                except OSError:
                    pass
                    
        elif event.event_type == 'moved':
            # File/folder moved
            dest_path = getattr(event, 'dest_path', None)
            if dest_path:
                # Handle as delete from source + create at destination
                if existing_node and existing_node in parent_node.children:
                    parent_node.children.remove(existing_node)
                    parent_node.invalidate_size_cache()
                    affected_parents.add(parent_dir)
                
                # Create at destination
                new_parent_dir = os.path.dirname(dest_path)
                new_parent_node = self.scanner._find_node_by_path(new_parent_dir)
                if new_parent_node and os.path.exists(dest_path):
                    try:
                        stat = os.stat(dest_path)
                        moved_node = FileNode(
                            path=dest_path,
                            name=os.path.basename(dest_path),
                            size=stat.st_size,
                            is_dir=os.path.isdir(dest_path),
                            mtime=stat.st_mtime,
                            parent=new_parent_node
                        )
                        new_parent_node.children.append(moved_node)
                        new_parent_node.invalidate_size_cache()
                        affected_parents.add(new_parent_dir)
                        logging.debug(f"Live update: MOVED {path} -> {dest_path}")
                    except OSError:
                        pass
        
        return affected_parents
    
    def _notify_ui_changes(self, changed_paths: List[str]):
        """
        Notify UI about changes and save to cache
        
        Args:
            changed_paths: List of parent directory paths that changed
        """
        logging.info(f"Live data change detected, notifying UI about: {changed_paths}")
        
        # Save changes to cache (debounced to reduce disk I/O)
        if self.scanner.root_node:
            self.scanner.cache_saver.schedule_save(self.scanner.root_node)
        
        # Notify UI callback if provided
        if self.ui_callback:
            try:
                unique_paths = list(set(changed_paths))
                self.ui_callback(unique_paths)
            except Exception as e:
                logging.error(f"Error notifying UI of live updates: {e}")
    
    def is_monitoring(self) -> bool:
        """Check if monitoring is currently active"""
        return self.observer is not None and self.observer.is_alive()