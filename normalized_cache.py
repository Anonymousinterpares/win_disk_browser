"""
Normalized SQLite storage for scan trees (replaces monolithic pickle blobs).
"""

import logging
import pickle
import sqlite3
import time
from typing import List, Optional, Tuple, Any

CACHE_FORMAT_PICKLE = 1
CACHE_FORMAT_NORMALIZED = 2
# CACHE_FORMAT_MMAP = 3 lives in mmap_snapshot.py
# CACHE_FORMAT_MMAP = 3 lives in mmap_snapshot.py


def _configure_cache_connection(conn: sqlite3.Connection, *, write: bool = False) -> None:
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL' if write else 'PRAGMA synchronous=OFF')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute('PRAGMA mmap_size=268435456')
    conn.execute('PRAGMA cache_size=-64000')


def ensure_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS scan_cache (
            drive TEXT PRIMARY KEY,
            data BLOB,
            timestamp INTEGER,
            usn_journal_id INTEGER,
            usn_next INTEGER DEFAULT 0,
            cache_format INTEGER DEFAULT 1
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS cache_nodes (
            drive TEXT NOT NULL,
            path TEXT NOT NULL,
            parent_path TEXT,
            name TEXT NOT NULL,
            size INTEGER NOT NULL,
            is_dir INTEGER NOT NULL,
            mtime REAL DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            dir_count INTEGER DEFAULT 0,
            PRIMARY KEY (drive, path)
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_cache_nodes_parent ON cache_nodes(drive, parent_path)'
    )
    columns = {row[1] for row in conn.execute('PRAGMA table_info(scan_cache)')}
    if 'usn_next' not in columns:
        conn.execute('ALTER TABLE scan_cache ADD COLUMN usn_next INTEGER DEFAULT 0')
    if 'cache_format' not in columns:
        conn.execute('ALTER TABLE scan_cache ADD COLUMN cache_format INTEGER DEFAULT 1')
    if 'snapshot_path' not in columns:
        conn.execute('ALTER TABLE scan_cache ADD COLUMN snapshot_path TEXT')


def _drive_key(path: str) -> str:
    return path


def _flatten_tree(root: Any) -> List[Tuple]:
    rows: List[Tuple] = []
    drive = root.path

    def walk(node: Any, parent_path: Optional[str]) -> None:
        rows.append((
            drive,
            node.path,
            parent_path,
            node.name,
            node.size,
            1 if node.is_dir else 0,
            node.mtime,
            node.file_count,
            node.dir_count,
        ))
        if node.is_dir:
            for child in node.children:
                walk(child, node.path)

    walk(root, None)
    return rows


def save_normalized_tree(
    conn: sqlite3.Connection,
    root: Any,
    usn_journal_id: int = 0,
    usn_next: int = 0,
) -> None:
    drive = _drive_key(root.path)
    rows = _flatten_tree(root)
    timestamp = int(time.time())

    conn.execute('DELETE FROM cache_nodes WHERE drive = ?', (drive,))
    conn.executemany(
        '''
        INSERT INTO cache_nodes
            (drive, path, parent_path, name, size, is_dir, mtime, file_count, dir_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        rows,
    )
    conn.execute(
        '''
        INSERT OR REPLACE INTO scan_cache
            (drive, data, timestamp, usn_journal_id, usn_next, cache_format)
        VALUES (?, NULL, ?, ?, ?, ?)
        ''',
        (drive, timestamp, usn_journal_id, usn_next, CACHE_FORMAT_NORMALIZED),
    )


def load_normalized_tree(conn: sqlite3.Connection, drive: str) -> Optional[Any]:
    _configure_cache_connection(conn)
    cursor = conn.execute(
        '''
        SELECT path, parent_path, name, size, is_dir, mtime, file_count, dir_count
        FROM cache_nodes
        WHERE drive = ?
        ''',
        (drive,),
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    from disk_analyzer_fixed import FileNode

    nodes: dict = {}
    root: Optional[Any] = None

    for path, parent_path, name, size, is_dir, mtime, file_count, dir_count in rows:
        node = FileNode(
            path=path,
            name=name,
            size=size,
            is_dir=bool(is_dir),
            mtime=mtime or 0.0,
            file_count=file_count or 0,
            dir_count=dir_count or 0,
        )
        # Sizes were aggregated at save time — skip full-tree recomputation on load.
        node._calculated_size = size
        nodes[path] = node
        if parent_path is None or path == drive:
            root = node

    for path, parent_path, *_ in rows:
        if parent_path and parent_path in nodes:
            child = nodes[path]
            parent = nodes[parent_path]
            child.parent = parent
            parent.children.append(child)

    return root


def load_pickle_tree(conn: sqlite3.Connection, drive: str) -> Optional[Any]:
    cursor = conn.execute('SELECT data FROM scan_cache WHERE drive = ?', (drive,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    try:
        return pickle.loads(row[0])
    except Exception as exc:
        logging.error(f"Pickle cache load failed for {drive}: {exc}")
        return None


def get_cache_meta(conn: sqlite3.Connection, drive: str) -> Optional[Tuple[int, int, int]]:
    """Return (cache_format, usn_journal_id, usn_next) if present."""
    cursor = conn.execute(
        'SELECT cache_format, usn_journal_id, usn_next FROM scan_cache WHERE drive = ?',
        (drive,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return int(row[0] or CACHE_FORMAT_PICKLE), int(row[1] or 0), int(row[2] or 0)


def get_snapshot_path(conn: sqlite3.Connection, drive: str) -> Optional[str]:
    cursor = conn.execute(
        'SELECT snapshot_path FROM scan_cache WHERE drive = ?',
        (drive,),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def upsert_scan_cache_meta(
    conn: sqlite3.Connection,
    drive: str,
    cache_format: int,
    usn_journal_id: int,
    usn_next: int,
    snapshot_path: Optional[str] = None,
) -> None:
    timestamp = int(time.time())
    conn.execute(
        '''
        INSERT OR REPLACE INTO scan_cache
            (drive, data, timestamp, usn_journal_id, usn_next, cache_format, snapshot_path)
        VALUES (?, NULL, ?, ?, ?, ?, ?)
        ''',
        (drive, timestamp, usn_journal_id, usn_next, cache_format, snapshot_path),
    )


def has_normalized_cache(conn: sqlite3.Connection, drive: str) -> bool:
    cursor = conn.execute(
        'SELECT 1 FROM cache_nodes WHERE drive = ? LIMIT 1',
        (drive,),
    )
    return cursor.fetchone() is not None
