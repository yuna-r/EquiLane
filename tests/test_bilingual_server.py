"""Language selection uses the same constrained local report boundary."""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from equilane.server import create_server

ROOT = Path(__file__).resolve().parents[1]


class BilingualServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.reports = Path(self.temporary.name)
        saved = self.reports / "saved-run"
        saved.mkdir()
        (saved / "showcase.html").write_text('<!doctype html><html lang="ja"><body><h1>実行エラー</h1></body></html>')
        self.server = create_server(project=ROOT / "equilane.toml", reports_root=self.reports)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def get(self, path):
        with urlopen(self.base + path, timeout=10) as response:
            return response.read().decode("utf-8")

    def test_dashboard_has_complete_english_and_keeps_japanese_default(self):
        english = self.get("/?lang=en")
        self.assertIn('lang="en"', english)
        self.assertNotRegex(english, r"[ぁ-んァ-ヶ一-龠]")
        self.assertIn('lang="ja"', self.get("/"))

    def test_english_saved_report_is_available_without_an_english_file(self):
        original = (self.reports / "saved-run/showcase.html").read_bytes()
        english = self.get("/reports/saved-run/showcase.en.html")
        self.assertIn('lang="en"', english)
        self.assertNotRegex(english, r"[ぁ-んァ-ヶ一-龠]")
        self.assertEqual((self.reports / "saved-run/showcase.html").read_bytes(), original)
        self.assertFalse((self.reports / "saved-run/showcase.en.html").exists())
        self.assertIn("実行エラー", self.get("/reports/saved-run/showcase.html"))

    def test_missing_english_translation_never_claims_complete_english(self):
        (self.reports / "saved-run/showcase.html").write_text('<html lang="ja"><body>未登録翻訳検査語彙</body></html>')
        with self.assertRaises(HTTPError) as caught:
            self.get("/reports/saved-run/showcase.en.html")
        self.assertEqual(caught.exception.code, 422)
        message = json.loads(caught.exception.read())
        self.assertEqual(message["error"], "English translation is incomplete for this saved report")


if __name__ == "__main__":
    unittest.main()
