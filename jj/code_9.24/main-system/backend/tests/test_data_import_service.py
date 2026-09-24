import unittest
from pathlib import Path

from app.services.data_import_service import (
    detect_archive_extension,
    ensure_safe_storage_path,
)


class DataImportServiceTests(unittest.TestCase):
    def test_detect_archive_extension_supports_required_types(self):
        self.assertEqual(detect_archive_extension("a.tar.gz"), "tar.gz")
        self.assertEqual(detect_archive_extension("a.tgz"), "tgz")
        self.assertEqual(detect_archive_extension("a.zip"), "zip")
        self.assertEqual(detect_archive_extension("a.tar"), "tar")
        self.assertIsNone(detect_archive_extension("a.rar"))

    def test_ensure_safe_storage_path_blocks_traversal(self):
        root = Path("/tmp/data-import-root")
        with self.assertRaises(ValueError):
            ensure_safe_storage_path(root, "../escape.zip")


if __name__ == "__main__":
    unittest.main()
