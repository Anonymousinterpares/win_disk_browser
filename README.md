# DiskInsight Pro - Windows Disk Space Analyzer

A powerful and modern disk space analyzer for Windows 11 with a graphical user interface for managing file storage efficiently.

## Features (Phase 1 - Current)

### ✅ Implemented
- **Fast Directory Scanning**: Efficiently scans drives and folders with multi-threading support
- **Tree View Display**: Hierarchical view of your file system with size information
- **Smart Caching**: SQLite-based caching to speed up subsequent scans
- **Size Sorting**: Automatically sorts folders/files by size (largest first)
- **Real-time Progress**: Shows scanning progress with current path
- **Error Handling**: Gracefully handles permission errors and inaccessible files
- **Modern UI Option**: Supports CustomTkinter for modern dark theme (optional)
- **Details Panel**: Shows detailed information about selected items

### 🎯 Core Capabilities
- Scan any drive or folder
- View folder sizes and file counts
- Navigate through directory structure
- See which folders consume the most space
- Handle large directory structures efficiently

## Installation

### Prerequisites
- Python 3.8 or higher
- Windows 10/11

### Quick Start

1. Clone or download the project to your desired location:
   ```
   E:\AI\AI\projects\win_disk_browser\
   ```

2. Install optional dependencies for better UI (recommended):
   ```bash
   pip install customtkinter
   ```

3. Run the application:
   ```bash
   python disk_analyzer.py
   ```

## Usage

1. **Select Drive**: Choose a drive from the dropdown menu
2. **Click Scan**: Start scanning the selected drive
3. **Browse Results**: 
   - Click on folders to see details
   - Double-click to expand/collapse folders
   - View size and item count for each folder
4. **Stop Scan**: Click Stop button to cancel ongoing scan

## Architecture

### Core Components

- **FileSystemNode**: Tree structure for representing files/folders
- **DiskScanner**: Handles scanning operations and caching
- **DiskAnalyzerGUI**: Main GUI application using Tkinter/CustomTkinter

### Database Schema
The application uses SQLite for caching scan results:
- Path, size, type (file/folder)
- Modification time and scan time
- File and directory counts
- Parent-child relationships

## Upcoming Features (Phase 2-4)

### Phase 2: Search & Filter
- [ ] Advanced search by name, size, date
- [ ] Filter by file type/extension
- [ ] Find duplicate files
- [ ] Quick filters for large files

### Phase 3: File Operations
- [ ] Safe file deletion (with recycle bin)
- [ ] Move/copy operations
- [ ] Batch operations
- [ ] Program uninstallation

### Phase 4: Visualization & Polish
- [ ] Treemap visualization
- [ ] Sunburst charts
- [ ] Export reports
- [ ] Windows Explorer integration
- [ ] Scheduled scans

## Performance Notes

- First scan of a drive may take time depending on the number of files
- Subsequent scans are faster due to caching
- Large folders (>100 items) show only top 100 items by size
- Scanning is performed in a separate thread to keep UI responsive

## Troubleshooting

### Common Issues

1. **"Permission Denied" errors**: 
   - Normal for system folders
   - Application continues scanning accessible folders

2. **Slow scanning**:
   - First scan is always slower
   - Check if antivirus is interfering
   - Consider excluding very large folders initially

3. **UI looks basic**:
   - Install customtkinter for modern appearance:
   ```bash
   pip install customtkinter
   ```

## Development

### Project Structure
```
win_disk_browser/
├── disk_analyzer.py      # Main application
├── disk_cache.db        # SQLite cache (created on first run)
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

### Extending the Application

The modular design allows easy extension:
- Add new visualization types in separate modules
- Implement additional scanners for specific file types
- Create plugins for cloud storage integration

## License

This project is provided as-is for personal use.

## Contact

For issues or suggestions, please create an issue in the project repository.

---

**Note**: This is Phase 1 of a multi-phase project. Core functionality is complete and stable. Additional features will be added incrementally.
