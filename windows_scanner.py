"""
Windows-optimized directory listing and skip rules for disk scanning.
"""

import os
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import win32file
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# System folders: shallow size estimate, do not recurse
SKIP_DIRS_SYSTEM = {
    '$RECYCLE.BIN',
    'System Volume Information',
    'Recovery',
    'Windows.old',
    'Config.Msi',
    '$Windows.~BT',
    '$Windows.~WS',
}

# Dev/build artifacts: shallow size estimate, do not recurse
SKIP_DIRS_DEV = {
    'node_modules',
    '.git',
    '__pycache__',
    '.venv',
    'venv',
    'env',
    '.idea',
    '.vscode',
    'target',
    'build',
    'dist',
    'out',
    'bin',
    'obj',
    '.gradle',
    '.m2',
    'bower_components',
    'vendor',
    'packages',
}

SKIP_DIRS_SCAN = SKIP_DIRS_SYSTEM | SKIP_DIRS_DEV


@dataclass
class DirEntry:
  name: str
  size: int
  is_dir: bool
  mtime: float = 0.0
  skip_recurse: bool = False


class WindowsAPIScanner:
    """Fast directory listing via Win32 FindFilesW when available."""

    @staticmethod
    def fast_listdir(path: str) -> List[Tuple[str, int, bool, float]]:
        if HAS_WIN32:
            try:
                results: List[Tuple[str, int, bool, float]] = []
                for file_data in win32file.FindFilesW(os.path.join(path, '*')):
                    filename = file_data[8]
                    if filename in ('.', '..'):
                        continue
                    attributes = file_data[0]
                    size_high = file_data[4]
                    size_low = file_data[5]
                    file_size = (size_high << 32) + size_low
                    is_directory = bool(attributes & win32con.FILE_ATTRIBUTE_DIRECTORY)
                    mtime = 0.0
                    results.append((filename, file_size, is_directory, mtime))
                return results
            except OSError:
                pass
            except Exception as exc:
                logging.debug(f"FindFilesW failed for {path}: {exc}")
        return WindowsAPIScanner._scandir_listdir(path)

    @staticmethod
    def _scandir_listdir(path: str) -> List[Tuple[str, int, bool, float]]:
        results: List[Tuple[str, int, bool, float]] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            results.append((entry.name, stat.st_size, False, stat.st_mtime))
                        elif entry.is_dir(follow_symlinks=False):
                            results.append((entry.name, 0, True, 0.0))
                    except OSError:
                        continue
        except OSError:
            pass
        return results


def should_skip_recurse(dir_name: str) -> bool:
    return dir_name in SKIP_DIRS_SCAN


def list_directory_entries(path: str) -> Optional[List[DirEntry]]:
    """List one directory; returns None if the path cannot be read."""
    try:
        raw_entries = WindowsAPIScanner.fast_listdir(path)
    except OSError:
        return None

    entries: List[DirEntry] = []
    for name, size, is_dir, mtime in raw_entries:
        if is_dir and should_skip_recurse(name):
            entries.append(DirEntry(name=name, size=0, is_dir=True, mtime=mtime, skip_recurse=True))
        else:
            entries.append(DirEntry(name=name, size=size, is_dir=is_dir, mtime=mtime))
    return entries
