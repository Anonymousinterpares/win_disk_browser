"""View-only file-type exclusions for TreeView. Never reads or writes the scan database."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from typing import Iterable, List

_SAVE_LOCK = threading.RLock()

CONFIG_NAME = 'exclude_config.json'

# Common types a disk browser shows by the thousand. Order is the checkbox order.
# These always stay in the file. Names added by hand are kept after them.
POPULAR_EXTENSIONS = [
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp',
    'mp4', 'mkv', 'avi', 'mov',
    'mp3', 'wav',
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt',
    'zip', 'rar', '7z',
    'exe', 'dll', 'msi',
    'iso', 'tmp', 'log', 'ini', 'sys',
]

# Name stays first and cannot be hidden. Widths are pixels after the user drags.
COLUMN_IDS = (
    'name', 'modified', 'created', 'type', 'size',
    'accessed', 'attributes', 'items',
)
DEFAULT_VISIBLE_COLUMNS = (
    'name', 'modified', 'created', 'type', 'size', 'items',
)
MIN_COLUMN_WIDTH = 48
MAX_COLUMN_WIDTH = 4000


class ExcludeConfigError(Exception):
    """The JSON file is damaged, so it must not be overwritten without a yes."""


def config_path() -> str:
    """JSON file in the project root, or beside the executable when frozen."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_NAME)


def _clean_extension(value: object) -> str:
    if not isinstance(value, str):
        return ''
    text = value.strip().lower()
    if text.startswith('.'):
        text = text[1:]
    if not text or len(text) > 16 or not text.isalnum():
        return ''
    return text


def _unique(values: Iterable[object]) -> List[str]:
    seen = set()
    cleaned: List[str] = []
    for value in values:
        ext = _clean_extension(value)
        if not ext or ext in seen:
            continue
        seen.add(ext)
        cleaned.append(ext)
    return cleaned


def default_columns() -> dict:
    return {
        'visible': list(DEFAULT_VISIBLE_COLUMNS),
        'widths': {},
    }


def default_settings() -> dict:
    return {
        'extensions': list(POPULAR_EXTENSIONS),
        'excluded': [],
        'columns': default_columns(),
    }


def _with_flag(settings: dict, corrupted: bool) -> dict:
    columns = settings.get('columns') or default_columns()
    return {
        'extensions': list(settings['extensions']),
        'excluded': list(settings['excluded']),
        'columns': {
            'visible': list(columns.get('visible') or []),
            'widths': dict(columns.get('widths') or {}),
        },
        'corrupted': corrupted,
    }


def _normalize_columns(raw: object) -> dict:
    columns = default_columns()
    if not isinstance(raw, dict):
        return columns
    visible = raw.get('visible')
    # pywebview can turn a one-item list into a string. Keep that single column.
    if isinstance(visible, str):
        visible = [visible]
    if isinstance(visible, list):
        chosen = {
            item for item in visible
            if isinstance(item, str) and item in COLUMN_IDS
        }
        chosen.add('name')
        columns['visible'] = [item for item in COLUMN_IDS if item in chosen]
    widths = {}
    if isinstance(raw.get('widths'), dict):
        for key, value in raw['widths'].items():
            if key not in COLUMN_IDS:
                continue
            try:
                pixels = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            if MIN_COLUMN_WIDTH <= pixels <= MAX_COLUMN_WIDTH:
                widths[key] = pixels
    columns['widths'] = widths
    return columns


def _merge_extensions(from_file: List[str]) -> List[str]:
    merged = list(POPULAR_EXTENSIONS)
    seen = set(merged)
    for ext in from_file:
        if ext in seen:
            continue
        merged.append(ext)
        seen.add(ext)
    return merged


def _normalize(raw: object) -> dict:
    """Hardcoded types first, then any extra types from the file."""
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    from_file = _unique(raw.get('extensions') or [])
    excluded = _unique(raw.get('excluded') or [])
    settings['extensions'] = _merge_extensions(from_file)
    known = set(settings['extensions'])
    for ext in excluded:
        if ext not in known:
            settings['extensions'].append(ext)
            known.add(ext)
    settings['excluded'] = [ext for ext in excluded if ext in known]
    if 'columns' in raw:
        settings['columns'] = _normalize_columns(raw.get('columns'))
    return settings


def _is_corrupt_payload(raw: object) -> bool:
    if not isinstance(raw, dict):
        return True
    if 'extensions' in raw and not isinstance(raw.get('extensions'), list):
        return True
    if 'excluded' in raw and not isinstance(raw.get('excluded'), list):
        return True
    return False


def _matches(raw: object, normalized: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    return (
        _unique(raw.get('extensions') or []) == normalized['extensions']
        and _unique(raw.get('excluded') or []) == normalized['excluded']
    )


def load_settings(path: str | None = None, repair: bool = True) -> dict:
    """Read the config. A damaged file is left untouched and marked corrupted."""
    target = path or config_path()
    if not os.path.isfile(target):
        settings = default_settings()
        save_settings(settings, target)
        return _with_flag(settings, False)
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _with_flag(default_settings(), True)
    if _is_corrupt_payload(raw):
        return _with_flag(default_settings(), True)
    settings = _normalize(raw)
    if repair and not _matches(raw, settings):
        save_settings(settings, target)
    return _with_flag(settings, False)


def save_settings(settings: dict, path: str | None = None) -> dict:
    with _SAVE_LOCK:
        return _write_settings(settings, path)


def _write_settings(settings: dict, path: str | None = None) -> dict:
    target = path or config_path()
    normalized = _normalize(settings)
    folder = os.path.dirname(target) or '.'
    fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(normalized, handle, indent=2)
            handle.write('\n')
        os.replace(temporary, target)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return normalized


def save_columns(visible: object, widths: object, path: str | None = None) -> dict:
    """Column visibility and dragged widths. Refuses to replace a damaged file."""
    with _SAVE_LOCK:
        return _save_columns(visible, widths, path)


def _save_columns(visible: object, widths: object, path: str | None = None) -> dict:
    current = load_settings(path, repair=False)
    if current.get('corrupted'):
        raise ExcludeConfigError(
            'exclude_config.json is damaged and was left unchanged'
        )
    current['columns'] = _normalize_columns({'visible': visible, 'widths': widths})
    saved = save_settings(current, path)
    return _with_flag(saved, False)


def save_excluded(excluded: object, path: str | None = None) -> dict:
    """Tick state only. Refuses to replace a damaged file."""
    with _SAVE_LOCK:
        return _save_excluded(excluded, path)


def _save_excluded(excluded: object, path: str | None = None) -> dict:
    current = load_settings(path, repair=False)
    if current.get('corrupted'):
        raise ExcludeConfigError(
            'exclude_config.json is damaged and was left unchanged'
        )
    if isinstance(excluded, str):
        chosen = [excluded]
    elif isinstance(excluded, (list, tuple, set)):
        chosen = list(excluded)
    else:
        chosen = []
    current['excluded'] = chosen
    saved = save_settings(current, path)
    return _with_flag(saved, False)


def reset_settings(path: str | None = None) -> dict:
    """Replace the file with the hardcoded types and nothing excluded."""
    with _SAVE_LOCK:
        saved = save_settings(default_settings(), path)
        return _with_flag(saved, False)
