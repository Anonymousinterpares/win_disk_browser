"""
Flat snapshot cache — compact on disk, fast to reopen.

Format v2 (.snap): zlib-compressed payload, names only (paths reconstructed on load).
Format v1 (.mmap): legacy uncompressed snapshot with full paths (auto-migrated on load).
"""

from __future__ import annotations

import io
import logging
import mmap
import os
import struct
import time
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

SNAPSHOT_MAGIC = b'DISP'
SNAPSHOT_VERSION_V1 = 1
SNAPSHOT_VERSION_V2 = 2
HEADER_SIZE = 64
NODE_SIZE_V1 = 48
NODE_SIZE_V2 = 40

HEADER_FMT_V1 = '<4s8I3Q4x'
NODE_FMT_V1 = '<iIIIqqII8x'

HEADER_FMT_V2 = '<4s8I3Q4x'
NODE_FMT_V2 = '<iIIIqqI4x'

FLAG_IS_DIR = 1
CACHE_FORMAT_MMAP = 3
COMPRESS_LEVEL = 6


@dataclass
class SnapshotMeta:
    cache_format: int
    usn_journal_id: int
    usn_next: int
    snapshot_path: Optional[str] = None


def snapshot_file_path(db_path: str, drive: str) -> str:
    """Primary snapshot path — compressed v2."""
    db_dir = os.path.dirname(os.path.abspath(db_path))
    snapshots_dir = os.path.join(db_dir, 'snapshots')
    os.makedirs(snapshots_dir, exist_ok=True)
    key = drive.rstrip('\\').replace(':', '_').replace('\\', '_') or 'drive'
    return os.path.join(snapshots_dir, f'{key}.snap')


def legacy_mmap_path(db_path: str, drive: str) -> str:
    """Legacy uncompressed snapshot path."""
    return snapshot_file_path(db_path, drive).replace('.snap', '.mmap')


def _intern_string(value: str, table: bytearray, offsets: dict) -> int:
    if value in offsets:
        return offsets[value]
    offset = len(table)
    offsets[value] = offset
    table.extend(value.encode('utf-8'))
    table.append(0)
    return offset


def _read_cstring(data: memoryview | mmap.mmap, base_offset: int, rel_offset: int) -> str:
    start = base_offset + rel_offset
    end = start
    limit = len(data)
    while end < limit and data[end] != 0:
        end += 1
    return bytes(data[start:end]).decode('utf-8', errors='replace')


def _flatten_tree(root: Any) -> Tuple[List[Any], List[int], List[int], List[int], List[int], int]:
    nodes: List[Any] = []
    parents: List[int] = []
    queue: deque = deque([(root, -1)])

    while queue:
        node, parent_idx = queue.popleft()
        idx = len(nodes)
        nodes.append(node)
        parents.append(parent_idx)
        for child in node.children:
            queue.append((child, idx))

    children_flat: List[int] = []
    first_child: List[int] = []
    child_counts: List[int] = []
    children_by_parent: dict = defaultdict(list)
    for idx, parent_idx in enumerate(parents):
        if parent_idx >= 0:
            children_by_parent[parent_idx].append(idx)

    for idx in range(len(nodes)):
        kids = children_by_parent.get(idx, [])
        first_child.append(len(children_flat))
        child_counts.append(len(kids))
        children_flat.extend(kids)

    root_index = 0
    for idx, node in enumerate(nodes):
        if node.path == root.path:
            root_index = idx
            break

    return nodes, parents, first_child, child_counts, children_flat, root_index


def _node_size(node: Any) -> int:
    if node.is_dir and node._calculated_size is not None:
        return int(node._calculated_size)
    return int(node.size)


def _build_v2_payload(
    root: Any,
    usn_journal_id: int,
    usn_next: int,
) -> bytes:
    """Build uncompressed v2 binary (names only, no full paths in string table)."""
    nodes, parents, first_child, child_counts, children_flat, root_index = _flatten_tree(root)
    node_count = len(nodes)

    string_table = bytearray()
    string_offsets: dict = {}

    buf = io.BytesIO()
    buf.write(b'\0' * HEADER_SIZE)

    for idx, node in enumerate(nodes):
        name_offset = _intern_string(node.name, string_table, string_offsets)
        flags = FLAG_IS_DIR if node.is_dir else 0
        record = struct.pack(
            NODE_FMT_V2,
            parents[idx],
            first_child[idx],
            child_counts[idx],
            name_offset,
            _node_size(node),
            int(node.mtime or 0.0),
            flags,
        )
        buf.write(record)

    for child_idx in children_flat:
        buf.write(struct.pack('<I', child_idx))

    strings_offset = buf.tell()
    buf.write(string_table)
    strings_size = len(string_table)

    nodes_offset = HEADER_SIZE
    children_offset = nodes_offset + node_count * NODE_SIZE_V2
    children_count = len(children_flat)

    header = struct.pack(
        HEADER_FMT_V2,
        SNAPSHOT_MAGIC,
        SNAPSHOT_VERSION_V2,
        node_count,
        root_index,
        nodes_offset,
        children_offset,
        strings_offset,
        strings_size,
        children_count,
        int(time.time()),
        int(usn_journal_id),
        int(usn_next),
    )
    buf.seek(0)
    buf.write(header)
    return buf.getvalue()


def _parse_v2_header(raw: memoryview) -> Optional[Tuple]:
    if len(raw) < HEADER_SIZE:
        return None
    magic, version, node_count, root_index, nodes_offset, children_offset, strings_offset, strings_size, children_count, timestamp, usn_journal_id, usn_next = struct.unpack_from(
        HEADER_FMT_V2, raw, 0
    )
    if magic != SNAPSHOT_MAGIC or version != SNAPSHOT_VERSION_V2:
        return None
    if node_count == 0:
        return None
    return (
        node_count,
        root_index,
        nodes_offset,
        children_offset,
        strings_offset,
        strings_size,
        children_count,
        timestamp,
        usn_journal_id,
        usn_next,
    )


def _load_v2_payload(raw: memoryview) -> Optional[Any]:
    parsed = _parse_v2_header(raw)
    if not parsed:
        return None

    (
        node_count,
        root_index,
        nodes_offset,
        children_offset,
        strings_offset,
        strings_size,
        _children_count,
        _timestamp,
        _usn_journal_id,
        _usn_next,
    ) = parsed

    from disk_analyzer_fixed import FileNode

    nodes: List[Optional[Any]] = [None] * node_count
    parents: List[int] = [-1] * node_count

    for idx in range(node_count):
        offset = nodes_offset + idx * NODE_SIZE_V2
        parent_id, _first_child, _child_count, name_off, size, mtime_int, flags = struct.unpack_from(
            NODE_FMT_V2, raw, offset
        )
        name = _read_cstring(raw, strings_offset, name_off)
        node = FileNode(
            path=name,
            name=name,
            size=int(size),
            is_dir=bool(flags & FLAG_IS_DIR),
            mtime=float(mtime_int),
        )
        node._calculated_size = int(size)
        nodes[idx] = node
        parents[idx] = parent_id

    for idx in range(node_count):
        parent_id = parents[idx]
        node = nodes[idx]
        if parent_id >= 0:
            parent = nodes[parent_id]
            node.parent = parent
            parent.children.append(node)
            node.path = os.path.normpath(os.path.join(parent.path, node.name))
        else:
            node.path = node.name

    root = nodes[root_index]
    return root


def _parse_v1_header(mm: mmap.mmap) -> Optional[Tuple]:
    if len(mm) < HEADER_SIZE:
        return None
    magic, version, node_count, root_index, nodes_offset, children_offset, strings_offset, strings_size, children_count, timestamp, usn_journal_id, usn_next = struct.unpack_from(
        HEADER_FMT_V1, mm, 0
    )
    if magic != SNAPSHOT_MAGIC or version != SNAPSHOT_VERSION_V1:
        return None
    if node_count == 0:
        return None
    return (
        node_count,
        root_index,
        nodes_offset,
        children_offset,
        strings_offset,
        strings_size,
        children_count,
        timestamp,
        usn_journal_id,
        usn_next,
    )


def _load_v1_mmap(mm: mmap.mmap) -> Optional[Any]:
    parsed = _parse_v1_header(mm)
    if not parsed:
        return None

    (
        node_count,
        root_index,
        nodes_offset,
        children_offset,
        strings_offset,
        strings_size,
        _children_count,
        _timestamp,
        _usn_journal_id,
        _usn_next,
    ) = parsed

    from disk_analyzer_fixed import FileNode

    nodes: List[Optional[Any]] = [None] * node_count
    parents: List[int] = [-1] * node_count

    for idx in range(node_count):
        offset = nodes_offset + idx * NODE_SIZE_V1
        parent_id, _first_child, _child_count, name_off, path_off, size, mtime_int, flags = struct.unpack_from(
            NODE_FMT_V1, mm, offset
        )
        name = _read_cstring(mm, strings_offset, name_off)
        path = _read_cstring(mm, strings_offset, path_off)
        node = FileNode(
            path=path,
            name=name,
            size=int(size),
            is_dir=bool(flags & FLAG_IS_DIR),
            mtime=float(mtime_int),
        )
        node._calculated_size = int(size)
        nodes[idx] = node
        parents[idx] = parent_id

    for idx in range(node_count):
        parent_id = parents[idx]
        node = nodes[idx]
        if parent_id >= 0:
            parent = nodes[parent_id]
            node.parent = parent
            parent.children.append(node)

    return nodes[root_index]


def save_mmap_snapshot(
    root: Any,
    snapshot_path: str,
    usn_journal_id: int = 0,
    usn_next: int = 0,
) -> None:
    """Save compressed v2 snapshot (.snap). Also removes legacy .mmap if present."""
    payload = _build_v2_payload(root, usn_journal_id, usn_next)
    compressed = zlib.compress(payload, level=COMPRESS_LEVEL)
    tmp_path = f'{snapshot_path}.tmp'

    with open(tmp_path, 'wb') as handle:
        handle.write(compressed)

    os.replace(tmp_path, snapshot_path)

    legacy_path = snapshot_path.replace('.snap', '.mmap')
    if legacy_path != snapshot_path and os.path.isfile(legacy_path):
        try:
            os.remove(legacy_path)
            logging.info(f'Removed legacy mmap snapshot: {legacy_path}')
        except OSError as exc:
            logging.warning(f'Could not remove legacy mmap snapshot: {exc}')

    ratio = (1 - len(compressed) / max(len(payload), 1)) * 100
    logging.info(
        f'Saved compressed snapshot for {root.path}: '
        f'{len(payload):,} → {len(compressed):,} bytes ({ratio:.1f}% reduction) '
        f'-> {snapshot_path}'
    )


def _load_compressed_snapshot(snapshot_path: str) -> Optional[Any]:
    start = time.time()
    with open(snapshot_path, 'rb') as handle:
        compressed = handle.read()

    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        logging.error(f'Compressed snapshot decompress failed: {exc}')
        return None

    view = memoryview(raw)
    parsed = _parse_v2_header(view)
    if not parsed:
        logging.warning(f'Unrecognized compressed snapshot payload: {snapshot_path}')
        return None

    root = _load_v2_payload(view)
    if root:
        elapsed = time.time() - start
        node_count = parsed[0]
        logging.info(
            f'Loaded compressed snapshot in {elapsed:.2f}s '
            f'({node_count:,} nodes, {len(compressed):,} bytes on disk)'
        )
    return root


def load_mmap_snapshot(snapshot_path: str) -> Optional[Any]:
    """Load .snap (v2 compressed) or legacy .mmap (v1)."""
    if not os.path.exists(snapshot_path):
        return None

    if snapshot_path.endswith('.snap'):
        return _load_compressed_snapshot(snapshot_path)

    start = time.time()
    try:
        with open(snapshot_path, 'rb') as handle:
            mm = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                root = _load_v1_mmap(mm)
                if root:
                    elapsed = time.time() - start
                    logging.info(
                        f'Loaded legacy mmap snapshot in {elapsed:.2f}s from {snapshot_path}'
                    )
                return root
            finally:
                mm.close()
    except Exception as exc:
        logging.error(f'Snapshot load failed for {snapshot_path}: {exc}', exc_info=True)
        return None


def has_mmap_snapshot(snapshot_path: str) -> bool:
    if not os.path.isfile(snapshot_path):
        return False
    try:
        with open(snapshot_path, 'rb') as handle:
            if snapshot_path.endswith('.snap'):
                sample = handle.read(64)
                if len(sample) < 2:
                    return False
                if sample[:2] in (b'\x78\x9c', b'\x78\x01', b'\x78\xda', b'\x78\x5e'):
                    return True
                try:
                    zlib.decompress(sample)
                    return True
                except zlib.error:
                    return False
            header = handle.read(8)
        magic, version = struct.unpack('<4sI', header)
        return magic == SNAPSHOT_MAGIC and version == SNAPSHOT_VERSION_V1
    except Exception:
        return False


def find_snapshot_for_drive(db_path: str, drive: str) -> Optional[str]:
    """Return existing snapshot path (.snap preferred, then .mmap)."""
    snap = snapshot_file_path(db_path, drive)
    if has_mmap_snapshot(snap):
        return snap
    legacy = legacy_mmap_path(db_path, drive)
    if has_mmap_snapshot(legacy):
        return legacy
    return None
