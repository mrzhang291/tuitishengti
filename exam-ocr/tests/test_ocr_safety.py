import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ocr_exam import MinerUError, extract_zip, normalize_api_base


class OcrSafetyTests(unittest.TestCase):
    def test_api_base_is_configurable_but_rejects_remote_http_and_credentials(self):
        self.assertEqual(normalize_api_base("https://mineru.net/api/v4/"), "https://mineru.net/api/v4")
        self.assertEqual(normalize_api_base("http://127.0.0.1:9000/api"), "http://127.0.0.1:9000/api")
        with self.assertRaises(MinerUError):
            normalize_api_base("http://example.com/api")
        with self.assertRaises(MinerUError):
            normalize_api_base("https://user:secret@example.com/api")

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.txt", "blocked")
            with self.assertRaises(MinerUError):
                extract_zip(archive_path, root / "extract")
            self.assertFalse((root / "outside.txt").exists())

    def test_normal_zip_extracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "safe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("result/full.md", "ok")
            extract_zip(archive_path, root / "extract")
            self.assertEqual((root / "extract" / "result" / "full.md").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
