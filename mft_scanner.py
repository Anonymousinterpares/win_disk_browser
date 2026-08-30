"""
NTFS MFT fast scan — reads the Master File Table sequentially from the volume.

Requires administrator privileges on NTFS volumes. Falls back to directory walking
when unavailable. Approach aligned with WizTree / DiskSleuth: one sequential read
pass over MFT records instead of per-path directory traversal.
"""

from __future__ import annotations

import logging
import os
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    import win32file
    import win32con
    import winioctlcon
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from windows_scanner import SKIP_DIRS_SCAN

ATTR_STANDARD_INFORMATION = 0x10
ATTR_FILE_NAME = 0x30
ATTR_END = 0xFFFFFFFF

FILE_NAME_WIN32 = 0x01
FILE_NAME_POSIX = 0x00

PROGRESS_INTERVAL = 0.1
MFT_READ_CHUNK_RECORDS = 512


@dataclass
class VolumeGeometry:
    bytes_per_sector: int
    bytes_per_cluster: int
    bytes_per_file_record: int
    mft_start_lcn: int


@dataclass
class MftEntry:
    index: int
    parent_index: int
    name: str
    is_dir: bool
    size: int
    mtime: float = 0.0
    frn: int = 0


def mft_index(frn: int) -> int:
    return frn & 0x0000FFFFFFFFFFFF


def is_ntfs_volume(path: str) -> bool:
    if not path or len(path) < 2:
        return False
    root = path[:3] if path[1] == ':' else path
    try:
        import ctypes
        fs_name = ctypes.create_unicode_buffer(64)
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            None,
            0,
            None,
            None,
            None,
            fs_name,
            len(fs_name),
        )
        return fs_name.value.upper() == 'NTFS'
    except Exception:
        return False


def _volume_handle(drive_path: str):
    drive_letter = drive_path[0].upper()
    return win32file.CreateFile(
        f'\\\\.\\{drive_letter}:',
        win32file.GENERIC_READ,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )


def _get_volume_geometry(handle) -> Optional[VolumeGeometry]:
    try:
        data = win32file.DeviceIoControl(
            handle,
            winioctlcon.FSCTL_GET_NTFS_VOLUME_DATA,
            None,
            128,
        )
        if len(data) < 96:
            return None
        bytes_per_sector = struct.unpack_from('<I', data, 40)[0]
        bytes_per_cluster = struct.unpack_from('<I', data, 44)[0]
        bytes_per_file_record = struct.unpack_from('<I', data, 48)[0]
        mft_start_lcn = struct.unpack_from('<Q', data, 64)[0]
        if bytes_per_file_record <= 0 or bytes_per_cluster <= 0:
            return None
        return VolumeGeometry(
            bytes_per_sector=bytes_per_sector,
            bytes_per_cluster=bytes_per_cluster,
            bytes_per_file_record=bytes_per_file_record,
            mft_start_lcn=mft_start_lcn,
        )
    except Exception as exc:
        logging.debug(f'FSCTL_GET_NTFS_VOLUME_DATA failed: {exc}')
        return None


def _apply_fixup(record: bytearray, geometry: VolumeGeometry) -> None:
    usa_offset = struct.unpack_from('<H', record, 4)[0]
    usa_count = struct.unpack_from('<H', record, 6)[0]
    if usa_offset == 0 or usa_count < 2:
        return
    sector_size = geometry.bytes_per_sector
    update_seq = struct.unpack_from('<H', record, usa_offset)[0]
    for sector_idx in range(1, usa_count):
        patch_offset = sector_idx * sector_size - 2
        if 0 <= patch_offset + 1 < len(record):
            struct.pack_into('<H', record, patch_offset, update_seq)


def _iter_attributes(record: bytes, first_attr_offset: int):
    offset = first_attr_offset
    record_len = len(record)
    while offset + 8 <= record_len:
        attr_type = struct.unpack_from('<I', record, offset)[0]
        if attr_type == ATTR_END:
            break
        attr_len = struct.unpack_from('<I', record, offset + 4)[0]
        if attr_len < 24 or offset + attr_len > record_len:
            break
        yield attr_type, record[offset: offset + attr_len]
        offset += attr_len


def _parse_file_name_attribute(attr: bytes) -> Optional[Tuple[int, str, int, float, int]]:
    if len(attr) < 0x18:
        return None
    non_resident = attr[8]
    if non_resident != 0:
        return None
    value_length = struct.unpack_from('<I', attr, 0x10)[0]
    value_offset = struct.unpack_from('<H', attr, 0x14)[0]
    if value_offset + value_length > len(attr):
        return None
    value = attr[value_offset: value_offset + value_length]
    if len(value) < 66:
        return None
    parent_frn = struct.unpack_from('<Q', value, 0)[0]
    real_size = struct.unpack_from('<Q', value, 48)[0]
    mtime_raw = struct.unpack_from('<Q', value, 16)[0]
    name_length = value[64]
    name_type = value[65]
    name_bytes = value[66: 66 + name_length * 2]
    try:
        name = name_bytes.decode('utf-16-le')
    except UnicodeDecodeError:
        return None
    mtime = _filetime_to_unix(mtime_raw)
    return parent_frn, name, real_size, mtime, name_type


def _parse_standard_information(attr: bytes) -> Optional[Tuple[int, float]]:
    if len(attr) < 0x18:
        return None
    if attr[8] != 0:
        return None
    value_offset = struct.unpack_from('<H', attr, 0x14)[0]
    value_length = struct.unpack_from('<I', attr, 0x10)[0]
    if value_offset + value_length > len(attr):
        return None
    value = attr[value_offset: value_offset + value_length]
    if len(value) < 56:
        return None
    mtime_raw = struct.unpack_from('<Q', value, 16)[0]
    logical_size = struct.unpack_from('<Q', value, 48)[0]
    return logical_size, _filetime_to_unix(mtime_raw)


def _filetime_to_unix(filetime: int) -> float:
    if filetime <= 0:
        return 0.0
    return (filetime - 116444736000000000) / 10_000_000.0


def _parse_file_record(
    record: bytes,
    record_index: int,
    geometry: VolumeGeometry,
) -> Optional[MftEntry]:
    if len(record) < 0x40 or record[:4] != b'FILE':
        return None

    flags = struct.unpack_from('<H', record, 0x16)[0]
    if not (flags & 0x01):
        return None

    is_dir = bool(flags & 0x02)
    first_attr = struct.unpack_from('<H', record, 0x14)[0]
    if first_attr <= 0 or first_attr >= len(record):
        return None

    parent_index = 0
    name = ''
    name_type = -1
    size = 0
    mtime = 0.0

    for attr_type, attr in _iter_attributes(record, first_attr):
        if attr_type == ATTR_FILE_NAME:
            parsed = _parse_file_name_attribute(attr)
            if not parsed:
                continue
            parent_frn, candidate_name, candidate_size, candidate_mtime, candidate_type = parsed
            if candidate_type in (FILE_NAME_WIN32, FILE_NAME_POSIX) and candidate_type >= name_type:
                parent_index = mft_index(parent_frn)
                name = candidate_name
                name_type = candidate_type
                if not is_dir:
                    size = candidate_size
                mtime = candidate_mtime
        elif attr_type == ATTR_STANDARD_INFORMATION and not is_dir and size == 0:
            parsed_si = _parse_standard_information(attr)
            if parsed_si:
                size, si_mtime = parsed_si
                if si_mtime:
                    mtime = si_mtime

    if not name or name in ('.', '..'):
        return None

    sequence = struct.unpack_from('<H', record, 0x10)[0]
    frn = (sequence << 48) | record_index

    return MftEntry(
        index=record_index,
        parent_index=parent_index,
        name=name,
        is_dir=is_dir,
        size=size,
        mtime=mtime,
        frn=frn,
    )


def _read_mft_entries(
    handle,
    geometry: VolumeGeometry,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> Dict[int, MftEntry]:
    record_size = geometry.bytes_per_file_record
    mft_byte_offset = geometry.mft_start_lcn * geometry.bytes_per_cluster
    win32file.SetFilePointer(handle, mft_byte_offset, win32con.FILE_BEGIN)

    entries: Dict[int, MftEntry] = {}
    record_index = 0
    last_progress = 0.0
    chunk_bytes = record_size * MFT_READ_CHUNK_RECORDS

    while True:
        try:
            _, data = win32file.ReadFile(handle, chunk_bytes)
        except Exception:
            break
        if not data:
            break

        for offset in range(0, len(data), record_size):
            raw = bytearray(data[offset: offset + record_size])
            if len(raw) < record_size:
                break
            _apply_fixup(raw, geometry)
            entry = _parse_file_record(bytes(raw), record_index, geometry)
            if entry:
                entries[record_index] = entry
            record_index += 1

            if progress_callback and time.time() - last_progress >= PROGRESS_INTERVAL:
                progress_callback(f'MFT record {record_index:,}', record_index)
                last_progress = time.time()

        if len(data) < chunk_bytes:
            break

    return entries


def _resolve_paths(
    entries: Dict[int, MftEntry],
    root_index: int,
    drive_root: str,
) -> Dict[int, str]:
    children_by_parent: Dict[int, List[int]] = defaultdict(list)
    for idx, entry in entries.items():
        children_by_parent[entry.parent_index].append(idx)

    paths: Dict[int, str] = {root_index: drive_root}
    queue: deque = deque([root_index])
    while queue:
        parent_idx = queue.popleft()
        parent_path = paths[parent_idx]
        for child_idx in children_by_parent.get(parent_idx, []):
            if child_idx in paths:
                continue
            child = entries.get(child_idx)
            if not child:
                continue
            paths[child_idx] = os.path.join(parent_path, child.name)
            queue.append(child_idx)

    return paths


def _aggregate_directory_sizes(entries: Dict[int, MftEntry], paths: Dict[int, str]) -> None:
    children_by_parent: Dict[int, List[int]] = defaultdict(list)
    for idx, entry in entries.items():
        if idx in paths:
            children_by_parent[entry.parent_index].append(idx)

    visiting: Set[int] = set()
    visited: Set[int] = set()

    def compute(idx: int) -> int:
        if idx in visited:
            return entries[idx].size
        if idx in visiting:
            return entries[idx].size
        visiting.add(idx)
        entry = entries[idx]
        if not entry.is_dir:
            visiting.remove(idx)
            visited.add(idx)
            return entry.size
        total = entry.size
        for child_idx in children_by_parent.get(idx, []):
            if child_idx in paths:
                total += compute(child_idx)
        entry.size = total
        visiting.remove(idx)
        visited.add(idx)
        return total

    for idx in list(paths.keys()):
        if entries[idx].is_dir:
            compute(idx)


def _should_prune_children(path: str) -> bool:
    parts = path.split('\\')
    return any(part in SKIP_DIRS_SCAN for part in parts)


def _build_file_node(
    idx: int,
    entries: Dict[int, MftEntry],
    paths: Dict[int, str],
    children_by_parent: Dict[int, List[int]],
) -> Optional[object]:
    from disk_analyzer_fixed import FileNode

    if idx not in paths:
        return None
    entry = entries[idx]
    path = paths[idx]
    node = FileNode(
        path=path,
        name=entry.name,
        size=entry.size,
        is_dir=entry.is_dir,
        mtime=entry.mtime,
        frn=entry.frn,
    )
    node._calculated_size = entry.size

    if entry.is_dir and _should_prune_children(path):
        return node

    if not entry.is_dir:
        node.file_count = 1
        return node

    for child_idx in children_by_parent.get(idx, []):
        if child_idx not in paths:
            continue
        child = entries[child_idx]
        child_path = paths[child_idx]
        if _should_prune_children(child_path) and child.is_dir:
            prune_node = FileNode(
                path=child_path,
                name=child.name,
                size=child.size,
                is_dir=True,
                mtime=child.mtime,
                frn=child.frn,
                parent=node,
            )
            prune_node._calculated_size = child.size
            node.children.append(prune_node)
            node.dir_count += 1
            continue
        child_node = _build_file_node(child_idx, entries, paths, children_by_parent)
        if child_node:
            child_node.parent = node
            node.children.append(child_node)
            if child_node.is_dir:
                node.dir_count += 1
            else:
                node.file_count += 1

    node.children.sort(key=lambda child: child.name.lower())
    return node


def scan_drive_mft(
    drive_path: str,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> Optional[object]:
    """
    Scan an NTFS volume via sequential MFT read. Returns a FileNode tree rooted at drive_path.
  """
    if not HAS_WIN32:
        logging.info('MFT scan unavailable: pywin32 not installed')
        return None

    drive_path = os.path.abspath(drive_path)
    if len(drive_path) >= 2 and drive_path[1] == ':':
        drive_root = drive_path[:2] + '\\'
    else:
        drive_root = drive_path

    if not is_ntfs_volume(drive_root):
        logging.info(f'MFT scan skipped: {drive_root} is not NTFS')
        return None

    start = time.time()
    handle = None
    try:
        handle = _volume_handle(drive_root)
        geometry = _get_volume_geometry(handle)
        if not geometry:
            logging.warning('MFT scan failed: could not read NTFS volume geometry')
            return None

        if progress_callback:
            progress_callback(f'Reading MFT on {drive_root}', 0)

        entries = _read_mft_entries(handle, geometry, progress_callback)
        if not entries:
            logging.warning('MFT scan returned no entries')
            return None

        root_index = 5
        if root_index not in entries:
            logging.warning('MFT scan: root directory record (index 5) not found')
            return None

        paths = _resolve_paths(entries, root_index, drive_root)
        _aggregate_directory_sizes(entries, paths)

        children_by_parent: Dict[int, List[int]] = defaultdict(list)
        for idx in paths:
            entry = entries[idx]
            if entry.parent_index in paths or entry.parent_index == root_index:
                children_by_parent[entry.parent_index].append(idx)

        root_node = _build_file_node(root_index, entries, paths, children_by_parent)
        if not root_node:
            return None

        elapsed = time.time() - start
        logging.info(
            f'MFT scan of {drive_root} completed in {elapsed:.2f}s '
            f'({len(entries):,} records, {len(paths):,} paths)'
        )
        return root_node

    except Exception as exc:
        logging.error(f'MFT scan failed: {exc}', exc_info=True)
        return None
    finally:
        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass


def can_use_mft_scan(path: str) -> bool:
    """True when elevated on an NTFS volume and pywin32 is available."""
    if not HAS_WIN32:
        return False
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return False
    except Exception:
        return False
    drive = os.path.abspath(path)
    if len(drive) >= 2 and drive[1] == ':':
        drive = drive[:2] + '\\'
    return is_ntfs_volume(drive)
