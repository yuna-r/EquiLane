"""Public exports contain report presentation, never local execution evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pages_export", ROOT / "docker/export_pages.py")
pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pages)


class PagesExportTests(unittest.TestCase):
    def _source(self, root):
        source = root / "private"
        (source / "canonical/compare-arm").mkdir(parents=True)
        (source / "pipeline-summary.json").write_text(json.dumps({
            "artifact_type": "equilane.docker-pipeline", "status": "findings",
            "created_at": "2026-11-10T00:00:00+00:00",
            "launcher_errors": [{"error": "/Users/alice/private ghp_abcdefghijklmnopqrstuv"}],
            "wrapper_comparisons": [{"target": "arm", "status": "mismatch", "samples": 762,
                                     "matched": 738, "mismatch_count": 24,
                                     "observed_manifest": {"environment": {"HOME": "/Users/alice", "token": "private-token"}}}],
            "canonical_comparisons": [{"target": "arm", "outcome": "mismatch", "record_count": 2032,
                                       "matched_inputs": 2000, "mismatched_inputs": 32, "not_comparable_inputs": 0,
                                       "failure_bundles": ["../../secrets.json"]}],
            "quality": {"status": "findings", "tests_run": 1400, "portable_model_mismatches": 2159,
                        "gates": {"private-log": "/home/runner/private.log"}},
        }))
        (source / "canonical/compare-arm/showcase.html").write_text('''<!doctype html><html><head><title>EquiLane · mismatch</title></head><body>
<a href="#cases">Cases</a><a href="file:///Users/alice/private">private</a><a href="../../native/arm/arm-results.manifest.json">manifest</a>
<a href="https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_pd">_mm_add_pd</a>
<p>/Users/alice/project/source.c /home/runner/work/secret /opt/equilane/build/test.c ghp_abcdefghijklmnopqrstuv</p>
<script>document.querySelectorAll('.case-card');</script></body></html>''')
        (source / "raw-manifest.json").write_text('{"password":"private-token"}')
        return source

    def test_export_allowlists_files_and_removes_local_paths_and_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            output = root / "public"
            summary = pages.export_pages(source, output, commit="a" * 40, repository="yuna-r/EquiLane", run_id="123")
            self.assertEqual(summary["status"], "findings")
            self.assertEqual(summary["detail_pages"], ["arm"])
            files = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
            self.assertEqual(files, {"index.html", "summary.json", "deployment.json", ".nojekyll", "arm/index.html"})
            contents = "\n".join(path.read_text() for path in output.rglob("*") if path.is_file())
            for value in ("/Users/", "/home/runner", "/opt/equilane", "ghp_abcdefghijklmnopqrstuv", "private-token", "../../secrets", "arm-results.manifest.json"):
                self.assertNotIn(value, contents)
            self.assertIn("_mm_add_pd", contents)
            self.assertIn("document.querySelectorAll('.case-card')", contents)
            self.assertIn('href="#cases"', contents)

    def test_export_ignores_report_controlled_file_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            secret = root / "outside.html"
            secret.write_text("<title>EquiLane</title><p>outside-secret</p></html>")
            page = source / "canonical/compare-arm/showcase.html"
            page.unlink()
            page.symlink_to(secret)
            output = root / "public"
            summary = pages.export_pages(source, output)
            self.assertEqual(summary["detail_pages"], [])
            self.assertFalse((output / "arm").exists())
            self.assertNotIn("outside-secret", (output / "index.html").read_text())

    def test_missing_or_invalid_report_exports_an_honest_error_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "private"
            source.mkdir()
            output = root / "public"
            summary = pages.export_pages(source, output)
            self.assertEqual(summary["status"], "error")
            self.assertIn("工程未完了", (output / "index.html").read_text())

    def test_untrusted_status_and_metadata_cannot_inject_html(self):
        summary = pages.public_summary({"status": {"bad": "<script>"},
                                       "wrapper_comparisons": [{"target": "arm", "status": ["<script>"], "samples": True}]})
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["wrapper_comparisons"], [{"status": "error", "target": "arm"}])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            output = root / "public"
            pages.export_pages(source, output, repository='x/<script>alert(1)</script>', commit="/Users/alice", run_id="bad")
            contents = (output / "summary.json").read_text()
            self.assertNotIn("<script>", contents)
            self.assertNotIn("/Users/", contents)

    def test_preexisting_destination_is_rejected_instead_of_publishing_stale_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            output = root / "public"
            output.mkdir()
            (output / "private.log").write_text("must-not-publish")
            with self.assertRaisesRegex(ValueError, "empty"):
                pages.export_pages(source, output)

    def test_destination_cannot_contain_its_private_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            with self.assertRaisesRegex(ValueError, "private input"):
                pages.export_pages(source, root)


if __name__ == "__main__":
    unittest.main()
