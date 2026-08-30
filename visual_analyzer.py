from collections import deque
import webview
import threading
import os
import sys
import json
import logging
import math

# Ensure current directory is in Python path for PyInstaller
if hasattr(sys, '_MEIPASS'):
    # Running as PyInstaller executable
    sys.path.insert(0, sys._MEIPASS)
else:
    # Running as script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from disk_analyzer_fixed import FixedDiskScanner, FileNode
import colorsys
from copy import deepcopy
from typing import Dict, List, Tuple, Optional, Union
import time
import subprocess
import platform
import ctypes
import atexit

# Import shared live update system with fallback
try:
    from live_update_system import LiveUpdateManager
    SHARED_LIVE_UPDATE_AVAILABLE = True
except ImportError:
    LiveUpdateManager = None
    SHARED_LIVE_UPDATE_AVAILABLE = False

def is_admin():
    """Check if the script is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

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

def get_app_data_dir():
    """Get the application data directory for DiskInsight Pro"""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        # Fallback to current directory if APPDATA is not available
        return os.path.abspath('.')
    
    app_dir = os.path.join(appdata, 'DiskInsightPro')
    
    # Create directory if it doesn't exist
    try:
        os.makedirs(app_dir, exist_ok=True)
        return app_dir
    except Exception as e:
        # If we can't create the app data directory, use current directory
        return os.path.abspath('.')

# --- SETUP LOGGING ---
# Define the log file path in the app data directory
APP_DATA_DIR = get_app_data_dir()
LOG_FILE_PATH = os.path.join(APP_DATA_DIR, 'analyzer.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=LOG_FILE_PATH,
    filemode='w'
)

# Log the app data directory after logging is set up
logging.info(f"DiskInsight Pro starting - using app data directory: {APP_DATA_DIR}")
logging.info(f"Log file: {LOG_FILE_PATH}")

# Global instance of our scanner with database in app data directory
DATABASE_PATH = os.path.join(APP_DATA_DIR, 'disk_cache_fixed.db')
scanner = FixedDiskScanner(db_path=DATABASE_PATH)
logging.info(f"Database file: {DATABASE_PATH}")
atexit.register(scanner.cache_saver.flush_now)


from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import queue

class LiveUpdateHandler(FileSystemEventHandler):
    """Event handler that puts file system events into a queue."""
    def __init__(self, event_queue):
        super().__init__()
        self.queue = event_queue

    def on_any_event(self, event):
        self.queue.put(event)


class NaniteLODSystem:
    """
    Implements a Nanite-like LOD (Level of Detail) system for disk visualization.
    Automatically merges small chunks based on view parameters for optimal performance.
    """
    
    def __init__(self, min_pixel_size: float = 2.0, max_items_per_level: int = 100):
        self.min_pixel_size = min_pixel_size  # Minimum size in pixels before merging
        self.max_items_per_level = max_items_per_level  # Max items to show at any level
        self.cache = {}  # Cache LOD views for performance
        
    def calculate_adaptive_threshold(self, viewport_size: Tuple[int, int], zoom_level: float, 
                                    depth: int, parent_size: float) -> float:
        """
        Calculate adaptive threshold based on viewport, zoom, and depth.
        Similar to Nanite's screen-space error metric.
        """
        # Validate viewport dimensions to prevent division by zero
        width, height = viewport_size
        if width <= 0 or height <= 0:
            logging.warning(f"Invalid viewport size {viewport_size}, using fallback (1280, 800)")
            width, height = 1280, 800
        
        # Validate other parameters
        zoom_level = max(0.01, zoom_level)  # Prevent zero/negative zoom
        parent_size = max(0.001, parent_size)  # Prevent zero parent size
        
        # Base threshold increases with depth (deeper = less detail)
        base_threshold = 0.001 * (1.5 ** depth)
        
        # Adjust for zoom level (higher zoom = more detail)
        zoom_factor = max(0.1, 1.0 / (zoom_level ** 0.5))
        
        # Screen space calculation - items smaller than min_pixel_size get merged
        viewport_area = width * height
        pixel_threshold = (self.min_pixel_size * self.min_pixel_size) / viewport_area
        
        # Combine factors
        adaptive_threshold = max(base_threshold * zoom_factor, pixel_threshold)
        
        # Ensure we don't exceed a maximum threshold
        return min(adaptive_threshold, 0.1)  # Max 10% threshold
    
    def build_lod_tree(self, node: FileNode, viewport: Tuple[int, int], 
                       zoom: float = 1.0, current_depth: int = 0,
                       parent_total_size: Optional[float] = None) -> dict:
        """
        Builds a single-level LOD tree and finds the largest file within each visible directory.
        """
        # Validate input parameters
        if not node:
            logging.error("build_lod_tree called with None node")
            return {'name': 'Error', 'path': '', 'value': 1, 'children': []}
            
        # Validate viewport
        width, height = viewport
        if width <= 0 or height <= 0:
            logging.warning(f"Invalid viewport {viewport} in build_lod_tree, using fallback")
            viewport = (1280, 800)
        
        if parent_total_size is None:
            parent_total_size = node.get_size()
        
        # Ensure parent_total_size is valid
        parent_total_size = max(0.001, parent_total_size)
            
        node_size = node.get_size()
        threshold = self.calculate_adaptive_threshold(viewport, zoom, current_depth, parent_total_size)
        
        data = {
            'name': node.name, 'path': node.path, 'value': node_size if node_size > 0 else 1,
            'depth': current_depth, 'is_dir': node.is_dir
        }
        
        if not node.is_dir or not node.children:
            data['children'] = []
            return data
            
        sorted_children = sorted(node.children, key=lambda c: c.get_size(), reverse=True)
        
        visible_children, micro_children, micro_size = [], [], 0
        
        for child in sorted_children:
            child_size = child.get_size()
            relative_size = child_size / parent_total_size if parent_total_size > 0 else 0
            
            if relative_size >= threshold and len(visible_children) < self.max_items_per_level:
                child_data = {
                    'name': child.name, 'path': child.path, 'value': child_size if child_size > 0 else 1, 'is_dir': child.is_dir
                }
                
                # --- NEW: Find and embed the largest file within this visible directory ---
                if child.is_dir:
                    largest_file_node = self._find_largest_file_recursive(child)
                    if largest_file_node:
                        child_data['largest_file'] = {
                            'name': largest_file_node.name,
                            'path': largest_file_node.path,
                            'size': largest_file_node.size
                        }
                visible_children.append(child_data)
            else:
                micro_children.append(child)
                micro_size += child_size
        
        if micro_children:
            visible_children.extend(self._create_smart_aggregations(micro_children, micro_size, node.path, threshold, parent_total_size))
            
        data['children'] = visible_children
        return data
    
    def _find_largest_file_recursive(self, node: FileNode) -> Optional[FileNode]:
        """Helper to recursively find the single largest file within a directory tree."""
        largest_file = None
        max_size = -1

        q = deque([node])
        while q:
            current = q.popleft()
            for child in current.children:
                if child.is_dir:
                    q.append(child)
                elif child.size > max_size:
                    max_size = child.size
                    largest_file = child
        return largest_file
    
    def _create_smart_aggregations(self, items: List[FileNode], total_size: float, 
                                  parent_path: str, threshold: float,
                                  parent_total_size: float) -> List[dict]:
        """
        Create smart aggregation groups instead of one "Other Items" blob.
        Groups by file type, size ranges, or directories.
        """
        if not items:
            return []
            
        aggregations = []
        if len(items) > 10:
            dirs = [i for i in items if i.is_dir]
            files = [i for i in items if not i.is_dir]
            extension_groups = {}

            for file in files:
                ext = os.path.splitext(file.name)[1].lower() if '.' in file.name else 'no_ext'
                if ext not in extension_groups: extension_groups[ext] = []
                extension_groups[ext].append(file)
            for ext, group_files in extension_groups.items():
                if len(group_files) >= 3:
                    group_size = sum(f.get_size() for f in group_files)
                    if group_size / parent_total_size >= threshold * 0.5:
                        aggregations.append({'name': f'{len(group_files)} {ext} files', 'path': f'{parent_path}\\[{ext}_files]', 'value': group_size if group_size > 0 else 1, 'itemStyle': {'color': self._get_extension_color(ext)}, 'aggregated': True, 'expandable': True, 'items': [{'name': f.name, 'path': f.path, 'size': f.get_size()} for f in group_files]})
            if dirs:
                dir_size = sum(d.get_size() for d in dirs)
                aggregations.append({'name': f'{len(dirs)} folders', 'path': f'{parent_path}\\[folders]', 'value': dir_size if dir_size > 0 else 1, 'itemStyle': {'color': '#4a90e2'}, 'aggregated': True, 'expandable': True, 'items': [{'name': d.name, 'path': d.path, 'size': d.get_size()} for d in dirs]})

        # --- Enhanced fallback aggregation with a clear, descriptive label ---
        if not aggregations and items:
            # Count the number of folders and files within the aggregated items
            folder_count = sum(1 for item in items if item.is_dir)
            file_count = len(items) - folder_count
            
            # Build a descriptive name based on what was aggregated
            name_parts = []
            if folder_count > 0:
                name_parts.append(f'{folder_count} folders')
            if file_count > 0:
                name_parts.append(f'{file_count} files')
            
            # Join the parts for a clear label like "2 folders and 15 files"
            final_name = " and ".join(name_parts)
            if not final_name:
                 final_name = f'{len(items)} other items' # Fallback just in case

            aggregations.append({
                'name': final_name,
                'path': f'{parent_path}\\[other]',
                'value': total_size if total_size > 0 else 1,
                'itemStyle': {'color': '#666', 'borderColor': '#888'},
                'aggregated': True,
                'expandable': True,
                'items': [{'name': i.name, 'path': i.path, 'size': i.get_size()} 
                        for i in items]
            })
        
        return aggregations
    
    def _get_extension_color(self, ext: str) -> str:
        """Get a consistent color for file extensions."""
        # Common extensions get specific colors
        ext_colors = {
            '.py': '#3776ab',
            '.js': '#f7df1e',
            '.html': '#e34c26',
            '.css': '#1572b6',
            '.json': '#292929',
            '.xml': '#ff6600',
            '.txt': '#888888',
            '.pdf': '#ff0000',
            '.zip': '#ffd700',
            '.exe': '#0078d4',
            '.dll': '#5c2d91',
            'no_ext': '#999999'
        }
        
        if ext in ext_colors:
            return ext_colors[ext]
        
        # Generate color based on extension string
        hash_val = sum(ord(c) for c in ext)
        hue = (hash_val % 360) / 360
        rgb = colorsys.hsv_to_rgb(hue, 0.6, 0.7)
        return '#{:02x}{:02x}{:02x}'.format(
            int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)
        )

    def _find_largest_file_recursive(self, node: FileNode) -> Optional[FileNode]:
        """Helper to recursively find the single largest file within a directory tree."""
        largest_file = None
        max_size = -1

        q = deque([node])
        while q:
            current = q.popleft()
            for child in current.children:
                if child.is_dir:
                    q.append(child)
                elif child.size > max_size:
                    max_size = child.size
                    largest_file = child
        return largest_file

class Api:
    """
    Enhanced API with Nanite-like LOD system for the JavaScript frontend.
    """
    def __init__(self):
        self._scan_result_root: Optional[FileNode] = None
        self.lod_system = NaniteLODSystem()
        self._viewport_size = (1280, 800)
        self._current_zoom = 1.0
        self._view_cache = {}
        self._initial_pruned_data = None
        self._top_list_cache: Dict[str, Optional[List[dict]]] = {'folders': None, 'files': None}
        self._top_list_lock = threading.Lock()
        self._top_list_build_thread: Optional[threading.Thread] = None
        
        # Initialize live update system (new shared system)
        if SHARED_LIVE_UPDATE_AVAILABLE:
            self.live_update_manager = LiveUpdateManager(
                scanner, 
                ui_callback=self._on_live_data_changed
            )
        else:
            self.live_update_manager = None
            
        # Legacy live update system (keep for backward compatibility)
        self.observer = None
        self.event_queue = queue.Queue()
        self.live_update_thread = None
        self._stop_live_updates_flag = threading.Event()

    def _set_scan_result(self, root_node: Optional[FileNode], warm_indexes: bool = True) -> None:
        """Store scan data and optionally warm derived indexes (sizes, top-list)."""
        self._scan_result_root = root_node
        if root_node:
            FileNode.ensure_tree_sizes(root_node)
            if warm_indexes:
                self._invalidate_top_list_cache()
                self._schedule_top_list_index_build()

    def _invalidate_top_list_cache(self) -> None:
        with self._top_list_lock:
            self._top_list_cache = {'folders': None, 'files': None}

    def _schedule_top_list_index_build(self) -> None:
        if not self._scan_result_root:
            return
        if self._top_list_build_thread and self._top_list_build_thread.is_alive():
            return

        def build_worker():
            try:
                self._build_top_list_index()
            except Exception as e:
                logging.error(f"Top list index build failed: {e}", exc_info=True)

        self._top_list_build_thread = threading.Thread(target=build_worker, daemon=True)
        self._top_list_build_thread.start()

    def _build_top_list_index(self) -> None:
        if not self._scan_result_root:
            return
        start_time = time.time()
        root_path = self._scan_result_root.path
        all_nodes = self._flatten_tree_nodes(self._scan_result_root)

        folders: List[dict] = []
        files: List[dict] = []
        for node in all_nodes:
            if node.is_dir and node.path != root_path:
                folders.append({
                    'name': node.name,
                    'path': node.path,
                    'value': node.get_size(),
                    'is_dir': True,
                })
            elif not node.is_dir:
                files.append({
                    'name': node.name,
                    'path': node.path,
                    'value': node.size,
                    'is_dir': False,
                })

        folders.sort(key=lambda item: item['value'], reverse=True)
        files.sort(key=lambda item: item['value'], reverse=True)

        with self._top_list_lock:
            self._top_list_cache = {'folders': folders, 'files': files}

        elapsed = time.time() - start_time
        logging.info(
            f"Top list index built in {elapsed:.2f}s "
            f"({len(folders)} folders, {len(files)} files)"
        )
        
    def set_viewport(self, width: int, height: int):
        """Update viewport dimensions for LOD calculations."""
        # Validate viewport dimensions
        if width <= 0 or height <= 0:
            logging.warning(f"Attempted to set invalid viewport size ({width}, {height}), using fallback")
            width, height = 1280, 800
        
        logging.info(f"Viewport updated to ({width}, {height})")
        self._viewport_size = (width, height)
        self._view_cache.clear()  # Clear cache when viewport changes
        
    def get_drives(self):
        """Returns a list of available drives."""
        return [f"{d}:\\" for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
    
    def reset_view_data(self):
        """Returns the initially scanned data to reset the frontend view."""
        logging.info("Frontend requested view reset.")
        if self._initial_pruned_data:
            # Ensure data has proper structure
            if 'children' not in self._initial_pruned_data:
                self._initial_pruned_data['children'] = []
        return self._initial_pruned_data

    def get_structure_view(self, path: str, zoom: float = 1.0):
        """
        ENHANCED: Get a hierarchical view that uses the intelligent LOD system for 
        consistent aggregation and supports spatial zoom.
        """
        logging.info(f"Generating ENHANCED structure view for: {path} at zoom {zoom:.2f}")
        
        # Validate inputs
        if not path:
            logging.error("get_structure_view called with empty path")
            return None
            
        if not self._scan_result_root:
            logging.warning("get_structure_view called but no scan data available")
            return None

        # Ensure viewport is valid before proceeding
        width, height = self._viewport_size
        if width <= 0 or height <= 0:
            logging.warning(f"Invalid viewport size {self._viewport_size}, resetting to fallback")
            self.set_viewport(1280, 800)

        node = self._find_node_by_path(path)
        if not node:
            logging.warning(f"Node not found for path: {path}")
            return None

        try:
            # Use the adaptive LOD system to get dynamic aggregation based on zoom
            # This brings the "heatmap" aggregation intelligence into the structure view
            structure_data = self.lod_system.build_lod_tree(
                node, 
                self._viewport_size, 
                zoom
            )
            
            return structure_data
        except Exception as e:
            logging.error(f"Error generating structure view for {path}: {e}", exc_info=True)
            # Return a safe fallback structure
            return {
                'name': node.name or 'Unknown',
                'path': node.path or path,
                'value': max(1, node.get_size()),
                'children': [],
                'error': str(e)
            }
    
    def get_lod_view(self, path: str, threshold: float):
        """Legacy LOD view for compatibility."""
        # For compatibility, just use adaptive view with zoom based on threshold
        zoom = 1.0 if threshold > 0.01 else 2.0
        return self.get_adaptive_lod_view(path, zoom)

    def get_adaptive_lod_view(self, path: str, zoom: float = 1.0, 
                             viewport_width: int = None, viewport_height: int = None,
                             largest_files_only: bool = False): # Add new parameter
        """
        Core of Heatmap Mode. Can show hierarchical view OR a flat list of largest files.
        """
        if not self._scan_result_root:
            return None
            
        if viewport_width and viewport_height:
            self._viewport_size = (viewport_width, viewport_height)
        
        self._current_zoom = zoom
        start_node = self._find_node_by_path(path)
        if not start_node:
            return None
            
        # --- ROUTER LOGIC ---
        if largest_files_only:
            logging.info(f"Generating LARGEST FILES view for: {path}")
            return self._get_largest_files_recursively(start_node)
        else:
            logging.info(f"Generating HIERARCHICAL heatmap view for: {path}")
            return self.lod_system.build_lod_tree(start_node, self._viewport_size, zoom)
    
    def _apply_colors_to_lod(self, lod_data: dict, color_map: dict):
        """Apply colors to LOD data structure."""
        if lod_data['path'] in color_map:
            lod_data['itemStyle'] = {'color': color_map[lod_data['path']]}
        
        if 'children' in lod_data:
            for child in lod_data['children']:
                self._apply_colors_to_lod(child, color_map)
    
    def expand_aggregation(self, parent_path: str, aggregation_path: str):
        """
        Expand an aggregated group to show its contents.
        This allows drilling into "Other Items" groups.
        """
        logging.info(f"Expanding aggregation: {aggregation_path}")
        
        # Find the parent node
        parent_node = self._find_node_by_path(parent_path)
        if not parent_node:
            return []
        
        # Extract aggregation type from path (e.g., "[folders]", "[.py_files]")
        if '[' in aggregation_path and ']' in aggregation_path:
            agg_type = aggregation_path[aggregation_path.rindex('['):aggregation_path.rindex(']')+1]
            
            # Find matching items based on aggregation type
            items = []
            if agg_type == '[folders]':
                items = [child for child in parent_node.children if child.is_dir]
            elif '_files]' in agg_type:
                ext = agg_type.replace('[', '').replace('_files]', '')
                items = [child for child in parent_node.children 
                        if not child.is_dir and child.name.endswith(ext)]
            else:
                # Return smaller items that didn't meet threshold
                threshold = self.lod_system.calculate_adaptive_threshold(
                    self._viewport_size, self._current_zoom, 0, parent_node.get_size()
                )
                items = [child for child in parent_node.children 
                        if child.get_size() / parent_node.get_size() < threshold]
            
            # Convert to display format
            result = []
            for item in items[:50]:  # Limit to prevent UI overload
                result.append({
                    'name': item.name,
                    'path': item.path,
                    'value': item.get_size(),
                    'expandable': item.is_dir
                })
            
            return sorted(result, key=lambda x: x['value'], reverse=True)
        
        return []
    
    def get_progressive_detail(self, path: str, zoom: float, visible_bounds: dict):
        """
        Get progressive detail for visible area only (viewport culling).
        This improves performance by only computing detail for visible regions.
        """
        if not self._scan_result_root:
            return None
        
        logging.info(f"Getting progressive detail for visible area: {visible_bounds}")
        
        node = self._find_node_by_path(path)
        if not node:
            return None
        
        # Only compute LOD for items that would be visible
        # This is a simplified version - full implementation would do spatial culling
        lod_data = self.get_adaptive_lod_view(path, zoom)
        
        # Add visibility flags based on bounds
        if lod_data and 'children' in lod_data:
            self._mark_visible_items(lod_data['children'], visible_bounds)
        
        return lod_data
    
    def _mark_visible_items(self, items: List[dict], bounds: dict):
        """Mark items that are within visible bounds."""
        # Simplified visibility check - full implementation would use spatial indexing
        for item in items:
            item['visible'] = True  # Simplified - all items visible for now
            if 'children' in item:
                self._mark_visible_items(item['children'], bounds)
    
    def get_sunburst_adaptive_view(self, path: str, max_depth: int = 3):
        """
        Get an adaptive Sunburst view with clear hierarchy and expandable aggregations.
        """
        if not self._scan_result_root:
            return None
        
        logging.info(f"Generating adaptive Sunburst view for: {path}")
        
        node = self._find_node_by_path(path)
        if not node:
            return None
        
        # Build sunburst-optimized LOD tree
        sunburst_data = self._build_sunburst_lod(node, max_depth)
        
        return sunburst_data
    
    def _build_sunburst_lod(self, node: FileNode, max_depth: int, current_depth: int = 0) -> dict:
        """
        Build Sunburst-optimized LOD tree with clear parent-child relationships.
        """
        node_size = node.get_size()
        data = {
            'name': node.name,
            'path': node.path,
            'value': node_size if node_size > 0 else 1,
            'depth': current_depth,
            'is_dir': node.is_dir,  # <-- ADD THIS LINE
            'children': []
        }
        
        if current_depth >= max_depth or not node.is_dir or not node.children:
            if node.is_dir and node.children:
                data['hasMore'] = True
            return data
        
        # For Sunburst, use a different aggregation strategy
        sorted_children = sorted(node.children, key=lambda c: c.get_size(), reverse=True)
        
        # Dynamic limit based on depth
        limit = 20 - (current_depth * 5)  # Fewer items as we go deeper
        limit = max(limit, 5)
        
        children_data = []
        
        if len(sorted_children) <= limit:
            # Show all children
            for child in sorted_children:
                children_data.append(
                    self._build_sunburst_lod(child, max_depth, current_depth + 1)
                )
        else:
            # Show top N and aggregate the rest
            for child in sorted_children[:limit]:
                children_data.append(
                    self._build_sunburst_lod(child, max_depth, current_depth + 1)
                )
            
            # Smart aggregation of remaining items
            remaining = sorted_children[limit:]
            remaining_size = sum(c.get_size() for c in remaining)
            
            # Group remaining by type
            remaining_dirs = [c for c in remaining if c.is_dir]
            remaining_files = [c for c in remaining if not c.is_dir]
            
            if remaining_dirs:
                dirs_size = sum(d.get_size() for d in remaining_dirs)
                children_data.append({
                    'name': f'{len(remaining_dirs)} more folders',
                    'path': f'{node.path}\\[more_folders]',
                    'value': dirs_size if dirs_size > 0 else 1,
                    'itemStyle': {'color': '#4a90e2', 'opacity': 0.7},
                    'expandable': True,
                    'aggregated': True,
                    'children': []  # Ensure children array exists
                })
            
            if remaining_files:
                files_size = sum(f.get_size() for f in remaining_files)
                children_data.append({
                    'name': f'{len(remaining_files)} more files',
                    'path': f'{node.path}\\[more_files]',
                    'value': files_size if files_size > 0 else 1,
                    'itemStyle': {'color': '#888', 'opacity': 0.7},
                    'expandable': True,
                    'aggregated': True,
                    'children': []  # Ensure children array exists
                })
        
        data['children'] = children_data
        return data
    
    def start_scan(self, path):
        """Start a scan with progressive updates."""
        # Return immediately to avoid blocking
        def scan_wrapper():
            self._start_scan_impl(path)
        threading.Thread(target=scan_wrapper).start()
        return None  # Don't return a promise

    def _start_scan_impl(self, path):
        """Implementation of scan with robust frontend communication."""
        def scan_thread_target(path):
            try:
                logging.info(f"Starting progressive scan of {path}")
                
                # --- Quick Scan ---
                # This part remains the same.
                self._quick_scan(path)
                
                # --- Full Scan ---
                # This performs the long, blocking scan.
                root_node = scanner.scan_directory(path, use_cache=False)

                if root_node:
                    # Store the complete scan result in the API object. This is now the source of truth.
                    self._set_scan_result(root_node)
                    logging.info("Full scan complete. Notifying frontend to pull final data.")
                    
                    # --- FIX: Send a simple notification, not the whole dataset ---
                    # This is a small, lightweight message that is much more likely to succeed.
                    window.evaluate_js('onScanFinallyComplete()')
                else:
                    # If the full scan fails, notify the frontend.
                    window.evaluate_js('onScanFailed("Full scan returned no data")')

            except Exception as e:
                logging.error(f"Scan error: {e}", exc_info=True)
                window.evaluate_js('onScanFailed("An error occurred during the full scan.")')
        
        scan_thread = threading.Thread(target=scan_thread_target, args=(path,))
        scan_thread.start()

    def get_final_scan_data(self):
        """Called by the frontend after it receives the onScanFinallyComplete signal."""
        logging.info("Frontend requested final scan data")
        
        if not self._scan_result_root:
            logging.error("Frontend requested final data, but scan result root is None")
            return None
        
        # Log scan result details for debugging
        root_size = self._scan_result_root.get_size()
        child_count = len(self._scan_result_root.children) if self._scan_result_root.children else 0
        logging.info(f"Scan data available: root={self._scan_result_root.path}, size={root_size}, children={child_count}")
        logging.info(f"Current viewport: {self._viewport_size}")
        
        try:
            # Generate the view from the fully scanned and stored root node.
            result = self.get_structure_view(self._scan_result_root.path)
            if result:
                logging.info("Successfully generated final scan structure view")
            else:
                logging.error("get_structure_view returned None for final scan data")
            return result
        except Exception as e:
            logging.error(f"Error generating final scan data: {e}", exc_info=True)
            return None

    def _quick_scan(self, path: str):
        """Perform a quick shallow scan for immediate feedback."""
        try:
            quick_root = FileNode(path=path, name=os.path.basename(path) or path, is_dir=True)
            
            # Quick scan - only immediate children
            try:
                for entry in os.scandir(path):
                    try:
                        if entry.is_dir():
                            size = self._estimate_dir_size(entry.path)
                            child = FileNode(
                                path=entry.path,
                                name=entry.name,
                                size=size,
                                is_dir=True
                            )
                        else:
                            stat = entry.stat()
                            child = FileNode(
                                path=entry.path,
                                name=entry.name,
                                size=stat.st_size,
                                is_dir=False
                            )
                        quick_root.children.append(child)
                    except:
                        continue
            except PermissionError:
                logging.warning(f"Permission denied for quick scan of {path}")
            
            # Send quick preview
            self._set_scan_result(quick_root, warm_indexes=False)
            preview_data = self.get_structure_view(path) # START IN STRUCTURE MODE
            if preview_data:
                # Ensure preview data has proper structure
                if 'children' not in preview_data:
                    preview_data['children'] = []
                window.evaluate_js(f'onQuickPreview({json.dumps(preview_data)})')
            
        except Exception as e:
            logging.error(f"Quick scan error: {e}")
    
    def _estimate_dir_size(self, path: str) -> int:
        """Quickly estimate directory size (shallow)."""
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    total += entry.stat().st_size
        except:
            pass
        return total
    
    def load_from_cache(self, path):
        """Load from cache with LOD view."""
        # Return immediately and process in thread
        def load_wrapper():
            self._load_from_cache_impl(path)
        threading.Thread(target=load_wrapper).start()
        return None  # Don't return a promise

    def _load_from_cache_impl(self, path):
        """
        Implementation of cache loading, now with an administrator check
        to decide whether to use the fast USN Journal refresh.
        """
        try:
            # --- START OF THE CRITICAL FIX ---
            if is_admin():
                logging.info(f"Admin rights detected. Attempting USN Journal refresh for: {path}")
                # The scanner performs the USN operation and RETURNS a result.
                root_node = scanner.refresh_from_cache(path)
            else:
                logging.warning("No admin rights. Falling back to a full scan for refresh.")
                # Inform the user why it will be slow.
                window.evaluate_js('onScanFailed("Admin rights needed for fast refresh. Performing full scan...")')
                # Perform a regular, slow scan as a fallback.
                root_node = scanner.scan_directory(path)
            # --- END OF THE CRITICAL FIX ---

            if root_node:
                logging.info("Scan/Refresh successful, building initial view.")
                self._set_scan_result(root_node)
                initial_lod = self.get_structure_view(path)
                
                if initial_lod:
                    if 'children' not in initial_lod:
                        initial_lod['children'] = []
                    self._initial_pruned_data = deepcopy(initial_lod)
                    window.evaluate_js(f'onScanComplete({json.dumps(initial_lod)})')
                else:
                    logging.warning("Scan/Refresh resulted in a valid but empty node.")
                    window.evaluate_js('onCacheMiss()')
            else:
                logging.error("The scan/refresh operation returned None.")
                window.evaluate_js('onScanFailed("Scan or refresh operation failed.")')

        except Exception as e:
            logging.error(f"CRITICAL FAILURE in _load_from_cache_impl: {e}", exc_info=True)
            window.evaluate_js('onScanFailed("Cache refresh error. Check analyzer.log for details.")')
    
    def _on_live_data_changed(self, changed_paths):
        """
        Callback for new shared live update system
        Forwards to JavaScript using the same method as legacy system
        """
        try:
            logging.info(f"Live data changed via shared system: {len(changed_paths)} paths affected")
            self._invalidate_top_list_cache()
            self._schedule_top_list_index_build()
            
            # Notify JavaScript about the live updates
            # Re-use existing JavaScript callback mechanism
            window.evaluate_js(f'''
                if (typeof onLiveUpdatesDetected === 'function') {{
                    onLiveUpdatesDetected({len(changed_paths)});
                }}
            ''')
            
            # If we have a current root, update the visualization
            if self._scan_result_root:
                # Force a refresh of the current view
                try:
                    current_data = self.get_data()
                    if current_data and current_data.get('items'):
                        window.evaluate_js(f'onScanComplete({json.dumps(current_data)});')
                except Exception as e:
                    logging.error(f"Error refreshing view after live updates: {e}")
                    
        except Exception as e:
            logging.error(f"Error in live update callback: {e}")
    
    def open_location(self, path: str):
        """
        Open the specified file or folder location in Windows Explorer.
        If path is a file, opens the containing folder and selects the file.
        If path is a folder, opens the folder directly.
        """
        try:
            logging.info(f"Opening location: {path}")
            
            # Ensure we're on Windows
            if platform.system() != 'Windows':
                logging.error("Open location is only supported on Windows")
                return False
            
            # Check if path exists
            if not os.path.exists(path):
                logging.error(f"Path does not exist: {path}")
                return False
            
            if os.path.isfile(path):
                # For files, open the containing folder and select the file
                subprocess.run(['explorer', '/select,', path], check=True)
                logging.info(f"Opened folder and selected file: {path}")
            else:
                # For directories, open the folder directly
                subprocess.run(['explorer', path], check=True)
                logging.info(f"Opened folder: {path}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to open location {path}: {e}")
            return False
        except Exception as e:
            logging.error(f"Error opening location {path}: {e}")
            return False
    
    def search_nodes(self, query: str):
        """Search with relevance scoring."""
        if not self._scan_result_root or not query:
            return []
        
        logging.info(f"Searching for: '{query}'")
        results = []
        SEARCH_LIMIT = 50
        
        lower_query = query.lower()
        q = deque([self._scan_result_root])
        
        while q and len(results) < SEARCH_LIMIT:
            current = q.popleft()
            
            # Score based on match quality
            score = 0
            name_lower = current.name.lower()
            
            if lower_query in name_lower:
                # Higher score for exact matches
                if lower_query == name_lower:
                    score = 100
                # Higher score for prefix matches
                elif name_lower.startswith(lower_query):
                    score = 80
                # Standard score for contains
                else:
                    score = 50
                
                # Bonus for larger files
                size_bonus = min(20, math.log10(current.get_size() + 1) * 2)
                score += size_bonus
                
                results.append({
                    'name': current.name,
                    'path': current.path,
                    'value': current.get_size(),
                    'score': score
                })
            
            for child in current.children:
                q.append(child)
        
        # Sort by score
        results.sort(key=lambda x: x['score'], reverse=True)
        
        # Remove score from final results
        for r in results:
            del r['score']
        
        return results
    
    def _find_node_by_path(self, path: str) -> Optional[FileNode]:
        """Find a node by path within the API's own root_node."""
        if not self._scan_result_root or not path.startswith(self._scan_result_root.path):
            return None
        
        q = deque([self._scan_result_root])
        while q:
            current = q.popleft()
            if current.path == path:
                return current
            if path.startswith(current.path):
                for child in current.children:
                    q.append(child)
        return None

    def start_live_updates(self, path: str):
        """API endpoint to start the live file system watcher."""
        if not self._scan_result_root:
            logging.warning("Cannot start live updates without a loaded scan.")
            return False
        
        # Try using shared live update system first
        if self.live_update_manager:
            if self.live_update_manager.is_monitoring():
                logging.warning("Shared live update manager is already running.")
                return True
            # Set the root_node on the scanner for the live update manager to use
            scanner.root_node = self._scan_result_root
            return self.live_update_manager.start_monitoring(path)
        
        # Fallback to legacy system
        if self.observer and self.observer.is_alive():
            logging.warning("Watcher is already running.")
            return True
        
        try:
            logging.info(f"Starting legacy live watcher for: {path}")
            self._stop_live_updates_flag.clear()
            event_handler = LiveUpdateHandler(self.event_queue)
            self.observer = Observer()
            self.observer.schedule(event_handler, path, recursive=True)
            self.observer.start()
            
            self.live_update_thread = threading.Thread(target=self._process_event_queue, daemon=True)
            self.live_update_thread.start()
            return True
        except Exception as e:
            logging.error(f"Failed to start live watcher: {e}")
            return False

    def stop_live_updates(self):
        """API endpoint to stop the live file system watcher."""
        # Try stopping shared live update system first
        if self.live_update_manager and self.live_update_manager.is_monitoring():
            return self.live_update_manager.stop_monitoring()
        
        # Fallback to legacy system
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            self._stop_live_updates_flag.set()
            self.event_queue.put(None) # Sentinel to unblock the thread
            self.live_update_thread.join()
            logging.info("Legacy live watcher stopped.")
        self.observer = None
        return True

    def _process_event_queue(self):
        """Worker thread to process file system events with robust error handling."""
        db_abs_path = os.path.abspath(scanner.db_path)
        log_abs_path = os.path.abspath(LOG_FILE_PATH)
        logging.info(f"Live update processor will ignore changes to: {db_abs_path} and {log_abs_path}")

        while not self._stop_live_updates_flag.is_set():
            try:
                event = self.event_queue.get(timeout=1)
                if event is None: break

                events_batch = [event]
                time.sleep(0.2)
                while not self.event_queue.empty():
                    next_event = self.event_queue.get_nowait()
                    if next_event is None: return
                    events_batch.append(next_event)

                logging.info(f"Processing batch of {len(events_batch)} file system events.")
                affected_parents = set()

                for evt in events_batch:
                    # --- START OF THE DEFINITIVE FIX ---
                    # 1. Check against the app's own files (log, db)
                    paths_to_check = [evt.src_path]
                    if hasattr(evt, 'dest_path'):
                        paths_to_check.append(evt.dest_path)

                    should_ignore = False
                    for path in paths_to_check:
                        event_abs_path = os.path.abspath(path)
                        if event_abs_path == db_abs_path or event_abs_path == log_abs_path:
                            should_ignore = True
                            break
                    if should_ignore:
                        continue
                    
                    # 2. Check against common OS/application noise patterns
                    normalized_path = evt.src_path.replace('\\', '/')
                    if any(pattern in normalized_path for pattern in LIVE_UPDATE_IGNORE_PATTERNS):
                        continue # Skip this noisy event
                    # --- END OF THE DEFINITIVE FIX ---
                    
                    try:
                        path = evt.src_path
                        parent_dir = os.path.dirname(path)
                        parent_node = self._find_node_by_path(parent_dir)

                        if not parent_node: continue
                        
                        node_in_tree = self._find_node_by_path(path)

                        if evt.event_type == 'deleted' and node_in_tree:
                            parent_node.children.remove(node_in_tree)
                            logging.info(f"Live Update: DELETED {path}")
                            parent_node.invalidate_size_cache()
                            affected_parents.add(parent_dir)

                        elif evt.event_type == 'created':
                            if not os.path.exists(path): continue
                            stat = os.stat(path)
                            new_node = FileNode(path=path, name=os.path.basename(path), size=stat.st_size, is_dir=os.path.isdir(path), parent=parent_node)
                            parent_node.children.append(new_node)
                            logging.info(f"Live Update: CREATED {path}")
                            parent_node.invalidate_size_cache()
                            affected_parents.add(parent_dir)

                        elif evt.event_type == 'modified' and node_in_tree:
                            if not os.path.exists(path): continue
                            stat = os.stat(path)
                            if node_in_tree.size != stat.st_size:
                                node_in_tree.size = stat.st_size
                                logging.info(f"Live Update: MODIFIED {path}")
                                parent_node.invalidate_size_cache()
                                affected_parents.add(parent_dir)
                        
                        elif evt.event_type == 'moved':
                            dest_path = evt.dest_path
                            if node_in_tree:
                                parent_node.children.remove(node_in_tree)
                                parent_node.invalidate_size_cache()
                                affected_parents.add(parent_dir)
                            
                            if os.path.exists(dest_path):
                                new_parent_dir = os.path.dirname(dest_path)
                                new_parent_node = self._find_node_by_path(new_parent_dir)
                                if new_parent_node:
                                    stat = os.stat(dest_path)
                                    moved_node = FileNode(path=dest_path, name=os.path.basename(dest_path), size=stat.st_size, is_dir=os.path.isdir(dest_path), parent=new_parent_node)
                                    new_parent_node.children.append(moved_node)
                                    new_parent_node.invalidate_size_cache()
                                    affected_parents.add(new_parent_dir)
                    
                    except FileNotFoundError:
                        logging.warning(f"File vanished before processing: {evt.src_path}. Skipping.")
                        continue
                    except Exception as e:
                        logging.error(f"Error processing event for {evt.src_path}: {e}")
                        continue

                if affected_parents:
                    self._on_live_data_changed(list(affected_parents))
            except queue.Empty:
                continue
            
    def _on_live_data_changed(self, changed_parent_dirs: List[str]):
        """Callback that is triggered when the in-memory tree is modified."""
        if not changed_parent_dirs:
            return
        
        logging.info(f"Live data change detected. Notifying frontend about: {changed_parent_dirs}")
        self._invalidate_top_list_cache()
        self._schedule_top_list_index_build()

        if self._scan_result_root:
            scanner.cache_saver.schedule_save(self._scan_result_root)

        try:
            unique_parents = list(set(changed_parent_dirs))
            window.evaluate_js(f'onDataChanged_v2({json.dumps(unique_parents)})')
        except Exception as e:
            logging.error(f"Failed to notify frontend of live data change: {e}")

    def get_live_update_payload(self, path: str, view_mode: str, zoom: float = 1.0):
        """
        Finds the node for the given path and returns its new, updated representation
        based on the specified view mode. This is the core of the patch-based update.
        """
        if not self._scan_result_root:
            return None

        node_to_update = self._find_node_by_path(path)
        if not node_to_update:
            return None

        logging.info(f"Generating live update payload for '{path}' in '{view_mode}' mode.")
        
        # Invalidate the size cache for the node and its parents to ensure it's fresh.
        node_to_update.invalidate_size_cache()

        # Generate the correct data structure based on the frontend's current view.
        if view_mode == 'sunburst':
            return self._build_sunburst_lod(node_to_update, max_depth=4)
        elif view_mode == 'heatmap':
            # Note: The 'largest_files_only' flag isn't passed here for simplicity,
            # assuming we are updating the hierarchical view.
            return self.lod_system.build_lod_tree(node_to_update, self._viewport_size, zoom)
        else: # Default to 'structure' mode
            return self.lod_system.build_lod_tree(node_to_update, self._viewport_size, 1.0)

    def _generate_distinct_colors(self, n):
        """Generate visually distinct colors using golden ratio."""
        colors = []
        golden_ratio = 0.618033988749895
        hue = 0.1
        
        for i in range(n):
            hue += golden_ratio
            hue %= 1.0
            # Vary saturation and value for more distinction
            saturation = 0.5 + (i % 3) * 0.2
            value = 0.7 + (i % 2) * 0.15
            rgb = colorsys.hsv_to_rgb(hue, saturation, value)
            hex_color = '#{:02x}{:02x}{:02x}'.format(
                int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)
            )
            colors.append(hex_color)
        
        return colors

    def _get_largest_files_recursively(self, node: FileNode, limit: int = 200) -> dict:
        """
        Recursively finds the N largest files within a directory tree and returns
        a synthetic treemap data structure.
        """
        all_files = []
        q = deque([node])
        while q:
            current = q.popleft()
            for child in current.children:
                if child.is_dir:
                    q.append(child)
                else:
                    all_files.append(child)
        
        sorted_files = sorted(all_files, key=lambda f: f.size, reverse=True)
        
        largest_files_data = []
        remaining_size = 0
        
        for i, file_node in enumerate(sorted_files):
            if i < limit:
                largest_files_data.append({
                    'name': file_node.name,
                    'path': file_node.path,
                    'value': file_node.size if file_node.size > 0 else 1,
                    'is_dir': False # Explicitly a file
                })
            else:
                remaining_size += file_node.size
        
        if remaining_size > 0:
            largest_files_data.append({
                'name': f'{len(sorted_files) - limit} smaller files',
                'path': f'{node.path}\\[remaining_files]',
                'value': remaining_size,
                'is_dir': True, # Treat as a group
                'aggregated': True,
                'itemStyle': {'color': '#666'}
            })
            
        return {
            'name': node.name,
            'path': node.path,
            'value': node.get_size(),
            'children': largest_files_data,
            'isLargestFileView': True # Add a flag for the frontend
        }

    def _flatten_tree_nodes(self, node: FileNode) -> List[FileNode]:
        """Helper to create a flat list of all nodes in a tree, iteratively."""
        if not node:
            return []
        nodes = []
        q = deque([node])
        while q:
            current = q.popleft()
            nodes.append(current)
            if current.is_dir:
                q.extend(current.children)
        return nodes

    def get_largest_consumers(self, consumer_type: str, offset: int = 0, limit: int = 50):
        """
        Gets a sorted, paginated list of the largest files or folders.
        Uses a pre-built index when available (built once after scan/cache load).
        """
        if not self._scan_result_root:
            return {'items': [], 'total': 0}

        logging.info(f"Fetching largest consumers: type={consumer_type}, offset={offset}, limit={limit}")

        with self._top_list_lock:
            consumers = self._top_list_cache.get(consumer_type)

        if consumers is None:
            if self._top_list_build_thread and self._top_list_build_thread.is_alive():
                self._top_list_build_thread.join(timeout=120.0)
            with self._top_list_lock:
                consumers = self._top_list_cache.get(consumer_type)
            if consumers is None:
                self._build_top_list_index()
                with self._top_list_lock:
                    consumers = self._top_list_cache.get(consumer_type) or []

        total_count = len(consumers)
        paginated = consumers[offset: offset + limit]
        return {'items': paginated, 'total': total_count}
    
    
    
    def _format_size(self, size: int) -> str:
        """Format size in human readable format"""
        if size == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
    
    def _build_lazy_folder_nodes(self, parent_node: FileNode) -> List[dict]:
        """Build one jsTree level — immediate subdirectories only."""
        subdirs = [child for child in parent_node.children if child.is_dir]
        nodes = []
        for subdir in sorted(subdirs, key=lambda x: x.name.lower()):
            has_subdirs = any(child.is_dir for child in subdir.children)
            node = {
                "id": subdir.path,
                "text": subdir.name,
                "type": "folder",
                "data": {
                    "path": subdir.path,
                    "is_dir": True,
                },
            }
            if has_subdirs:
                node["children"] = True
            nodes.append(node)
        return nodes

    def _resolve_tree_node(self, path: str = None) -> Optional[FileNode]:
        if not self._scan_result_root:
            return None
        if not path or path == self._scan_result_root.path:
            return self._scan_result_root
        return self._find_node_by_path(path)

    def get_directory_tree_root(self, path: str = None) -> Dict:
        """Return the scan root as a single lazy jsTree node (no full-tree walk)."""
        try:
            root_node = self._resolve_tree_node(path)
            if not root_node:
                return {"error": "No data loaded"}

            start_time = time.time()
            is_drive_root = len(root_node.path) <= 3 and root_node.path[1:2] == ':'
            has_subdirs = any(child.is_dir for child in root_node.children)
            node = {
                "id": root_node.path,
                "text": root_node.name or root_node.path,
                "type": "drive" if is_drive_root else "folder",
                "data": {
                    "path": root_node.path,
                    "is_dir": True,
                },
            }
            if has_subdirs:
                node["children"] = True

            child_count = sum(1 for child in root_node.children if child.is_dir)
            elapsed = time.time() - start_time
            logging.info(
                f"Lazy tree root for {root_node.path} in {elapsed:.3f}s "
                f"({child_count} immediate folders)"
            )
            return {
                "success": True,
                "node": node,
                "stats": {
                    "root_path": root_node.path,
                    "child_count": child_count,
                    "load_time": elapsed,
                },
            }
        except Exception as e:
            logging.error(f"Error building lazy tree root: {e}")
            return {"error": str(e)}

    def get_directory_tree_children(self, parent_path: str) -> Dict:
        """Return immediate subdirectory children for lazy jsTree expansion."""
        try:
            parent_node = self._resolve_tree_node(parent_path)
            if not parent_node:
                return {"error": f"Path not found: {parent_path}"}
            if not parent_node.is_dir:
                return {"error": f"Path is not a directory: {parent_path}"}

            start_time = time.time()
            nodes = self._build_lazy_folder_nodes(parent_node)
            elapsed = time.time() - start_time
            logging.info(
                f"Lazy tree children for {parent_path}: {len(nodes)} folders in {elapsed:.3f}s"
            )
            return {
                "success": True,
                "nodes": nodes,
                "stats": {
                    "parent_path": parent_path,
                    "child_count": len(nodes),
                    "load_time": elapsed,
                },
            }
        except Exception as e:
            logging.error(f"Error building lazy tree children for {parent_path}: {e}")
            return {"error": str(e)}

    def get_directory_tree(self, path: str = None) -> Dict:
        """Backward-compatible alias: returns lazy root node only."""
        result = self.get_directory_tree_root(path)
        if result.get("error"):
            return result
        return {
            "success": True,
            "data": result["node"],
            "stats": {
                "directories": result["stats"]["child_count"] + 1,
                "load_time": result["stats"]["load_time"],
                "root_path": result["stats"]["root_path"],
            },
        }
    
    def get_directory_contents(self, path: str) -> Dict:
        """Get files and folders in a specific directory (Windows Explorer style)"""
        try:
            if not self._scan_result_root:
                return {"error": "No data loaded"}
                
            # Find the target directory
            target_node = self._find_node_by_path(path)
            if not target_node:
                return {"error": f"Directory not found: {path}"}
                
            if not target_node.is_dir:
                return {"error": f"Path is not a directory: {path}"}
            
            logging.info(f"Loading directory contents: {path}")
            start_time = time.time()
            
            contents = []
            
            # Sort children by size (largest first)
            sorted_children = sorted(target_node.children, key=lambda x: x.get_size(), reverse=True)
            
            for child in sorted_children:
                item = {
                    "name": child.name,
                    "path": child.path,
                    "size": child.get_size(),
                    "size_formatted": self._format_size(child.get_size()),
                    "is_dir": child.is_dir,
                    "type": "Folder" if child.is_dir else self._get_file_type(child.name),
                    "modified": getattr(child, 'mtime', 0)
                }
                
                # Add additional info for directories
                if child.is_dir:
                    # Calculate counts from immediate children (not stored attributes)
                    file_count = sum(1 for c in child.children if not c.is_dir)
                    dir_count = sum(1 for c in child.children if c.is_dir)
                    item["file_count"] = file_count
                    item["dir_count"] = dir_count
                    item["items_text"] = f"{file_count:,} files, {dir_count:,} folders"
                else:
                    item["items_text"] = ""
                
                contents.append(item)
            
            elapsed = time.time() - start_time
            logging.info(f"Directory contents loaded in {elapsed:.2f}s - {len(contents)} items")
            
            return {
                "success": True,
                "path": path,
                "contents": contents,
                "stats": {
                    "items": len(contents),
                    "files": len([c for c in contents if not c["is_dir"]]),
                    "folders": len([c for c in contents if c["is_dir"]]),
                    "load_time": elapsed
                }
            }
            
        except Exception as e:
            logging.error(f"Error loading directory contents: {e}")
            return {"error": str(e)}
    
    def _get_file_type(self, filename: str) -> str:
        """Get file type from extension"""
        try:
            ext = filename.split('.')[-1].lower() if '.' in filename else ""
            
            type_map = {
                'txt': 'Text File',
                'doc': 'Word Document', 'docx': 'Word Document',
                'pdf': 'PDF Document',
                'xls': 'Excel Spreadsheet', 'xlsx': 'Excel Spreadsheet',
                'ppt': 'PowerPoint Presentation', 'pptx': 'PowerPoint Presentation',
                'jpg': 'JPEG Image', 'jpeg': 'JPEG Image', 'png': 'PNG Image', 'gif': 'GIF Image',
                'mp4': 'MP4 Video', 'avi': 'AVI Video', 'mov': 'QuickTime Video',
                'mp3': 'MP3 Audio', 'wav': 'WAV Audio',
                'zip': 'ZIP Archive', 'rar': 'RAR Archive',
                'exe': 'Application',
                'py': 'Python File',
                'js': 'JavaScript File',
                'html': 'HTML File', 'htm': 'HTML File',
                'css': 'CSS File'
            }
            
            return type_map.get(ext, f"{ext.upper()} File" if ext else "File")
            
        except Exception:
            return "File"
    

def main():
    import sys
    api = Api()
    
    # Check for command line arguments for auto-loading
    auto_load_path = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--auto-load":
        auto_load_path = sys.argv[2]
        logging.info(f"Visual analyzer will auto-load: {auto_load_path}")
    
    global window
    window = webview.create_window(
        'DiskInsight Pro - Enhanced Visual Analyzer',
        'webview_ui/index.html',
        js_api=api,
        width=1280,
        height=800,
        min_size=(800, 600),
        # --- START OF FIX ---
        # easy_drag=True can interfere with mouse events like scrolling.
        # Disabling it is crucial for interactive charts.
        easy_drag=False
        # --- END OF FIX ---
    )
    
    # Set up auto-loading - try specified path or detect available drives
    def on_window_ready():
        # Give the window a moment to fully initialize
        import time
        time.sleep(1)
        try:
            target_path = auto_load_path
            
            # If no specific path provided, find a cached drive (metadata only — no pickle load)
            if not target_path:
                cached_drives = scanner.list_cached_drives()
                if cached_drives:
                    target_path = cached_drives[0]
                    logging.info(f"Found cached data for drive: {target_path}")
                else:
                    for drive in api.get_drives():
                        if scanner.cache_exists(drive):
                            target_path = drive
                            logging.info(f"Found cached data for drive: {drive}")
                            break
            
            if target_path:
                logging.info(f"Auto-loading cache for: {target_path}")
                api.load_from_cache(target_path)
            else:
                logging.info("No cached data found - user will need to scan or load manually")
                # Show popup asking if user wants to scan
                def show_no_cache_popup():
                    time.sleep(0.5)  # Brief delay to let UI fully load
                    available_drives = api.get_drives()
                    if available_drives:
                        first_drive = available_drives[0]
                        # Properly escape the drive path for JavaScript
                        escaped_drive = json.dumps(first_drive)
                        window.evaluate_js(f'''
                            if (confirm("No cached data found. Would you like to perform a fresh scan of " + {escaped_drive} + "?")) {{
                                pywebview.api.start_scan({escaped_drive});
                            }}
                        ''')
                threading.Thread(target=show_no_cache_popup, daemon=True).start()
                
        except Exception as e:
            logging.error(f"Auto-load failed: {e}")
    
    # Always start auto-load (either with specified path or auto-detection)
    import threading
    threading.Thread(target=on_window_ready, daemon=True).start()
    
    webview.start(debug=False)


if __name__ == '__main__':
    import json
    main()