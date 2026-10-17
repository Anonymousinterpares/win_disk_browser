"""
NTFS MFT fast scan — reads the Master File Table in logical record order.

Requires administrator privileges on NTFS volumes. Falls back to directory walking
when unavailable. $MFT is often several physical runs; record numbers are positions
in that logical file, so reading only the first run drops later folders.
"""

from __future__ import annotations

import ctypes
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

FILE_NAME_POSIX = 0x00
FILE_NAME_WIN32 = 0x01
FILE_NAME_DOS = 0x02
FILE_NAME_WIN32_DOS = 0x03
ATTR_DATA = 0x80
ATTR_ATTRIBUTE_LIST = 0x20

# Win32 names win over a POSIX alias. A name that fits in 8.3 is stored once
# as Win32+DOS (3), not as plain Win32 (1). DOS-only is the last resort.
_NAMESPACE_RANK = {
    FILE_NAME_DOS: 1,
    FILE_NAME_POSIX: 2,
    FILE_NAME_WIN32: 3,
    FILE_NAME_WIN32_DOS: 3,
}

PROGRESS_INTERVAL = 0.1
MFT_READ_CHUNK_RECORDS = 4096


@dataclass
class VolumeGeometry:
    bytes_per_sector: int
    bytes_per_cluster: int
    bytes_per_file_record: int
    mft_start_lcn: int
    mft_valid_data_length: int


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
        mft_valid_data_length = struct.unpack_from('<Q', data, 56)[0]
        mft_start_lcn = struct.unpack_from('<Q', data, 64)[0]
        if bytes_per_file_record <= 0 or bytes_per_cluster <= 0 or bytes_per_sector <= 0:
            return None
        return VolumeGeometry(
            bytes_per_sector=bytes_per_sector,
            bytes_per_cluster=bytes_per_cluster,
            bytes_per_file_record=bytes_per_file_record,
            mft_start_lcn=mft_start_lcn,
            mft_valid_data_length=mft_valid_data_length,
        )
    except Exception as exc:
        logging.debug(f'FSCTL_GET_NTFS_VOLUME_DATA failed: {exc}')
        return None


def _apply_fixup(record: bytearray, geometry: VolumeGeometry) -> bool:
    """Restore the bytes NTFS replaced with the update sequence. False if unusable."""
    if len(record) < 42 or record[:4] != b'FILE':
        return False
    usa_offset, usa_count = struct.unpack_from('<HH', record, 4)
    sector_size = geometry.bytes_per_sector
    if sector_size < 2 or len(record) % sector_size != 0:
        return False
    expected = len(record) // sector_size + 1
    if usa_count != expected or usa_offset < 42:
        return False
    if usa_offset + usa_count * 2 > len(record):
        return False
    update_seq = struct.unpack_from('<H', record, usa_offset)[0]
    for sector_idx in range(1, usa_count):
        patch_offset = sector_idx * sector_size - 2
        if struct.unpack_from('<H', record, patch_offset)[0] != update_seq:
            return False
        struct.pack_into(
            '<H',
            record,
            patch_offset,
            struct.unpack_from('<H', record, usa_offset + sector_idx * 2)[0],
        )
    return True


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
    name_rank = 0
    size = 0
    mtime = 0.0

    for attr_type, attr in _iter_attributes(record, first_attr):
        if attr_type == ATTR_FILE_NAME:
            parsed = _parse_file_name_attribute(attr)
            if not parsed:
                continue
            parent_frn, candidate_name, candidate_size, candidate_mtime, candidate_type = parsed
            rank = _NAMESPACE_RANK.get(candidate_type, 0)
            if rank >= name_rank and rank > 0:
                parent_index = mft_index(parent_frn)
                name = candidate_name
                name_rank = rank
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


def _kernel32():
    kernel = getattr(_kernel32, '_dll', None)
    if kernel is not None:
        return kernel
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetFilePointerEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        ctypes.c_uint32,
    ]
    kernel.SetFilePointerEx.restype = ctypes.c_int
    kernel.GetFileSizeEx.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    kernel.GetFileSizeEx.restype = ctypes.c_int
    _kernel32._dll = kernel
    return kernel


def _file_size(handle) -> int:
    size = ctypes.c_ulonglong(0)
    if not _kernel32().GetFileSizeEx(int(handle), ctypes.byref(size)):
        return 0
    return int(size.value)


def _seek(handle, offset: int) -> None:
    new_pos = ctypes.c_longlong()
    ok = _kernel32().SetFilePointerEx(
        int(handle),
        ctypes.c_longlong(offset),
        ctypes.byref(new_pos),
        win32con.FILE_BEGIN,
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), 'SetFilePointerEx failed')


def _parse_data_runs(run_bytes: bytes) -> List[Tuple[int, int]]:
    """Decode a non-resident run list into (lcn, cluster_count). Sparse lcn is -1."""
    runs: List[Tuple[int, int]] = []
    pos = 0
    lcn = 0
    size = len(run_bytes)
    while pos < size:
        header = run_bytes[pos]
        pos += 1
        if header == 0:
            break
        len_size = header & 0x0F
        off_size = (header >> 4) & 0x0F
        if len_size == 0 or pos + len_size + off_size > size:
            break
        count = int.from_bytes(run_bytes[pos:pos + len_size], 'little')
        pos += len_size
        if count <= 0:
            break
        if off_size == 0:
            runs.append((-1, count))
            continue
        delta = int.from_bytes(run_bytes[pos:pos + off_size], 'little')
        pos += off_size
        sign = 1 << (off_size * 8 - 1)
        if delta & sign:
            delta -= 1 << (off_size * 8)
        lcn += delta
        if lcn < 0:
            break
        runs.append((lcn, count))
    return runs


def _unnamed_data_runs(record: bytes) -> Optional[bytes]:
    """Raw run list of the unnamed non-resident $DATA attribute, if this record has one."""
    if len(record) < 0x40 or record[:4] != b'FILE':
        return None
    first_attr = struct.unpack_from('<H', record, 0x14)[0]
    for attr_type, attr in _iter_attributes(record, first_attr):
        if attr_type != ATTR_DATA or len(attr) < 0x40:
            continue
        if attr[8] != 1 or attr[9] != 0:
            continue
        runs_off = struct.unpack_from('<H', attr, 0x20)[0]
        if runs_off >= len(attr):
            continue
        return attr[runs_off:]
    return None


def _attribute_list_targets(record: bytes) -> List[Tuple[int, int]]:
    """(starting_vcn, mft_record) for unnamed $DATA pieces stored outside this record."""
    if len(record) < 0x40 or record[:4] != b'FILE':
        return []
    first_attr = struct.unpack_from('<H', record, 0x14)[0]
    targets: List[Tuple[int, int]] = []
    for attr_type, attr in _iter_attributes(record, first_attr):
        if attr_type != ATTR_ATTRIBUTE_LIST or attr[8] != 0:
            continue
        value_length = struct.unpack_from('<I', attr, 0x10)[0]
        value_offset = struct.unpack_from('<H', attr, 0x14)[0]
        if value_offset + value_length > len(attr):
            continue
        value = attr[value_offset:value_offset + value_length]
        pos = 0
        while pos + 26 <= len(value):
            entry_type, entry_len = struct.unpack_from('<IH', value, pos)
            if entry_len < 26 or pos + entry_len > len(value):
                break
            name_len = value[pos + 6]
            starting_vcn = struct.unpack_from('<Q', value, pos + 8)[0]
            file_ref = struct.unpack_from('<Q', value, pos + 16)[0]
            if entry_type == ATTR_DATA and name_len == 0 and starting_vcn > 0:
                targets.append((starting_vcn, mft_index(file_ref)))
            pos += entry_len
    targets.sort()
    return targets


def _record_disk_offset(
    runs: List[Tuple[int, int]],
    record_index: int,
    geometry: VolumeGeometry,
) -> Optional[int]:
    record_size = geometry.bytes_per_file_record
    cluster_size = geometry.bytes_per_cluster
    if record_size <= 0 or cluster_size < record_size:
        return None
    records_per_cluster = cluster_size // record_size
    remaining = record_index
    for lcn, clusters in runs:
        records_in_run = clusters * records_per_cluster
        if remaining < records_in_run:
            if lcn < 0:
                return None
            cluster_index = remaining // records_per_cluster
            within = remaining % records_per_cluster
            return (lcn + cluster_index) * cluster_size + within * record_size
        remaining -= records_in_run
    return None


def _note_progress(
    progress_callback: Optional[Callable[[str, int], None]],
    record_index: int,
    last_progress: List[float],
) -> None:
    if progress_callback is None:
        return
    now = time.time()
    if now - last_progress[0] < PROGRESS_INTERVAL:
        return
    progress_callback(f'MFT record {record_index:,}', record_index)
    last_progress[0] = now


def _ingest_buffer(
    data: bytes,
    geometry: VolumeGeometry,
    record_index: int,
    entries: Dict[int, MftEntry],
    progress_callback: Optional[Callable[[str, int], None]],
    last_progress: List[float],
) -> int:
    record_size = geometry.bytes_per_file_record
    usable = len(data) - (len(data) % record_size)
    for offset in range(0, usable, record_size):
        raw = bytearray(data[offset:offset + record_size])
        try:
            if _apply_fixup(raw, geometry):
                entry = _parse_file_record(bytes(raw), record_index, geometry)
                if entry:
                    entries[record_index] = entry
        except Exception:
            pass
        record_index += 1
        _note_progress(progress_callback, record_index, last_progress)
    return record_index


def _ingest_handle(
    handle,
    byte_length: int,
    geometry: VolumeGeometry,
    record_index: int,
    entries: Dict[int, MftEntry],
    progress_callback: Optional[Callable[[str, int], None]],
    last_progress: List[float],
) -> int:
    record_size = geometry.bytes_per_file_record
    chunk_bytes = record_size * MFT_READ_CHUNK_RECORDS
    remaining = byte_length - (byte_length % record_size)
    while remaining >= record_size:
        to_read = min(chunk_bytes, remaining)
        _, data = win32file.ReadFile(handle, to_read)
        if not data:
            break
        usable = len(data) - (len(data) % record_size)
        if usable <= 0:
            break
        record_index = _ingest_buffer(
            data[:usable], geometry, record_index, entries, progress_callback, last_progress,
        )
        remaining -= usable
        if len(data) < to_read:
            break
    return record_index


def _open_mft_file(drive_root: str):
    letter = drive_root[0]
    return win32file.CreateFile(
        f'\\\\.\\{letter}:\\$MFT',
        win32file.GENERIC_READ,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
        None,
        win32file.OPEN_EXISTING,
        win32con.FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )


def _read_logical_mft_file(
    drive_root: str,
    geometry: VolumeGeometry,
    entries: Dict[int, MftEntry],
    progress_callback: Optional[Callable[[str, int], None]],
) -> bool:
    """Read \\.\\X:\\$MFT. Windows returns records in order across every fragment."""
    handle = None
    try:
        handle = _open_mft_file(drive_root)
        byte_length = _file_size(handle)
        valid = geometry.mft_valid_data_length
        if 0 < valid < byte_length or byte_length <= 0:
            byte_length = valid
        record_size = geometry.bytes_per_file_record
        if byte_length < record_size:
            return False
        expected = byte_length // record_size
        logging.info('Reading %s $MFT file (%s records)', drive_root, f'{expected:,}')
        reached = _ingest_handle(
            handle, byte_length, geometry, 0, entries, progress_callback, [0.0],
        )
        if reached < expected:
            logging.info(
                '$MFT file read stopped at record %s of %s',
                f'{reached:,}',
                f'{expected:,}',
            )
            entries.clear()
            return False
        return bool(entries)
    except Exception as exc:
        logging.info('$MFT file open failed for %s (%s)', drive_root, exc)
        entries.clear()
        return False
    finally:
        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass


def _read_record_at(handle, offset: int, geometry: VolumeGeometry) -> Optional[bytes]:
    _seek(handle, offset)
    _, data = win32file.ReadFile(handle, geometry.bytes_per_file_record)
    if len(data) < geometry.bytes_per_file_record:
        return None
    raw = bytearray(data[:geometry.bytes_per_file_record])
    if not _apply_fixup(raw, geometry):
        return None
    return bytes(raw)


def _extend_runs_from_attribute_list(
    handle,
    geometry: VolumeGeometry,
    base_record: bytes,
    runs: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Follow a resident attribute list when the $MFT run list does not fit in record 0."""
    merged = list(runs)
    seen = {0}
    for _starting_vcn, record_index in _attribute_list_targets(base_record):
        if record_index in seen:
            continue
        seen.add(record_index)
        offset = _record_disk_offset(merged, record_index, geometry)
        if offset is None:
            logging.info('MFT attribute-list record %s is outside the known runs', record_index)
            continue
        try:
            extension = _read_record_at(handle, offset, geometry)
        except Exception:
            logging.info('Could not read MFT attribute-list record %s', record_index, exc_info=True)
            continue
        if not extension:
            continue
        extra = _unnamed_data_runs(extension)
        if not extra:
            continue
        merged.extend(_parse_data_runs(extra))
    return merged


def _read_mft_runs(
    handle,
    geometry: VolumeGeometry,
    entries: Dict[int, MftEntry],
    progress_callback: Optional[Callable[[str, int], None]],
) -> None:
    record_size = geometry.bytes_per_file_record
    cluster_size = geometry.bytes_per_cluster
    base = _read_record_at(handle, geometry.mft_start_lcn * cluster_size, geometry)
    if not base:
        logging.warning('MFT record 0 could not be read')
        return
    run_blob = _unnamed_data_runs(base)
    if not run_blob:
        logging.warning('MFT record 0 has no $DATA run list')
        return
    runs = _extend_runs_from_attribute_list(
        handle, geometry, base, _parse_data_runs(run_blob),
    )
    if not runs:
        logging.warning('MFT $DATA run list was empty')
        return

    total_clusters = sum(count for _lcn, count in runs)
    logical_bytes = total_clusters * cluster_size
    valid = geometry.mft_valid_data_length
    if valid > 0:
        logical_bytes = min(logical_bytes, valid)
    logging.info(
        'Reading MFT by data runs: %s extents, %s records',
        len(runs),
        logical_bytes // record_size,
    )

    remaining = logical_bytes - (logical_bytes % record_size)
    record_index = 0
    last_progress = [0.0]
    for lcn, clusters in runs:
        if remaining < record_size:
            break
        run_bytes = min(clusters * cluster_size, remaining)
        run_bytes -= run_bytes % record_size
        planned = run_bytes // record_size
        if lcn < 0:
            record_index += planned
        else:
            _seek(handle, lcn * cluster_size)
            start_index = record_index
            record_index = _ingest_handle(
                handle,
                run_bytes,
                geometry,
                record_index,
                entries,
                progress_callback,
                last_progress,
            )
            if record_index < start_index + planned:
                logging.error(
                    'MFT run at LCN %s ended early at record %s',
                    lcn,
                    f'{record_index:,}',
                )
                entries.clear()
                return
        remaining -= run_bytes
    expected = logical_bytes // record_size
    if record_index < expected:
        logging.error(
            'MFT runs covered %s of %s records',
            f'{record_index:,}',
            f'{expected:,}',
        )
        entries.clear()


def _read_mft_entries(
    handle,
    geometry: VolumeGeometry,
    drive_root: str,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> Dict[int, MftEntry]:
    entries: Dict[int, MftEntry] = {}
    if _read_logical_mft_file(drive_root, geometry, entries, progress_callback):
        return entries
    try:
        _read_mft_runs(handle, geometry, entries, progress_callback)
    except Exception:
        logging.exception('MFT data-run read failed')
        entries.clear()
    return entries


def _mft_resolution_failed(entries: Dict[int, MftEntry], paths: Dict[int, str]) -> bool:
    """True when almost every record was orphaned, which a linear volume read used to do."""
    if len(entries) < 500:
        return False
    return len(paths) * 4 < len(entries)


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
        node.skip_recurse = True
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
            prune_node.skip_recurse = True
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

        entries = _read_mft_entries(handle, geometry, drive_root, progress_callback)
        if not entries:
            logging.warning('MFT scan returned no entries')
            return None

        root_index = 5
        if root_index not in entries:
            # Record 5's only file name is ".", which the parser drops. Children
            # still point at it, so the drive root has to exist for the walk.
            logging.info('MFT root name is not searchable; using the drive root')
            entries[root_index] = MftEntry(
                index=root_index,
                parent_index=0,
                name=drive_root.rstrip('\\') or drive_root,
                is_dir=True,
                size=0,
            )

        paths = _resolve_paths(entries, root_index, drive_root)
        if _mft_resolution_failed(entries, paths):
            logging.error(
                'MFT scan of %s kept %s paths out of %s records; falling back',
                drive_root,
                f'{len(paths):,}',
                f'{len(entries):,}',
            )
            return None
        _aggregate_directory_sizes(entries, paths)

        search_index = None
        try:
            from name_index import NameIndex
            search_index = NameIndex.from_mft(
                entries,
                root_index,
                drive_root,
                paths.keys(),
            )
        except Exception:
            logging.exception('Name index build from MFT failed')

        children_by_parent: Dict[int, List[int]] = defaultdict(list)
        for idx in paths:
            entry = entries[idx]
            if entry.parent_index in paths or entry.parent_index == root_index:
                children_by_parent[entry.parent_index].append(idx)

        root_node = _build_file_node(root_index, entries, paths, children_by_parent)
        if not root_node:
            return None
        if search_index is not None:
            root_node.search_index = search_index

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
