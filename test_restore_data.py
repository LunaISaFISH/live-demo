"""The release restores only dataset files, and rejects corrupt/unsafe input."""
import hashlib
import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

import restore_data


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'bundle.zip'

    def bundle(self, files):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_extracts_only_data_without_replacing_source_or_env(self):
        digest = self.bundle({'FitCoach/data/pmdata/p16/record.json': '{}',
                              'FitCoach/run.py': 'untrusted', 'FitCoach/.env': 'secret'})
        restore_data.restore(self.archive, self.root / 'data', digest)
        self.assertEqual((self.root / 'data/p16/record.json').read_text(), '{}')
        self.assertFalse((self.root / 'run.py').exists())
        self.assertFalse((self.root / '.env').exists())

    def test_hash_mismatch_writes_nothing(self):
        self.bundle({'FitCoach/data/pmdata/test': 'x'})
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            restore_data.restore(self.archive, self.root / 'data', '0' * 64)
        self.assertFalse((self.root / 'data').exists())

    def test_traversal_rejected_before_any_write(self):
        for name in ['../escape', '/absolute', 'p16/../../escape', 'C:/escape', 'p16\\escape']:
            with self.subTest(name=name):
                digest = self.bundle({'FitCoach/data/pmdata/good': 'x',
                                      'FitCoach/data/pmdata/' + name: 'bad'})
                with self.assertRaises(ValueError):
                    restore_data.restore(self.archive, self.root / 'data', digest)
                self.assertFalse((self.root / 'data/good').exists())

    def test_empty_dataset_rejected(self):
        digest = self.bundle({'FitCoach/README.md': 'docs'})
        with self.assertRaisesRegex(ValueError, 'dataset'):
            restore_data.restore(self.archive, self.root / 'data', digest)

    def test_corrupt_zip_rejected(self):
        self.archive.write_bytes(b'not a zip')
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaises(zipfile.BadZipFile):
            restore_data.restore(self.archive, self.root / 'data', digest)

    def test_main_reuses_ready_data(self):
        with patch('sys.argv', ['restore_data.py']), patch.object(restore_data, 'ready', return_value=True):
            restore_data.main()

    def test_main_downloads_and_verifies_release(self):
        digest = self.bundle({'FitCoach/data/pmdata/p16/record.json': '{}'})
        with patch('sys.argv', ['restore_data.py']), patch.object(restore_data, 'ROOT', self.root), \
                patch.object(restore_data, 'DATA', self.root / 'dataset'), \
                patch.object(restore_data, 'ready', side_effect=[False, True]), \
                patch.object(restore_data, 'SHA256', digest), \
                patch.object(restore_data, 'restore') as extract, \
                patch('urllib.request.urlopen', return_value=io.BytesIO(self.archive.read_bytes())):
            restore_data.main()
            self.assertEqual((self.root / 'data' / restore_data.ASSET).read_bytes(), self.archive.read_bytes())
            extract.assert_called_once()

    def test_main_rejects_corrupt_download(self):
        with patch('sys.argv', ['restore_data.py']), patch.object(restore_data, 'ROOT', self.root), \
                patch.object(restore_data, 'ready', return_value=False), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(b'broken')):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                restore_data.main()
            self.assertFalse((self.root / 'data' / restore_data.ASSET).exists())

    def test_main_reports_missing_local_archive(self):
        with patch('sys.argv', ['restore_data.py', '--archive', str(self.root / 'missing.zip')]), \
                patch.object(restore_data, 'ready', return_value=False):
            with self.assertRaises(SystemExit) as result:
                restore_data.main()
            self.assertEqual(result.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
