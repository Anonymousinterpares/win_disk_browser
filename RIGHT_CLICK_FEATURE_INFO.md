# Right-Click "Open Location" Feature

## Overview
Added right-click context menu functionality to open file/folder locations directly in Windows Explorer.

## How it Works

### Backend Changes (`visual_analyzer.py`)
- Added `open_location(path)` method to the `Api` class
- Uses `subprocess.run()` to call Windows Explorer with appropriate parameters
- For files: `explorer /select, {file_path}` - Opens containing folder and selects the file
- For folders: `explorer {folder_path}` - Opens the folder directly
- Includes error handling and logging
- Only works on Windows (platform check included)

### Frontend Changes (`main.js`)
- Enhanced `showContextMenu()` function to include "Open Location" option
- Improved right-click detection for both treemap and sunburst views
- Added context menu option: "🗂️ Open Location"
- Added proper error handling with status bar feedback
- Context menu now properly detects items under mouse cursor in both view modes

### CSS Changes (`style.css`)
- Added `.context-menu-separator` class for visual separation
- Improved context menu styling

## Usage
1. **Right-click on any file or folder** in either treemap or sunburst view
2. **Select "🗂️ Open Location"** from the context menu
3. **Windows Explorer will open** showing:
   - For files: The containing folder with the file selected
   - For folders: The folder itself

## Features
- Works in both **Structure Mode** and **Heatmap Mode** of treemap view
- Works in **Sunburst view**
- Only shows "Open Location" for real paths (not aggregated virtual groups)
- Provides feedback in the status bar
- Handles errors gracefully
- Includes visual separator in context menu for better organization

## Error Handling
- Checks if path exists before attempting to open
- Validates Windows platform compatibility
- Provides user feedback through status bar messages
- Logs all operations for debugging

## Context Menu Options (depending on view/mode)
1. **🗂️ Open Location** - Opens Windows Explorer (always available for real paths)
2. **📂 Show in Structure Mode** - Switch to structure mode (heatmap mode only)
3. **🔙 Spatial Zoom Out** - Navigate up (heatmap mode only)
4. **🔍 Explore This Folder** - Navigate into folder (heatmap mode only)
5. **☀️ Explore in Sunburst** - Navigate into folder (sunburst mode only)

## Technical Notes
- Uses `subprocess.run(['explorer', '/select,', path])` for files
- Uses `subprocess.run(['explorer', path])` for folders
- Implements proper mouse coordinate detection for both chart types
- Maintains compatibility with existing spatial zoom and navigation features
