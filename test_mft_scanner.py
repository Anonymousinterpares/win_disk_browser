"""Tests for NTFS MFT scanner (Windows + admin required for live scan)."""

import os
import sys

import pytest

from mft_scanner import can_use_mft_scan, is_ntfs_volume, scan_drive_mft


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
