"""
USN Journal helpers for incremental cache refresh on Windows.
"""

import logging
import os
import struct
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

try:
    import win32file
    import winioctlcon
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# Reason flags we care about for disk usage changes
USN_REASON_DATA_CHANGE = 0x00000001
USN_REASON_FILE_CREATE = 0x00000100
USN_REASON_FILE_DELETE = 0x00000200
USN_REASON_RENAME_NEW_NAME = 0x00002000
USN_REASON_CLOSE = 0x80000000
CHANGE_REASON_MASK = (
    USN_REASON_DATA_CHANGE
    | USN_REASON_FILE_CREATE
    | USN_REASON_FILE_DELETE
    | USN_REASON_RENAME_NEW_NAME
    | USN_REASON_CLOSE
)

MAX_USN_PATHS_BEFORE_FULL_SCAN = 500


@dataclass
class UsnJournalState:
    journal_id: int
    next_usn: int
    lowest_valid_usn: int


def _volume_handle(drive_path: str):
    drive_letter = drive_path[0] if drive_path else 'C'
    return win32file.CreateFile(
        f'\\\\.\\{drive_letter}:',
        win32file.GENERIC_READ,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )


def query_usn_journal(drive_path: str) -> Optional[UsnJournalState]:
    if not HAS_WIN32:
        return None
    try:
        handle = _volume_handle(drive_path)
        try:
            data = win32file.DeviceIoControl(
                handle,
                winioctlcon.FSCTL_QUERY_USN_JOURNAL,
                None,
                64,
            )
            if len(data) < 24:
                return None
            journal_id, _first, next_usn, lowest = struct.unpack('<QQQQ', data[:32])
            return UsnJournalState(
                journal_id=journal_id,
                next_usn=next_usn,
                lowest_valid_usn=lowest,
            )
        finally:
            win32file.CloseHandle(handle)
    except Exception as exc:
        logging.debug(f'USN journal query failed: {exc}')
        return None


def _parse_usn_records(buffer: bytes, start_offset: int = 8) -> List[Tuple[int, str]]:
    """Parse USN records; returns list of (reason, file_name)."""
    records: List[Tuple[int, str]] = []
    offset = start_offset
    while offset + 60 <= len(buffer):
        record_len = struct.unpack_from('<I', buffer, offset)[0]
        if record_len <= 0 or offset + record_len > len(buffer):
            break
        reason = struct.unpack_from('<I', buffer, offset + 32)[0]
        name_len = struct.unpack_from('<H', buffer, offset + 56)[0]
        name_offset = struct.unpack_from('<H', buffer, offset + 58)[0]
        name_start = offset + name_offset
        name_end = name_start + name_len
        if name_end <= len(buffer):
            file_name = buffer[name_start:name_end].decode('utf-16-le', errors='ignore')
            if file_name:
                records.append((reason, file_name))
        offset += record_len
    return records


def read_usn_changes(
    drive_path: str,
    start_usn: int,
    journal_id: int,
    max_records: int = 2000,
) -> Tuple[Set[str], int]:
    """
    Read USN journal records after start_usn.
    Returns a set of affected file/folder names and the latest USN seen.
    """
    names: Set[str] = set()
    latest_usn = start_usn

    if not HAS_WIN32 or start_usn <= 0:
        return names, latest_usn

    try:
        handle = _volume_handle(drive_path)
        try:
            read_data = struct.pack(
                '<QIIQQQ',
                start_usn,
                CHANGE_REASON_MASK,
                0,
                0,
                0,
                journal_id,
            )
            while len(names) < max_records:
                try:
                    buffer = win32file.DeviceIoControl(
                        handle,
                        winioctlcon.FSCTL_READ_USN_JOURNAL,
                        read_data,
                        65536,
                    )
                except OSError:
                    break

                if not buffer or len(buffer) <= 8:
                    break

                next_usn = struct.unpack_from('<Q', buffer, 0)[0]
                if next_usn <= latest_usn:
                    break
                latest_usn = next_usn

                for reason, file_name in _parse_usn_records(buffer):
                    if reason & CHANGE_REASON_MASK:
                        names.add(file_name)

                read_data = struct.pack(
                    '<QIIQQQ',
                    latest_usn,
                    CHANGE_REASON_MASK,
                    0,
                    0,
                    0,
                    journal_id,
                )
        finally:
            win32file.CloseHandle(handle)
    except Exception as exc:
        logging.debug(f'USN read failed: {exc}')

    return names, latest_usn


def resolve_changed_directories(
    root_path: str,
    changed_names: Set[str],
    find_node_by_path,
    root_node,
) -> Set[str]:
    """
    Map USN file names to directory paths in the cached tree for targeted rescan.
  Falls back to matching by basename under known paths.
    """
    dirs_to_rescan: Set[str] = set()
    root_path = os.path.abspath(root_path)

    for name in changed_names:
        if not name:
            continue
        if '\\' in name or '/' in name:
            candidate = os.path.normpath(os.path.join(root_path, name.replace('/', '\\')))
            node = find_node_by_path(root_node, candidate)
            if node:
                target = candidate if node.is_dir else os.path.dirname(candidate)
                if target:
                    dirs_to_rescan.add(target)
            continue

        # Short name only — walk tree for matches (bounded breadth-first)
        queue = [root_node]
        matches = 0
        while queue and matches < 20:
            current = queue.pop(0)
            for child in current.children:
                if child.name.lower() == name.lower():
                    matches += 1
                    target = child.path if child.is_dir else os.path.dirname(child.path)
                    if target:
                        dirs_to_rescan.add(target)
                if child.is_dir:
                    queue.append(child)

    return dirs_to_rescan
