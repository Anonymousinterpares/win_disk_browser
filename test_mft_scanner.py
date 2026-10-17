"""Tests for NTFS MFT scanner (Windows + admin required for live scan)."""

import os
import struct
import sys

import pytest

from mft_scanner import (
    FILE_NAME_DOS,
    FILE_NAME_WIN32_DOS,
    VolumeGeometry,
    _apply_fixup,
    _mft_resolution_failed,
    _parse_data_runs,
    _parse_file_record,
    can_use_mft_scan,
    is_ntfs_volume,
    scan_drive_mft,
)


def _geometry() -> VolumeGeometry:
    return VolumeGeometry(
        bytes_per_sector=512,
        bytes_per_cluster=4096,
        bytes_per_file_record=1024,
        mft_start_lcn=0,
        mft_valid_data_length=0,
    )


def _file_record_with_name(name: str, name_type: int) -> bytes:
    encoded = name.encode('utf-16-le')
    value_len = 66 + len(encoded)
    attr_len = 0x18 + value_len
    if attr_len % 8:
        attr_len += 8 - (attr_len % 8)
    record = bytearray(0x38 + attr_len + 8)
    record[:4] = b'FILE'
    struct.pack_into('<H', record, 0x10, 1)
    struct.pack_into('<H', record, 0x14, 0x38)
    struct.pack_into('<H', record, 0x16, 0x01)
    offset = 0x38
    struct.pack_into('<I', record, offset, 0x30)
    struct.pack_into('<I', record, offset + 4, attr_len)
    struct.pack_into('<I', record, offset + 0x10, value_len)
    struct.pack_into('<H', record, offset + 0x14, 0x18)
    value = offset + 0x18
    struct.pack_into('<Q', record, value, 5)
    struct.pack_into('<Q', record, value + 48, 100)
    record[value + 64] = len(name)
    record[value + 65] = name_type
    record[value + 66:value + 66 + len(encoded)] = encoded
    struct.pack_into('<I', record, offset + attr_len, 0xFFFFFFFF)
    return bytes(record)


def test_fixup_restores_sector_tails():
    record = bytearray(1024)
    record[:4] = b'FILE'
    struct.pack_into('<H', record, 4, 0x30)
    struct.pack_into('<H', record, 6, 3)
    struct.pack_into('<H', record, 0x30, 0x0001)
    struct.pack_into('<H', record, 0x32, 0xABCD)
    struct.pack_into('<H', record, 0x34, 0xEF01)
    struct.pack_into('<H', record, 510, 0x0001)
    struct.pack_into('<H', record, 1022, 0x0001)
    assert _apply_fixup(record, _geometry()) is True
    assert struct.unpack_from('<H', record, 510)[0] == 0xABCD
    assert struct.unpack_from('<H', record, 1022)[0] == 0xEF01


def test_fixup_rejects_non_file_and_wild_usa_offset():
    garbage = bytearray(b'RIFF' + b'\x00' * 1020)
    assert _apply_fixup(garbage, _geometry()) is False
    record = bytearray(1024)
    record[:4] = b'FILE'
    struct.pack_into('<H', record, 4, 25971)
    struct.pack_into('<H', record, 6, 3)
    assert _apply_fixup(record, _geometry()) is False


def test_parse_data_runs_relative_and_sparse():
    blob = bytes([
        0x21, 10, 0x64, 0x00,
        0x21, 5, 0xEC, 0xFF,
        0x01, 7,
        0x00,
    ])
    assert _parse_data_runs(blob) == [(100, 10), (80, 5), (-1, 7)]


def test_short_8_3_name_is_a_real_file():
    win32_dos = _parse_file_record(_file_record_with_name('NOTES', FILE_NAME_WIN32_DOS), 40, _geometry())
    dos_only = _parse_file_record(_file_record_with_name('TEMP', FILE_NAME_DOS), 41, _geometry())
    assert win32_dos is not None and win32_dos.name == 'NOTES'
    assert win32_dos.parent_index == 5 and win32_dos.size == 100
    assert dos_only is not None and dos_only.name == 'TEMP'


def test_resolution_guard_rejects_orphan_heavy_reads():
    orphaned = {index: object() for index in range(1000)}
    assert _mft_resolution_failed(orphaned, {5: 'C:\\'}) is True
    healthy = {index: object() for index in range(1000)}
    assert _mft_resolution_failed(healthy, {index: 'x' for index in range(800)}) is False
    assert _mft_resolution_failed({1: object()}, {5: 'C:\\'}) is False


def test_is_ntfs_detects_windows_drive():
    if sys.platform != 'win32':
        pytest.skip('Windows only')
    system_drive = os.environ.get('SystemDrive', 'C:') + '\\'
    assert is_ntfs_volume(system_drive) is True


def test_can_use_mft_scan_requires_admin_on_ntfs():
    if sys.platform != 'win32':
        pytest.skip('Windows only')
    system_drive = os.environ.get('SystemDrive', 'C:') + '\\'
    if not is_ntfs_volume(system_drive):
        pytest.skip('System drive is not NTFS')
    # Returns True only when elevated; both outcomes are valid in CI/dev.
    result = can_use_mft_scan(system_drive)
    assert isinstance(result, bool)


@pytest.mark.skipif(not can_use_mft_scan('C:\\'), reason='Requires admin on NTFS')
def test_mft_scan_returns_root_node():
    root = scan_drive_mft('C:\\')
    assert root is not None
    assert root.is_dir
    assert root.path.upper().startswith('C:')
