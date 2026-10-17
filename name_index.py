"""
Compact file/folder name index.

Names live in two byte blobs (original and casefolded). Files and folders have
separate sort orders, so a file-only or folder-only query never touches the
other set. Full paths are not stored. A hit rebuilds its path from parent ids.

Query plans:
  exact name          binary search
  prefix (report*)    binary search on the sorted order
  suffix (*.pdf)      binary search on the reversed-name order
  contains (*cache*)  one bytes.find scan of the casefolded blob
  other globs         prefix or suffix range, then a glob check on that slice

A leading star with no other literal still has to scan. The scan is over one
contiguous blob, which is much smaller than the folder tree and stays in C.
"""

from __future__ import annotations

import bisect
import heapq
import logging
import os
import struct
import threading
import time
import zlib
from array import array
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MAGIC = b'DIX1'
VERSION = 1
PARENT_NONE = 0xFFFFFFFF
MIN_LITERAL = 2
EXACT_FALLBACK_MIN = 3
COMPACT_THRESHOLD = 4000
RESULT_LIMIT = 100

RANK = {
    'exact': 0,
    'prefix': 1,
    'suffix': 2,
    'glob': 3,
    'contains': 4,
}

MSG_NEED_LITERAL = (
    'Type at least 2 characters. Use * where the name is unknown, '
    'for example report*, *.pdf, or *cache*.'
)

_HEADER = struct.Struct('<4s6I')


@dataclass(frozen=True)
class _Plan:
    mode: str
    needle: bytes
    parts: Tuple[bytes, ...]
    rank: int
    text: str


def index_path_for_snapshot(snapshot_path: str) -> str:
    base, _ext = os.path.splitext(snapshot_path)
    return base + '.idx'


def normalize_root_path(path: str) -> str:
    if not path:
        return path
    path = os.path.abspath(path)
    if len(path) == 2 and path[1] == ':':
        return path + '\\'
    if len(path) == 3 and path[1] == ':' and path[2] in '\\/':
        return path[0].upper() + ':\\'
    return path.rstrip('\\/') or path


def _norm_path(path: str) -> str:
    if not path:
        return ''
    norm = os.path.normcase(os.path.normpath(path))
    if len(norm) == 2 and norm[1] == ':':
        return norm + '\\'
    return norm


def _encode_name(name: str) -> Tuple[bytes, bytes]:
    orig = (name or '').encode('utf-8', errors='replace')
    folded = (name or '').casefold().encode('utf-8', errors='replace')
    if len(orig) > 65535:
        orig = orig[:65535]
    if len(folded) > 65535:
        folded = folded[:65535]
    return orig, folded


def _node_size(node) -> int:
    if getattr(node, 'is_dir', False):
        calculated = getattr(node, '_calculated_size', None)
        if isinstance(calculated, (int, float)):
            return max(0, int(calculated))
    try:
        return max(0, int(getattr(node, 'size', 0) or 0))
    except (TypeError, ValueError):
        return 0


def compile_pattern(query: str) -> Optional[_Plan]:
    text = (query or '').strip().casefold()
    if not text:
        return None
    while '**' in text:
        text = text.replace('**', '*')
    if set(text) <= {'*'}:
        return None
    parts = text.split('*')
    literals = [part for part in parts if part]
    if not literals or max(len(part) for part in literals) < MIN_LITERAL:
        return None
    encoded = tuple(part.encode('utf-8', errors='replace') for part in parts)
    if '*' not in text:
        needle = encoded[0]
        return _Plan('exact', needle, encoded, RANK['exact'], text)
    if text.endswith('*') and text.count('*') == 1:
        return _Plan('prefix', encoded[0], encoded, RANK['prefix'], text)
    if text.startswith('*') and text.count('*') == 1:
        return _Plan('suffix', encoded[-1], encoded, RANK['suffix'], text)
    if text.startswith('*') and text.endswith('*') and text.count('*') == 2:
        return _Plan('contains', encoded[1], encoded, RANK['contains'], text)
    longest = max(literals, key=len).encode('utf-8', errors='replace')
    return _Plan('glob', longest, encoded, RANK['glob'], text)


def _glob_match(name: bytes, parts: Sequence[bytes]) -> bool:
    if not parts:
        return False
    pos = 0
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if not part:
            continue
        if index == 0:
            if not name.startswith(part):
                return False
            pos = len(part)
        elif index == last:
            start = len(name) - len(part)
            if start < pos or not name.endswith(part):
                return False
        else:
            found = name.find(part, pos)
            if found < 0:
                return False
            pos = found + len(part)
    return True


class NameIndex:
    """In-memory name catalog for one scanned root."""

    def __init__(self) -> None:
        self.drive_root = ''
        self.partial = True
        self.count = 0
        self.file_count = 0
        self.dir_count = 0
        self.parent = array('I')
        self.orig_off = array('I')
        self.orig_len = array('H')
        self.fold_off = array('I')
        self.fold_len = array('H')
        self.size = array('Q')
        self.is_dir = bytearray()
        self.orig_blob = bytearray()
        self.fold_blob = bytearray()
        self.file_order = array('I')
        self.dir_order = array('I')
        self.file_rev = array('I')
        self.dir_rev = array('I')
        self.deleted: set = set()
        self.pending: List[int] = []
        self._lock = threading.RLock()

    @property
    def entry_count(self) -> int:
        return max(0, self.count - 1)

    @classmethod
    def from_tree(cls, root, partial: bool = True) -> 'NameIndex':
        records: List[Tuple[int, str, int, bool]] = []

        def walk(node, parent_index: int) -> None:
            name = node.name or os.path.basename(getattr(node, 'path', '') or '') or ''
            current = len(records)
            records.append((parent_index, name, _node_size(node), bool(node.is_dir)))
            for child in getattr(node, 'children', ()) or ():
                walk(child, current)

        walk(root, -1)
        drive_root = normalize_root_path(getattr(root, 'path', '') or '')
        return cls._from_records(records, drive_root, partial)

    @classmethod
    def from_mft(cls, entries: Dict[int, object], root_index: int, drive_root: str, reachable: Iterable[int]) -> 'NameIndex':
        """Build a complete index from MFT entries. Does not apply scan skip rules."""
        children: Dict[int, List[int]] = defaultdict(list)
        reachable_set = set(reachable)
        for record_index in reachable_set:
            if record_index == root_index:
                continue
            entry = entries.get(record_index)
            if entry is None:
                continue
            name = getattr(entry, 'name', '') or ''
            if not name or name in ('.', '..'):
                continue
            children[int(getattr(entry, 'parent_index', -1))].append(record_index)

        root_entry = entries.get(root_index)
        root_size = int(getattr(root_entry, 'size', 0) or 0) if root_entry is not None else 0
        normalized = normalize_root_path(drive_root)
        root_name = normalized.rstrip('\\') or normalized
        records: List[Tuple[int, str, int, bool]] = [(-1, root_name, max(0, root_size), True)]
        dense = {root_index: 0}
        queue: deque = deque([root_index])
        while queue:
            parent_mft = queue.popleft()
            parent_dense = dense[parent_mft]
            for child_mft in children.get(parent_mft, ()):
                if child_mft in dense:
                    continue
                entry = entries.get(child_mft)
                if entry is None:
                    continue
                name = getattr(entry, 'name', '') or ''
                if not name or name in ('.', '..'):
                    continue
                dense[child_mft] = len(records)
                records.append((
                    parent_dense,
                    name,
                    max(0, int(getattr(entry, 'size', 0) or 0)),
                    bool(getattr(entry, 'is_dir', False)),
                ))
                queue.append(child_mft)
        return cls._from_records(records, normalized, partial=False)

    @classmethod
    def _from_records(cls, records: Sequence[Tuple[int, str, int, bool]], drive_root: str, partial: bool) -> 'NameIndex':
        started = time.perf_counter()
        index = cls()
        index.drive_root = normalize_root_path(drive_root)
        index.partial = partial
        count = len(records)
        if count == 0:
            return index

        parent = array('I')
        orig_off = array('I')
        orig_len = array('H')
        fold_off = array('I')
        fold_len = array('H')
        sizes = array('Q')
        flags = bytearray()
        orig_blob = bytearray()
        fold_blob = bytearray()

        for parent_index, name, size, is_dir in records:
            orig, folded = _encode_name(name if parent_index >= 0 or name else (drive_root or ''))
            orig_off.append(len(orig_blob))
            orig_len.append(len(orig))
            orig_blob.extend(orig)
            orig_blob.append(0)
            fold_off.append(len(fold_blob))
            fold_len.append(len(folded))
            fold_blob.extend(folded)
            fold_blob.append(0)
            parent.append(PARENT_NONE if parent_index < 0 else int(parent_index))
            sizes.append(max(0, int(size)))
            flags.append(1 if is_dir else 0)

        file_ids = [i for i in range(1, count) if not flags[i]]
        dir_ids = [i for i in range(1, count) if flags[i]]
        folds: List[bytes] = [b''] * count
        for record_id in file_ids:
            folds[record_id] = bytes(fold_blob[fold_off[record_id]:fold_off[record_id] + fold_len[record_id]])
        for record_id in dir_ids:
            folds[record_id] = bytes(fold_blob[fold_off[record_id]:fold_off[record_id] + fold_len[record_id]])

        index.count = count
        index.file_count = len(file_ids)
        index.dir_count = len(dir_ids)
        index.parent = parent
        index.orig_off = orig_off
        index.orig_len = orig_len
        index.fold_off = fold_off
        index.fold_len = fold_len
        index.size = sizes
        index.is_dir = flags
        index.orig_blob = orig_blob
        index.fold_blob = fold_blob
        index.file_order = array('I', sorted(file_ids, key=folds.__getitem__))
        index.dir_order = array('I', sorted(dir_ids, key=folds.__getitem__))
        index.file_rev = array('I', sorted(file_ids, key=lambda i: folds[i][::-1]))
        index.dir_rev = array('I', sorted(dir_ids, key=lambda i: folds[i][::-1]))
        index.deleted = set()
        index.pending = []

        elapsed = time.perf_counter() - started
        message = (
            'Name index %s: %s entries (%s files, %s folders, partial=%s) in %.2fs'
        )
        args = (index.drive_root, count - 1, index.file_count, index.dir_count, partial, elapsed)
        if count > 100000 or elapsed >= 0.5:
            logging.info(message, *args)
        else:
            logging.debug(message, *args)
        return index

    def search(self, query: str, kind: str = 'both', limit: int = RESULT_LIMIT) -> dict:
        with self._lock:
            text = (query or '').strip()
            if not text:
                return self._empty(message='')
            plan = compile_pattern(text)
            if plan is None:
                return self._empty(message=MSG_NEED_LITERAL)
            if kind not in ('file', 'dir', 'both'):
                kind = 'both'
            limit = max(1, int(limit or RESULT_LIMIT))
            heap, total = self._collect(plan, kind, limit)
            expanded = False
            if total == 0 and plan.mode == 'exact' and len(plan.text) >= EXACT_FALLBACK_MIN:
                contains = _Plan(
                    'contains',
                    plan.needle,
                    (b'', plan.needle, b''),
                    RANK['contains'],
                    plan.text,
                )
                heap, total = self._collect(contains, kind, limit)
                expanded = total > 0
            return {
                'results': self._materialize(heap),
                'total': total,
                'partial': self.partial,
                'expanded': expanded,
                'message': '',
            }

    def save(self, path: str) -> None:
        with self._lock:
            if self.deleted or self.pending:
                self._compact()
            payload = zlib.compress(self._serialize(), 6)
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temporary = path + '.tmp'
            with open(temporary, 'wb') as handle:
                handle.write(payload)
            os.replace(temporary, path)
            logging.info(
                'Saved name index %s (%s entries, %s bytes, partial=%s)',
                path,
                self.entry_count,
                len(payload),
                self.partial,
            )

    @classmethod
    def load(cls, path: str) -> Optional['NameIndex']:
        try:
            with open(path, 'rb') as handle:
                compressed = handle.read()
            raw = zlib.decompress(compressed)
        except Exception as exc:
            logging.warning('Name index load failed for %s: %s', path, exc)
            return None
        try:
            return cls._deserialize(raw)
        except Exception as exc:
            logging.warning('Name index decode failed for %s: %s', path, exc)
            return None

    def note_deleted(self, path: str) -> None:
        with self._lock:
            record_id = self._find_id(path)
            if record_id is None or record_id == 0:
                return
            if self.is_dir[record_id]:
                self._mark_deleted_recursive(record_id, self._build_child_map())
            else:
                self.deleted.add(record_id)
            self._maybe_compact()

    def note_created(self, path: str, name: str, size: int, is_dir: bool) -> None:
        with self._lock:
            self._note_created(path, name, size, is_dir)
            self._maybe_compact()

    def note_size(self, path: str, size: int) -> None:
        with self._lock:
            record_id = self._find_id(path)
            if record_id is None or record_id in self.deleted:
                return
            self.size[record_id] = max(0, int(size or 0))

    def note_moved(self, src: str, dest: str, size: int, is_dir: bool) -> None:
        with self._lock:
            record_id = self._find_id(src)
            dest_parent = self._find_id(os.path.dirname(os.path.abspath(dest).rstrip('\\')))
            new_name = os.path.basename(os.path.abspath(dest).rstrip('\\'))
            if record_id is None or record_id == 0:
                if dest_parent is not None and new_name:
                    self._append(dest_parent, new_name, size, is_dir)
                    self._maybe_compact()
                return
            if dest_parent is None:
                if not bool(self.is_dir[record_id]):
                    self.deleted.add(record_id)
                    self._maybe_compact()
                return
            old_name = self.original_name(record_id)
            if old_name.casefold() == new_name.casefold():
                self.parent[record_id] = dest_parent
                if not bool(self.is_dir[record_id]):
                    self.size[record_id] = max(0, int(size or 0))
                return
            self._rename(record_id, new_name, dest_parent, size)
            self._maybe_compact()

    def apply_watch_event(self, event) -> None:
        event_type = getattr(event, 'event_type', '')
        src = getattr(event, 'src_path', '') or ''
        if not src:
            return
        if event_type == 'deleted':
            self.note_deleted(src)
            return
        if event_type == 'created':
            if not os.path.exists(src):
                return
            stat = os.stat(src)
            self.note_created(src, os.path.basename(src), stat.st_size, os.path.isdir(src))
            return
        if event_type == 'modified':
            if not os.path.exists(src):
                return
            self.note_size(src, os.stat(src).st_size)
            return
        if event_type == 'moved':
            dest = getattr(event, 'dest_path', None)
            if dest and os.path.exists(dest):
                stat = os.stat(dest)
                self.note_moved(src, dest, stat.st_size, os.path.isdir(dest))
            else:
                self.note_deleted(src)

    def sync_scanned_directory(self, dir_path: str, fresh_node) -> None:
        """Merge a freshly scanned directory into the index.

        Directories marked skip_recurse keep any already indexed descendants.
        That preserves files the folder tree does not expand, such as node_modules.
        """
        with self._lock:
            if fresh_node is None:
                return
            child_map = self._build_child_map()
            dir_id = self._find_id(dir_path)
            if dir_id is None:
                parent_path = os.path.dirname(os.path.abspath(dir_path).rstrip('\\'))
                parent_id = self._find_id(parent_path) if parent_path else None
                if parent_id is None:
                    return
                self._add_node_recursive(parent_id, fresh_node, child_map)
            else:
                self._sync_dir_id(dir_id, fresh_node, child_map)
            self._maybe_compact()

    def original_name(self, record_id: int) -> str:
        offset = int(self.orig_off[record_id])
        length = int(self.orig_len[record_id])
        return bytes(self.orig_blob[offset:offset + length]).decode('utf-8', errors='replace')

    def path_of(self, record_id: int) -> str:
        if record_id <= 0 or record_id >= self.count:
            return self.drive_root
        parts: List[str] = []
        seen = set()
        current = record_id
        while current and current not in seen and 0 < current < self.count:
            seen.add(current)
            parts.append(self.original_name(current))
            parent = int(self.parent[current])
            if parent in (PARENT_NONE, current):
                break
            current = parent
            if len(parts) > 512:
                break
        parts.reverse()
        if not parts:
            return self.drive_root
        return os.path.join(self.drive_root, *parts)

    def _empty(self, message: str) -> dict:
        return {
            'results': [],
            'total': 0,
            'partial': self.partial,
            'expanded': False,
            'message': message,
        }

    def _fold_at(self, record_id: int) -> bytes:
        offset = int(self.fold_off[record_id])
        length = int(self.fold_len[record_id])
        return bytes(self.fold_blob[offset:offset + length])

    def _rev_at(self, record_id: int) -> bytes:
        return self._fold_at(record_id)[::-1]

    def _lower_bound(self, order: array, needle: bytes, keyfn) -> int:
        lo = 0
        hi = len(order)
        while lo < hi:
            mid = (lo + hi) // 2
            if keyfn(int(order[mid])) < needle:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _upper_bound(self, order: array, needle: bytes, keyfn) -> int:
        lo = 0
        hi = len(order)
        while lo < hi:
            mid = (lo + hi) // 2
            if keyfn(int(order[mid])) <= needle:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _first_after_prefix(self, order: array, prefix: bytes, keyfn) -> int:
        lo = 0
        hi = len(order)
        while lo < hi:
            mid = (lo + hi) // 2
            name = keyfn(int(order[mid]))
            if name.startswith(prefix) or name < prefix:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _prefix_bounds(self, order: array, prefix: bytes, keyfn) -> Tuple[int, int]:
        if not prefix:
            return 0, 0
        lo = self._lower_bound(order, prefix, keyfn)
        hi = self._first_after_prefix(order, prefix, keyfn)
        if hi < lo:
            return lo, lo
        return lo, hi

    def _collect(self, plan: _Plan, kind: str, limit: int):
        heap: List[Tuple[int, int, int, int]] = []
        total = 0
        want_dirs: List[bool] = []
        if kind in ('file', 'both'):
            want_dirs.append(False)
        if kind in ('dir', 'both'):
            want_dirs.append(True)

        if plan.mode == 'contains':
            return heap, self._scan_blob(plan.needle, want_dirs, plan.rank, heap, limit, parts=None)

        if plan.mode == 'glob' and plan.parts and not plan.parts[0] and not plan.parts[-1]:
            return heap, self._scan_blob(plan.needle, want_dirs, plan.rank, heap, limit, parts=plan.parts)

        for is_dir in want_dirs:
            order = self.dir_order if is_dir else self.file_order
            reverse_order = self.dir_rev if is_dir else self.file_rev
            if plan.mode == 'exact':
                lo = self._lower_bound(order, plan.needle, self._fold_at)
                hi = self._upper_bound(order, plan.needle, self._fold_at)
                total += self._consume_span(order, lo, hi, plan.rank, heap, limit)
            elif plan.mode == 'prefix':
                lo, hi = self._prefix_bounds(order, plan.needle, self._fold_at)
                total += self._consume_span(order, lo, hi, plan.rank, heap, limit)
            elif plan.mode == 'suffix':
                lo, hi = self._prefix_bounds(reverse_order, plan.needle[::-1], self._rev_at)
                total += self._consume_span(reverse_order, lo, hi, plan.rank, heap, limit)
            else:
                total += self._consume_glob_bounded(plan, is_dir, heap, limit)
            total += self._consume_pending(plan, is_dir, heap, limit)
        return heap, total

    def _consume_span(self, order: array, lo: int, hi: int, rank: int, heap, limit: int) -> int:
        matched = 0
        deleted = self.deleted
        for pos in range(lo, hi):
            record_id = int(order[pos])
            if record_id in deleted:
                continue
            matched += 1
            self._consider(heap, limit, rank, record_id)
        return matched

    def _consume_glob_bounded(self, plan: _Plan, is_dir: bool, heap, limit: int) -> int:
        parts = plan.parts
        if parts[0]:
            order = self.dir_order if is_dir else self.file_order
            lo, hi = self._prefix_bounds(order, parts[0], self._fold_at)
        elif parts[-1]:
            order = self.dir_rev if is_dir else self.file_rev
            lo, hi = self._prefix_bounds(order, parts[-1][::-1], self._rev_at)
        else:
            return 0
        matched = 0
        deleted = self.deleted
        for pos in range(lo, hi):
            record_id = int(order[pos])
            if record_id in deleted:
                continue
            if not _glob_match(self._fold_at(record_id), parts):
                continue
            matched += 1
            self._consider(heap, limit, plan.rank, record_id)
        return matched

    def _consume_pending(self, plan: _Plan, is_dir: bool, heap, limit: int) -> int:
        matched = 0
        deleted = self.deleted
        for record_id in self.pending:
            if record_id in deleted or record_id >= self.count:
                continue
            if bool(self.is_dir[record_id]) != is_dir:
                continue
            name = self._fold_at(record_id)
            if not self._pending_match(plan, name):
                continue
            matched += 1
            self._consider(heap, limit, plan.rank, record_id)
        return matched

    def _pending_match(self, plan: _Plan, name: bytes) -> bool:
        if plan.mode == 'exact':
            return name == plan.needle
        if plan.mode == 'prefix':
            return name.startswith(plan.needle)
        if plan.mode == 'suffix':
            return name.endswith(plan.needle)
        if plan.mode == 'contains':
            return plan.needle in name
        return _glob_match(name, plan.parts)

    def _scan_blob(self, needle: bytes, want_dirs: Sequence[bool], rank: int, heap, limit: int, parts) -> int:
        if not needle:
            return 0
        want = set(want_dirs)
        blob = self.fold_blob
        deleted = self.deleted
        matched = 0
        start = 0
        needle_len = len(needle)
        while start < len(blob):
            pos = blob.find(needle, start)
            if pos < 0:
                break
            left = blob.rfind(b'\0', 0, pos)
            left = 0 if left < 0 else left + 1
            record_id = bisect.bisect_right(self.fold_off, pos) - 1
            if record_id <= 0 or record_id >= self.count:
                start = pos + 1
                continue
            name_start = int(self.fold_off[record_id])
            name_len = int(self.fold_len[record_id])
            name_end = name_start + name_len
            if name_start != left or pos < name_start or pos + needle_len > name_end:
                start = pos + 1
                continue
            start = name_end + 1
            if record_id in deleted:
                continue
            if bool(self.is_dir[record_id]) not in want:
                continue
            if parts is not None:
                name = bytes(blob[name_start:name_end])
                if not _glob_match(name, parts):
                    continue
            matched += 1
            self._consider(heap, limit, rank, record_id)
        return matched

    def _consider(self, heap, limit: int, rank: int, record_id: int) -> None:
        item = (-rank, -int(self.fold_len[record_id]), int(self.size[record_id]), record_id)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)

    def _materialize(self, heap) -> List[dict]:
        results = []
        for _rank, _length, _size, record_id in sorted(heap, reverse=True):
            results.append({
                'name': self.original_name(record_id),
                'path': self.path_of(record_id),
                'value': int(self.size[record_id]),
                'is_dir': bool(self.is_dir[record_id]),
            })
        return results

    def _find_id(self, path: str) -> Optional[int]:
        target = _norm_path(path)
        if not target:
            return None
        if target == _norm_path(self.drive_root):
            return 0
        base = os.path.basename(target.rstrip('\\'))
        if not base:
            return 0 if target.rstrip('\\') == _norm_path(self.drive_root).rstrip('\\') else None
        needle = base.casefold().encode('utf-8', errors='replace')
        for record_id in self._exact_ids(needle):
            if _norm_path(self.path_of(record_id)) == target:
                return record_id
        return None

    def _exact_ids(self, needle: bytes) -> List[int]:
        found: List[int] = []
        deleted = self.deleted
        for order in (self.file_order, self.dir_order):
            lo = self._lower_bound(order, needle, self._fold_at)
            hi = self._upper_bound(order, needle, self._fold_at)
            for pos in range(lo, hi):
                record_id = int(order[pos])
                if record_id not in deleted:
                    found.append(record_id)
        seen = set(found)
        for record_id in self.pending:
            if record_id in deleted or record_id in seen or record_id >= self.count:
                continue
            if self._fold_at(record_id) == needle:
                found.append(record_id)
                seen.add(record_id)
        return found

    def _append(self, parent_id: int, name: str, size: int, is_dir: bool) -> int:
        if parent_id < 0 or not name:
            return -1
        orig, folded = _encode_name(name)
        record_id = self.count
        self.orig_off.append(len(self.orig_blob))
        self.orig_len.append(len(orig))
        self.orig_blob.extend(orig)
        self.orig_blob.append(0)
        self.fold_off.append(len(self.fold_blob))
        self.fold_len.append(len(folded))
        self.fold_blob.extend(folded)
        self.fold_blob.append(0)
        self.parent.append(int(parent_id))
        self.size.append(max(0, int(size or 0)))
        self.is_dir.append(1 if is_dir else 0)
        self.count += 1
        self.pending.append(record_id)
        if is_dir:
            self.dir_count += 1
        else:
            self.file_count += 1
        return record_id

    def _note_created(self, path: str, name: str, size: int, is_dir: bool) -> None:
        existing = self._find_id(path)
        if existing is not None and existing not in self.deleted:
            if not bool(self.is_dir[existing]):
                self.size[existing] = max(0, int(size or 0))
            return
        parent_path = os.path.dirname(os.path.abspath(path).rstrip('\\'))
        parent_id = self._find_id(parent_path) if parent_path else None
        if parent_id is None:
            return
        self._append(parent_id, name or os.path.basename(path), size, is_dir)

    def _rename(self, record_id: int, new_name: str, new_parent: int, size: int) -> None:
        is_dir = bool(self.is_dir[record_id])
        kept_size = int(self.size[record_id]) if is_dir else max(0, int(size or 0))
        new_id = self._append(new_parent, new_name, kept_size, is_dir)
        if new_id < 0:
            return
        if is_dir:
            for index in range(self.count):
                if index == new_id or index in self.deleted:
                    continue
                if int(self.parent[index]) == record_id:
                    self.parent[index] = new_id
        self.deleted.add(record_id)

    def _build_child_map(self):
        mapping: Dict[int, List[int]] = defaultdict(list)
        deleted = self.deleted
        for record_id in range(1, self.count):
            if record_id in deleted:
                continue
            parent = int(self.parent[record_id])
            if parent == PARENT_NONE:
                continue
            mapping[parent].append(record_id)
        return mapping

    def _mark_deleted_recursive(self, record_id: int, child_map) -> None:
        if record_id <= 0:
            return
        stack = [record_id]
        seen = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            self.deleted.add(current)
            stack.extend(child_map.get(current, ()))

    def _add_node_recursive(self, parent_id: int, node, child_map) -> int:
        name = getattr(node, 'name', '') or os.path.basename(getattr(node, 'path', '') or '')
        record_id = self._append(parent_id, name, _node_size(node), bool(getattr(node, 'is_dir', False)))
        if record_id < 0:
            return record_id
        child_map.setdefault(parent_id, []).append(record_id)
        if getattr(node, 'is_dir', False) and not getattr(node, 'skip_recurse', False):
            for child in getattr(node, 'children', ()) or ():
                self._add_node_recursive(record_id, child, child_map)
        return record_id

    def _sync_dir_id(self, dir_id: int, fresh_node, child_map) -> None:
        if getattr(fresh_node, 'skip_recurse', False):
            return
        # Directory totals from the visible tree omit skipped descendants that
        # the index still stores, so leave the indexed folder size alone.
        existing = [record_id for record_id in child_map.get(dir_id, ()) if record_id not in self.deleted]
        existing_by_name = {}
        for record_id in existing:
            existing_by_name.setdefault(self.original_name(record_id).casefold(), record_id)
        fresh_by_name = {}
        for child in getattr(fresh_node, 'children', ()) or ():
            child_name = getattr(child, 'name', '') or ''
            if child_name:
                fresh_by_name.setdefault(child_name.casefold(), child)

        for folded_name, child in fresh_by_name.items():
            current = existing_by_name.get(folded_name)
            if current is None:
                self._add_node_recursive(dir_id, child, child_map)
                continue
            if bool(self.is_dir[current]) != bool(getattr(child, 'is_dir', False)):
                self._mark_deleted_recursive(current, child_map)
                self._add_node_recursive(dir_id, child, child_map)
                continue
            if getattr(child, 'is_dir', False):
                self._sync_dir_id(current, child, child_map)
            else:
                self.size[current] = _node_size(child)

        for folded_name, record_id in existing_by_name.items():
            if folded_name not in fresh_by_name and record_id not in self.deleted:
                self._mark_deleted_recursive(record_id, child_map)

    def _maybe_compact(self) -> None:
        if len(self.deleted) + len(self.pending) >= COMPACT_THRESHOLD:
            self._compact()

    def _compact(self) -> None:
        child_map = self._build_child_map()
        records: List[Tuple[int, str, int, bool]] = []
        old_to_new = {0: 0}
        records.append((
            -1,
            self.original_name(0) if self.count else self.drive_root,
            int(self.size[0]) if self.count else 0,
            True,
        ))
        queue: deque = deque([0])
        while queue:
            old_id = queue.popleft()
            for child_id in child_map.get(old_id, ()):
                if child_id in self.deleted or child_id in old_to_new:
                    continue
                old_to_new[child_id] = len(records)
                records.append((
                    old_to_new[old_id],
                    self.original_name(child_id),
                    int(self.size[child_id]),
                    bool(self.is_dir[child_id]),
                ))
                queue.append(child_id)
        rebuilt = self._from_records(records, self.drive_root, self.partial)
        self._steal(rebuilt)

    def _steal(self, other: 'NameIndex') -> None:
        self.drive_root = other.drive_root
        self.partial = other.partial
        self.count = other.count
        self.file_count = other.file_count
        self.dir_count = other.dir_count
        self.parent = other.parent
        self.orig_off = other.orig_off
        self.orig_len = other.orig_len
        self.fold_off = other.fold_off
        self.fold_len = other.fold_len
        self.size = other.size
        self.is_dir = other.is_dir
        self.orig_blob = other.orig_blob
        self.fold_blob = other.fold_blob
        self.file_order = other.file_order
        self.dir_order = other.dir_order
        self.file_rev = other.file_rev
        self.dir_rev = other.dir_rev
        self.deleted = set()
        self.pending = []

    def _serialize(self) -> bytes:
        drive = self.drive_root.encode('utf-8')
        flags = 1 if self.partial else 0
        buffer = bytearray()
        buffer.extend(_HEADER.pack(
            MAGIC,
            VERSION,
            flags,
            self.count,
            len(self.file_order),
            len(self.dir_order),
            len(drive),
        ))
        buffer.extend(drive)
        self._pack_bytes(buffer, bytes(self.orig_blob))
        self._pack_bytes(buffer, bytes(self.fold_blob))
        self._pack_array(buffer, self.parent)
        self._pack_array(buffer, self.orig_off)
        self._pack_array(buffer, self.orig_len)
        self._pack_array(buffer, self.fold_off)
        self._pack_array(buffer, self.fold_len)
        self._pack_array(buffer, self.size)
        raw_flags = bytes(self.is_dir)
        buffer.extend(struct.pack('<I', len(raw_flags)))
        buffer.extend(raw_flags)
        self._pack_array(buffer, self.file_order)
        self._pack_array(buffer, self.dir_order)
        self._pack_array(buffer, self.file_rev)
        self._pack_array(buffer, self.dir_rev)
        return bytes(buffer)

    @staticmethod
    def _pack_bytes(buffer: bytearray, data: bytes) -> None:
        buffer.extend(struct.pack('<I', len(data)))
        buffer.extend(data)

    @staticmethod
    def _pack_array(buffer: bytearray, values: array) -> None:
        raw = values.tobytes()
        buffer.extend(struct.pack('<I', len(raw)))
        buffer.extend(raw)

    @classmethod
    def _deserialize(cls, raw: bytes) -> 'NameIndex':
        if len(raw) < _HEADER.size:
            raise ValueError('name index header is short')
        magic, version, header_flags, count, nfiles, ndirs, drive_len = _HEADER.unpack_from(raw, 0)
        if magic != MAGIC or version != VERSION:
            raise ValueError('unrecognized name index')
        offset = _HEADER.size
        if offset + drive_len > len(raw):
            raise ValueError('name index drive path is short')
        drive = raw[offset:offset + drive_len].decode('utf-8', errors='replace')
        offset += drive_len
        orig_blob, offset = cls._read_bytes(raw, offset)
        fold_blob, offset = cls._read_bytes(raw, offset)
        parent, offset = cls._read_array(raw, offset, 'I', count)
        orig_off, offset = cls._read_array(raw, offset, 'I', count)
        orig_len, offset = cls._read_array(raw, offset, 'H', count)
        fold_off, offset = cls._read_array(raw, offset, 'I', count)
        fold_len, offset = cls._read_array(raw, offset, 'H', count)
        sizes, offset = cls._read_array(raw, offset, 'Q', count)
        flag_len = struct.unpack_from('<I', raw, offset)[0]
        offset += 4
        if flag_len != count:
            raise ValueError('name index flag count mismatch')
        flags = bytearray(raw[offset:offset + flag_len])
        offset += flag_len
        file_order, offset = cls._read_array(raw, offset, 'I', nfiles)
        dir_order, offset = cls._read_array(raw, offset, 'I', ndirs)
        file_rev, offset = cls._read_array(raw, offset, 'I', nfiles)
        dir_rev, offset = cls._read_array(raw, offset, 'I', ndirs)

        index = cls()
        index.drive_root = drive
        index.partial = bool(header_flags & 1)
        index.count = count
        index.file_count = nfiles
        index.dir_count = ndirs
        index.parent = parent
        index.orig_off = orig_off
        index.orig_len = orig_len
        index.fold_off = fold_off
        index.fold_len = fold_len
        index.size = sizes
        index.is_dir = flags
        index.orig_blob = bytearray(orig_blob)
        index.fold_blob = bytearray(fold_blob)
        index.file_order = file_order
        index.dir_order = dir_order
        index.file_rev = file_rev
        index.dir_rev = dir_rev
        return index

    @staticmethod
    def _read_bytes(raw: bytes, offset: int) -> Tuple[bytes, int]:
        length = struct.unpack_from('<I', raw, offset)[0]
        offset += 4
        end = offset + length
        if end > len(raw):
            raise ValueError('name index blob is short')
        return raw[offset:end], end

    @staticmethod
    def _read_array(raw: bytes, offset: int, typecode: str, count: int):
        itemsize = array(typecode).itemsize
        expect = count * itemsize
        length = struct.unpack_from('<I', raw, offset)[0]
        offset += 4
        if length != expect:
            raise ValueError('name index array size mismatch')
        end = offset + length
        if end > len(raw):
            raise ValueError('name index array is short')
        values = array(typecode)
        if length:
            values.frombytes(raw[offset:end])
        return values, end
