"""
Memory-mapped flat snapshot format for instant cache reopen.

Layout (little-endian):
  [Header 64 bytes]
  [Node records: node_count × 48 bytes]
  [Child index array: total_children × 4 bytes]
  [String table: UTF-8, null-terminated strings]
"""

from __future__ import annotations

import logging
import mmap
import os
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

SNAPSHOT_MAGIC = b'DISP'
SNAPSHOT_VERSION = 1
HEADER_SIZE = 64
NODE_SIZE = 48

HEADER_FMT = '<4s8I3Q4x'
# magic, version, node_count, root_index,
# nodes_offset, children_offset, strings_offset, strings_size, children_count,
# timestamp, usn_journal_id, usn_next

NODE_FMT = '<iIIIqqII8x'
# parent_id, first_child, child_count, name_offset, path_offset, size, mtime, flags

FLAG_IS_DIR = 1
CACHE_FORMAT_MMAP = 3


@dataclass
class SnapshotMeta:
    cache_format: int
    usn_journal_id: int
    usn_next: int
    snapshot_path: Optional[str] = None


def snapshot_file_path(db_path: str, drive: str) -> str:
    db_dir = os.path.dirname(os.path.abspath(db_path))
    snapshots_dir = os.path.join(db_dir, 'snapshots')
    os.makedirs(snapshots_dir, exist_ok=True)
    key = drive.rstrip('\\').replace(':', '_').replace('\\', '_') or 'drive'
    return os.path.join(snapshots_dir, f'{key}.mmap')


def _intern_string(value: str, table: bytearray, offsets: dict) -> int:
    if value in offsets:
        return offsets[value]
    offset = len(table)
    offsets[value] = offset
    table.extend(value.encode('utf-8'))
    table.append(0)
    return offset


def _read_cstring(mm: mmap.mmap, base_offset: int, rel_offset: int) -> str:
    start = base_offset + rel_offset
    end = mm.find(b'\0', start)
    if end < 0:
        end = len(mm)
    return mm[start:end].decode('utf-8', errors='replace')


def _flatten_tree(root: Any) -> Tuple[List[Any], List[int], List[int], List[int], List[int], int]:
    """Assign node indices (BFS) and build child index spans."""
    nodes: List[Any] = []
    parents: List[int] = []
    queue: deque = deque([(root, -1)])
    index_by_node = {}

    while queue:
        node, parent_idx = queue.popleft()
        idx = len(nodes)
        index_by_node[id(node)] = idx
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


def save_mmap_snapshot(
    root: Any,
    snapshot_path: str,
    usn_journal_id: int = 0,
    usn_next: int = 0,
) -> None:
    nodes, parents, first_child, child_counts, children_flat, root_index = _flatten_tree(root)
    node_count = len(nodes)

    string_table = bytearray()
    string_offsets: dict = {}

    tmp_path = f'{snapshot_path}.tmp'
    nodes_offset = HEADER_SIZE
    children_offset = nodes_offset + node_count * NODE_SIZE
    children_count = len(children_flat)
    strings_offset = children_offset + children_count * 4

    with open(tmp_path, 'wb') as handle:
        handle.write(b'\0' * HEADER_SIZE)

        for idx, node in enumerate(nodes):
            name_offset = _intern_string(node.name, string_table, string_offsets)
            path_offset = _intern_string(node.path, string_table, string_offsets)
            flags = FLAG_IS_DIR if node.is_dir else 0
            if node.is_dir and node._calculated_size is not None:
                stored_size = node._calculated_size
            else:
                stored_size = int(node.size)
            mtime_int = int(node.mtime or 0.0)
            record = struct.pack(
                NODE_FMT,
                parents[idx],
                first_child[idx],
                child_counts[idx],
                name_offset,
                path_offset,
                int(stored_size),
                mtime_int,
                flags,
            )
            handle.write(record)

        for child_idx in children_flat:
            handle.write(struct.pack('<I', child_idx))

        handle.write(string_table)
        strings_size = len(string_table)

        header = struct.pack(
            HEADER_FMT,
            SNAPSHOT_MAGIC,
            SNAPSHOT_VERSION,
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
        handle.seek(0)
        handle.write(header)

    os.replace(tmp_path, snapshot_path)
    logging.info(
        f'Saved mmap snapshot for {root.path}: {node_count:,} nodes -> {snapshot_path}'
    )


def _parse_header(mm: mmap.mmap) -> Optional[Tuple]:
    if len(mm) < HEADER_SIZE:
        return None
    magic, version, node_count, root_index, nodes_offset, children_offset, strings_offset, strings_size, children_count, timestamp, usn_journal_id, usn_next = struct.unpack_from(
        HEADER_FMT, mm, 0
    )
    if magic != SNAPSHOT_MAGIC or version != SNAPSHOT_VERSION:
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


def load_mmap_snapshot(snapshot_path: str) -> Optional[Any]:
    if not os.path.exists(snapshot_path):
        return None

    start = time.time()
    try:
        with open(snapshot_path, 'rb') as handle:
            mm = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                parsed = _parse_header(mm)
                if not parsed:
                    logging.warning(f'Invalid mmap snapshot header: {snapshot_path}')
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
                    offset = nodes_offset + idx * NODE_SIZE
                    parent_id, _first_child, _child_count, name_off, path_off, size, mtime_int, flags = struct.unpack_from(
                        NODE_FMT, mm, offset
                    )
                    mtime = float(mtime_int)
                    name = _read_cstring(mm, strings_offset, name_off)
                    path = _read_cstring(mm, strings_offset, path_off)
                    node = FileNode(
                        path=path,
                        name=name,
                        size=int(size),
                        is_dir=bool(flags & FLAG_IS_DIR),
                        mtime=float(mtime),
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

                root = nodes[root_index]
                if root is None:
                    return None

                elapsed = time.time() - start
                logging.info(
                    f'Loaded mmap snapshot in {elapsed:.2f}s '
                    f'({node_count:,} nodes from {snapshot_path})'
                )
                return root
            finally:
                mm.close()
    except Exception as exc:
        logging.error(f'mmap snapshot load failed for {snapshot_path}: {exc}', exc_info=True)
        return None


def has_mmap_snapshot(snapshot_path: str) -> bool:
    if not os.path.isfile(snapshot_path):
        return False
    try:
        with open(snapshot_path, 'rb') as handle:
            header = handle.read(8)
        magic, version = struct.unpack('<4sI', header)
        return magic == SNAPSHOT_MAGIC and version == SNAPSHOT_VERSION
    except Exception:
        return False
