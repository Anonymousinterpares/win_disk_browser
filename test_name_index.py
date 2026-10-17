"""Tests for the compact file/folder name index."""

import os
import tempfile

from disk_analyzer_fixed import FileNode
from mft_scanner import MftEntry
from name_index import NameIndex


def _tree():
    root = FileNode(path='C:\\', name='C:', is_dir=True, size=0)
    proj = FileNode(path='C:\\proj', name='proj', is_dir=True, parent=root)
    modules = FileNode(path='C:\\proj\\node_modules', name='node_modules', is_dir=True, parent=proj)
    package = FileNode(path='C:\\proj\\node_modules\\pkg', name='pkg', is_dir=True, parent=modules)
    left = FileNode(
        path='C:\\proj\\node_modules\\pkg\\left-pad.js',
        name='left-pad.js',
        size=4,
        is_dir=False,
        parent=package,
    )
    readme = FileNode(path='C:\\proj\\readme.md', name='readme.md', size=8, is_dir=False, parent=proj)
    old = FileNode(path='C:\\proj\\old.txt', name='old.txt', size=2, is_dir=False, parent=proj)
    report = FileNode(
        path='C:\\proj\\report-2024-final.pdf',
        name='report-2024-final.pdf',
        size=100,
        is_dir=False,
        parent=proj,
    )
    notes = FileNode(path='C:\\proj\\notes.pdf', name='notes.pdf', size=3, is_dir=False, parent=proj)
    windows = FileNode(path='C:\\Windows', name='Windows', is_dir=True, parent=root)
    dll_a = FileNode(path='C:\\Windows\\a.dll', name='a.dll', size=50, is_dir=False, parent=windows)
    dll_b = FileNode(path='C:\\Windows\\b.dll', name='b.dll', size=10, is_dir=False, parent=windows)
    folder_dll = FileNode(path='C:\\Windows\\x.dll', name='x.dll', is_dir=True, parent=windows)
    short_a = FileNode(path='C:\\proj\\ax.txt', name='ax.txt', size=10, is_dir=False, parent=proj)
    short_aa = FileNode(path='C:\\proj\\axa.txt', name='axa.txt', size=1, is_dir=False, parent=proj)
    short_ab = FileNode(path='C:\\proj\\axb.txt', name='axb.txt', size=5, is_dir=False, parent=proj)
    short_ac = FileNode(path='C:\\proj\\axc.txt', name='axc.txt', size=50, is_dir=False, parent=proj)

    package.children = [left]
    modules.children = [package]
    proj.children = [modules, readme, old, report, notes, short_a, short_aa, short_ab, short_ac]
    windows.children = [dll_a, dll_b, folder_dll]
    root.children = [proj, windows]
    FileNode.finalize_dir_size(root)
    return root


def _index(partial=False):
    return NameIndex.from_tree(_tree(), partial=partial)


def test_exact_prefix_suffix_and_kind():
    index = _index()
    exact = index.search('a.dll', kind='file')
    assert exact['total'] == 1
    assert exact['expanded'] is False
    assert exact['results'][0]['path'] == 'C:\\Windows\\a.dll'
    assert exact['results'][0]['value'] == 50

    folded = index.search('A.DLL', kind='file')
    assert folded['total'] == 1
    assert folded['results'][0]['name'] == 'a.dll'

    prefix = index.search('read*', kind='file')
    assert [item['name'] for item in prefix['results']] == ['readme.md']

    suffix = index.search('*.dll', kind='file')
    assert suffix['total'] == 2
    assert {item['name'] for item in suffix['results']} == {'a.dll', 'b.dll'}

    folders = index.search('*.dll', kind='dir')
    assert [item['name'] for item in folders['results']] == ['x.dll']
    assert folders['results'][0]['is_dir'] is True

    both = index.search('*.dll', kind='both')
    assert both['total'] == 3


def test_contains_glob_and_fallback():
    index = _index()
    contains = index.search('*pad*', kind='file')
    assert contains['total'] == 1
    assert contains['results'][0]['name'] == 'left-pad.js'
    assert contains['results'][0]['path'] == 'C:\\proj\\node_modules\\pkg\\left-pad.js'

    glob = index.search('rep*2024*.pdf', kind='file')
    assert [item['name'] for item in glob['results']] == ['report-2024-final.pdf']

    pdfs = index.search('*.pdf', kind='file')
    assert pdfs['total'] == 2

    fallback = index.search('pad', kind='file')
    assert fallback['expanded'] is True
    assert fallback['total'] == 1
    assert fallback['results'][0]['name'] == 'left-pad.js'

    short_miss = index.search('zz', kind='file')
    assert short_miss['total'] == 0
    assert short_miss['expanded'] is False

    assert index.search('*', kind='both')['message']
    assert index.search('*a*', kind='file')['message']
    assert index.search('a*', kind='file')['message']


def test_ranking_keeps_total_when_capped():
    index = _index()
    ranked = index.search('ax*', kind='file', limit=2)
    assert ranked['total'] == 4
    assert [item['name'] for item in ranked['results']] == ['ax.txt', 'axc.txt']
    assert index.search('axb*', kind='file')['results'][0]['name'] == 'axb.txt'
    assert index.search('axb*', kind='file')['total'] == 1


def test_folders_do_not_return_files():
    index = _index()
    folders = index.search('proj', kind='dir')
    assert folders['total'] == 1
    assert folders['results'][0]['is_dir'] is True
    assert index.search('proj', kind='file')['total'] == 0


def test_mft_index_includes_nested_names():
    entries = {
        5: MftEntry(index=5, parent_index=5, name='.', is_dir=True, size=100),
        10: MftEntry(index=10, parent_index=5, name='proj', is_dir=True, size=40),
        11: MftEntry(index=11, parent_index=10, name='node_modules', is_dir=True, size=20),
        12: MftEntry(index=12, parent_index=11, name='left-pad.js', is_dir=False, size=4),
    }
    index = NameIndex.from_mft(entries, 5, 'C:\\', entries.keys())
    assert index.partial is False
    hit = index.search('left-pad.js', kind='file')
    assert hit['total'] == 1
    assert hit['results'][0]['path'] == 'C:\\proj\\node_modules\\left-pad.js'

    # The real NTFS root record is named "." and is not stored. Children still
    # point at record 5.
    without_root = {key: value for key, value in entries.items() if key != 5}
    indexed = NameIndex.from_mft(without_root, 5, 'C:\\', [5, *without_root.keys()])
    assert indexed.search('left-pad.js', kind='file')['results'][0]['path'] == (
        'C:\\proj\\node_modules\\left-pad.js'
    )


def test_save_load_roundtrip(tmp_path=None):
    index = _index(partial=True)
    index.note_created(r'C:\proj\new.txt', 'new.txt', 9, False)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'C_.idx')
        index.save(path)
        loaded = NameIndex.load(path)
        assert loaded is not None
        assert loaded.partial is True
        assert loaded.search('new.txt', kind='file')['total'] == 1
        assert loaded.search('*.dll', kind='file')['total'] == 2
        assert loaded.search('*pad*', kind='file')['results'][0]['path'].endswith('left-pad.js')


def test_live_updates_and_directory_rename():
    index = _index()
    index.note_deleted(r'C:\proj\old.txt')
    assert index.search('old.txt', kind='file')['total'] == 0

    index.note_created(r'C:\proj\fresh.txt', 'fresh.txt', 6, False)
    created = index.search('fresh.txt', kind='file')
    assert created['total'] == 1
    assert created['results'][0]['path'] == 'C:\\proj\\fresh.txt'

    index.note_size(r'C:\proj\fresh.txt', 60)
    assert index.search('fresh.txt', kind='file')['results'][0]['value'] == 60

    index.note_moved(r'C:\Windows\a.dll', r'C:\proj\a.dll', 50, False)
    moved = index.search('a.dll', kind='file')
    assert moved['total'] == 1
    assert moved['results'][0]['path'] == 'C:\\proj\\a.dll'

    index.note_moved(r'C:\proj\node_modules', r'C:\proj\modules', 0, True)
    renamed = index.search('left-pad.js', kind='file')
    assert renamed['total'] == 1
    assert renamed['results'][0]['path'] == 'C:\\proj\\modules\\pkg\\left-pad.js'
    assert index.search('node_modules', kind='dir')['total'] == 0
    assert index.search('modules', kind='dir')['total'] == 1


def test_sync_keeps_skipped_descendants():
    index = _index(partial=False)
    fresh = FileNode(path='C:\\proj', name='proj', is_dir=True)
    skipped = FileNode(
        path='C:\\proj\\node_modules',
        name='node_modules',
        is_dir=True,
        skip_recurse=True,
        size=30,
        parent=fresh,
    )
    readme = FileNode(path='C:\\proj\\readme.md', name='readme.md', size=8, is_dir=False, parent=fresh)
    report = FileNode(
        path='C:\\proj\\report-2024-final.pdf',
        name='report-2024-final.pdf',
        size=100,
        is_dir=False,
        parent=fresh,
    )
    notes = FileNode(path='C:\\proj\\notes.pdf', name='notes.pdf', size=3, is_dir=False, parent=fresh)
    added = FileNode(path='C:\\proj\\new.txt', name='new.txt', size=9, is_dir=False, parent=fresh)
    kept_short = [
        FileNode(path='C:\\proj\\ax.txt', name='ax.txt', size=10, is_dir=False, parent=fresh),
        FileNode(path='C:\\proj\\axa.txt', name='axa.txt', size=1, is_dir=False, parent=fresh),
        FileNode(path='C:\\proj\\axb.txt', name='axb.txt', size=5, is_dir=False, parent=fresh),
        FileNode(path='C:\\proj\\axc.txt', name='axc.txt', size=50, is_dir=False, parent=fresh),
    ]
    fresh.children = [skipped, readme, report, notes, added, *kept_short]
    fresh._calculated_size = 200

    index.sync_scanned_directory(r'C:\proj', fresh)

    assert index.search('left-pad.js', kind='file')['total'] == 1
    assert index.search('old.txt', kind='file')['total'] == 0
    added_hit = index.search('new.txt', kind='file')
    assert added_hit['total'] == 1
    assert added_hit['results'][0]['path'] == 'C:\\proj\\new.txt'
    assert index.search('readme.md', kind='file')['total'] == 1


if __name__ == '__main__':
    import traceback
    failed = 0
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_') and callable(value)]
    for test in tests:
        try:
            test()
            print('ok', test.__name__)
        except Exception:
            failed += 1
            print('FAIL', test.__name__)
            traceback.print_exc()
    raise SystemExit(failed)
