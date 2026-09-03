"""Round-trip tests for compressed snapshot cache."""

import os
import tempfile
import zlib

from disk_analyzer_fixed import FileNode
from mmap_snapshot import (
    load_mmap_snapshot,
    save_mmap_snapshot,
    has_mmap_snapshot,
    _build_v2_payload,
)


def _make_sample_tree():
    root = FileNode(path='C:\\', name='C:\\', is_dir=True, size=0)
    windows = FileNode(path='C:\\Windows', name='Windows', is_dir=True, size=0, parent=root)
    file_a = FileNode(path='C:\\Windows\\a.dll', name='a.dll', size=50, is_dir=False, parent=windows)
    file_b = FileNode(path='C:\\Windows\\b.dll', name='b.dll', size=50, is_dir=False, parent=windows)
    windows.children = [file_b, file_a]
    root.children = [windows]
    FileNode.finalize_dir_size(root)
    return root


def test_compressed_snapshot_roundtrip():
    root = _make_sample_tree()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'C_.snap')
        save_mmap_snapshot(root, path, usn_journal_id=7, usn_next=99)
        assert has_mmap_snapshot(path)
        loaded = load_mmap_snapshot(path)
        assert loaded is not None
        assert loaded.path == 'C:\\'
        assert loaded.is_dir
        assert loaded.get_size() == 100
        assert len(loaded.children) == 1
        assert loaded.children[0].name == 'Windows'
        assert len(loaded.children[0].children) == 2


def test_compressed_snapshot_preserves_paths():
    root = _make_sample_tree()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'C_.snap')
        save_mmap_snapshot(root, path)
        loaded = load_mmap_snapshot(path)
        paths = set()

        def walk(node):
            paths.add(node.path)
            for child in node.children:
                walk(child)

        walk(loaded)
        assert 'C:\\Windows\\a.dll' in paths
        assert 'C:\\Windows\\b.dll' in paths


def test_compressed_snapshot_smaller_than_uncompressed():
    root = _make_sample_tree()
    payload = _build_v2_payload(root, 0, 0)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'C_.snap')
        save_mmap_snapshot(root, path)
        compressed_size = os.path.getsize(path)
        assert compressed_size < len(payload)
