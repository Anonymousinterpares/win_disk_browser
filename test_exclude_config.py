"""Exclude settings are a JSON file. They never touch the scan database."""

import json
import os
import tempfile
import unittest

import exclude_config as cfg


class ExcludeConfigTests(unittest.TestCase):
    def test_missing_file_is_created_with_nothing_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            settings = cfg.load_settings(path)
            self.assertEqual(settings['extensions'], cfg.POPULAR_EXTENSIONS)
            self.assertEqual(settings['excluded'], [])
            self.assertTrue(os.path.isfile(path))

    def test_ticking_persists_and_unticking_clears(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            cfg.load_settings(path)
            saved = cfg.save_excluded(['JPG', '.tmp', 'not a type'], path)
            self.assertEqual(saved['excluded'], ['jpg', 'tmp'])
            again = cfg.load_settings(path)
            self.assertEqual(again['excluded'], ['jpg', 'tmp'])
            cleared = cfg.save_excluded([], path)
            self.assertEqual(cleared['excluded'], [])

    def test_custom_extension_is_kept_and_missing_defaults_return(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'extensions': ['heic', 'jpg'], 'excluded': ['heic']}, handle)
            settings = cfg.load_settings(path)
            self.assertEqual(settings['extensions'][:len(cfg.POPULAR_EXTENSIONS)], cfg.POPULAR_EXTENSIONS)
            self.assertEqual(settings['extensions'][-1], 'heic')
            self.assertEqual(settings['extensions'].count('jpg'), 1)
            self.assertIn('sys', settings['extensions'])
            self.assertIn('ini', settings['extensions'])
            self.assertEqual(settings['excluded'], ['heic'])
            self.assertFalse(settings['corrupted'])
            with open(path, 'r', encoding='utf-8') as handle:
                stored = json.load(handle)
            self.assertEqual(stored['extensions'], settings['extensions'])
            self.assertEqual(stored['excluded'], ['heic'])

    def test_damaged_file_is_not_replaced_until_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{')
            settings = cfg.load_settings(path)
            self.assertTrue(settings['corrupted'])
            self.assertEqual(settings['extensions'], cfg.POPULAR_EXTENSIONS)
            with open(path, 'r', encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '{')
            with self.assertRaises(cfg.ExcludeConfigError):
                cfg.save_excluded(['jpg'], path)
            with open(path, 'r', encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '{')
            restored = cfg.reset_settings(path)
            self.assertFalse(restored['corrupted'])
            self.assertEqual(restored['excluded'], [])
            with open(path, 'r', encoding='utf-8') as handle:
                self.assertEqual(json.load(handle)['extensions'], cfg.POPULAR_EXTENSIONS)


    def test_column_visibility_and_widths_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            original = {'extensions': ['heic', 'jpg'], 'excluded': ['heic']}
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(original, handle)
            loaded = cfg.load_settings(path, repair=False)
            self.assertEqual(loaded['columns']['visible'], list(cfg.DEFAULT_VISIBLE_COLUMNS))
            self.assertEqual(loaded['columns']['widths'], {})
            self.assertEqual(loaded['extensions'][-1], 'heic')
            self.assertFalse(loaded['corrupted'])
            with open(path, 'r', encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), original)

            saved = cfg.save_columns(
                ['size', 'created'],
                {
                    'name': 240,
                    'type': 48,
                    'items': 4000,
                    'modified': 47,
                    'created': 4001,
                    'size': 10,
                    'nope': 100,
                },
                path,
            )
            self.assertEqual(saved['columns']['visible'], ['name', 'created', 'size'])
            self.assertEqual(saved['columns']['widths'], {'name': 240, 'type': 48, 'items': 4000})
            self.assertEqual(saved['excluded'], ['heic'])
            self.assertEqual(saved['extensions'][-1], 'heic')
            self.assertIn('sys', saved['extensions'])
            again = cfg.load_settings(path, repair=False)
            self.assertEqual(again['columns']['visible'][0], 'name')
            self.assertIn('created', again['columns']['visible'])
            self.assertEqual(again['columns']['widths']['name'], 240)
            self.assertNotIn('size', again['columns']['widths'])

            single = cfg.save_columns('accessed', {'accessed': '180'}, path)
            self.assertEqual(
                single['columns']['visible'],
                ['name', 'accessed'],
            )
            self.assertEqual(single['columns']['widths'], {'accessed': 180})

    def test_save_columns_refuses_damaged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'exclude_config.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{')
            with self.assertRaises(cfg.ExcludeConfigError):
                cfg.save_columns(['name', 'size'], {'name': 200}, path)
            with open(path, 'r', encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '{')


if __name__ == '__main__':
    unittest.main()
