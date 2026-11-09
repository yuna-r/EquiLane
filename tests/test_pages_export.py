"""Public exports contain report presentation, never local execution evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
            self.assertEqual(files, {"index.html", "index.en.html", "summary.json", "deployment.json", ".nojekyll", "arm/index.html", "arm/index.en.html"})
            contents = "\n".join(path.read_text() for path in output.rglob("*") if path.is_file())
            for value in ("/Users/", "/home/runner", "/opt/equilane", "ghp_abcdefghijklmnopqrstuv", "private-token", "../../secrets", "arm-results.manifest.json"):
                self.assertNotIn(value, contents)
            self.assertIn("_mm_add_pd", contents)
            self.assertIn("document.querySelectorAll('.case-card')", contents)
            self.assertIn('href="#cases"', contents)
            english = (output / "index.en.html").read_text()
            self.assertIn('lang="en"', english)
            self.assertIn('href="arm/index.en.html"', english)
            self.assertNotRegex(english, r"[ぁ-んァ-ヶ一-龠]")

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


class PagesDifferenceExportTests(unittest.TestCase):
    _source = PagesExportTests._source

    def _record(self, index=0, *, role="arm", outcome="mismatch"):
        typed = {"element": "i32", "lanes": [f"0x{index:08x}", "0x00000001", "0x00000002", "0x00000003"]}
        identity = {"case_id": "sse2.add.i32x4.default", "environment": {"fp_mode": "ieee", "rounding": "nearest_even"},
                    "operands": {"a": typed, "b": typed}}
        input_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record = {**identity, "schema_version": 1, "sequence": index + 1,
                  "input_id": input_id, "generation": {"class": "structured"}}
        base = {"case_id": identity["case_id"], "schema_version": 1, "input_id": input_id, "duration_ns": 1}
        intel = {**base, "runner": "intel", "status": "ok", "observed": {"return": typed}}
        target = {**base, "runner": role, "status": "ok", "observed": {"return": typed}}
        if outcome == "not_comparable":
            for item in (intel, target):
                item.pop("observed")
                item["status"] = "unsupported"
                item["error"] = {"stage": "capability", "code": "missing-feature"}
            comparison = {"outcome": outcome, "mismatch_count": 0, "first_difference": None, "reason": "both_unsupported"}
        else:
            target["observed"] = {"return": {"element": "i32", "lanes": ["0xffffffff", *typed["lanes"][1:]]}}
            comparison = {"outcome": outcome, "mismatch_count": 1, "reason": "value_mismatch",
                          "first_difference": {"kind": "return", "lane": 0, "intel": typed["lanes"][0], role: "0xffffffff"}}
        return {"case_id": identity["case_id"], "input_id": input_id, "target_role": role,
                "input": record, "intel": intel, "target": target, "comparison": comparison}

    def _archive(self, source, rows, *, target="arm", metadata=None, payload=None):
        directory = source / f"canonical/compare-{target}"
        directory.mkdir(parents=True, exist_ok=True)
        page = directory / "showcase.html"
        if not page.exists():
            page.write_text('<html><title>EquiLane</title><body></body></html>')
        page.write_text(page.read_text().replace("</body>", '<a href="differences.jsonl" download>All differences</a></body>'))
        if payload is None:
            payload = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows)
        (directory / "differences.jsonl").write_bytes(payload)
        info = {"file": "differences.jsonl", "record_count": len(rows), "byte_length": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
        if metadata:
            info.update(metadata)
        summary = {"differences": info, "mismatched_inputs": sum(row["comparison"]["outcome"] == "mismatch" for row in rows),
                   "not_comparable_inputs": sum(row["comparison"]["outcome"] == "not_comparable" for row in rows)}
        (directory / "summary.json").write_text(json.dumps(summary))
        return info

    def _assert_rejected(self, source, destination):
        summary = pages.export_pages(source, destination)
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["unavailable_difference_archives"], ["arm"])
        self.assertNotIn('href="differences.jsonl"', (destination / "arm/index.html").read_text())
        self.assertIn('role="alert"', (destination / "arm/index.html").read_text())
        self.assertFalse((destination / "arm/differences.jsonl").exists())
        self.assertFalse((destination / "arm/.differences.jsonl.tmp").exists())
        self.assertIn("差分 JSONL を公開できなかった", (destination / "index.html").read_text())

    def test_verified_archive_preserves_every_mismatch_and_noncomparable_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            rows = [self._record(), self._record(1, outcome="not_comparable")]
            metadata = self._archive(source, rows)
            output = root / "public"
            summary = pages.export_pages(source, output)
            self.assertEqual(summary["status"], "findings")
            self.assertEqual(summary["difference_archives"], {"arm": metadata})
            self.assertEqual([json.loads(line) for line in (output / "arm/differences.jsonl").read_text().splitlines()], rows)
            self.assertIn('href="differences.jsonl"', (output / "arm/index.html").read_text())
            deployment = json.loads((output / "deployment.json").read_text())
            archive = next(item for item in deployment["files"] if item["file"] == "arm/differences.jsonl")
            self.assertEqual(archive["sha256"], metadata["sha256"])

    def test_public_html_allows_fixed_download_only_when_available(self):
        html = '<a href="differences.jsonl">ok</a><a href="./differences.jsonl">dot</a><a href="../differences.jsonl">up</a><a href="differences.jsonl?x=1">query</a><img src="differences.jsonl">image'
        for available in ((), ("differences.jsonl",)):
            with self.subTest(available=available):
                parser = pages.PublicHTML(available_files=available)
                parser.feed(html)
                output = "".join(parser.parts)
                self.assertEqual(output.count('href="differences.jsonl"'), int(bool(available)))
                self.assertNotIn('src="differences.jsonl"', output)
                self.assertNotIn('href="./', output)
                self.assertNotIn('href="../', output)
                self.assertNotIn('?x=', output)

    def test_large_archive_is_streamed_for_validation_and_deployment_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            rows = [self._record(index) for index in range(4096)]
            self._archive(source, rows)
            original_read_bytes, original_read_text = Path.read_bytes, Path.read_text
            def forbid_bulk_bytes(path):
                if path.name == "differences.jsonl":
                    raise AssertionError("JSONL must be read incrementally")
                return original_read_bytes(path)
            def forbid_bulk_text(path, *args, **kwargs):
                if path.name == "differences.jsonl":
                    raise AssertionError("JSONL must be read incrementally")
                return original_read_text(path, *args, **kwargs)
            with mock.patch.object(Path, "read_bytes", forbid_bulk_bytes), mock.patch.object(Path, "read_text", forbid_bulk_text):
                summary = pages.export_pages(source, root / "public")
            self.assertEqual(summary["difference_archives"]["arm"]["record_count"], 4096)
            self.assertGreater(summary["difference_archives"]["arm"]["byte_length"], 4 * 1024 * 1024)

    def test_closed_schema_rejects_nested_environment_paths_and_secret_fields(self):
        for location in ("root", "input", "environment", "generation", "typed", "intel", "observed", "comparison", "difference"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                row = self._record()
                node = {"root": row, "input": row["input"], "environment": row["input"]["environment"],
                        "generation": row["input"]["generation"], "typed": row["input"]["operands"]["a"],
                        "intel": row["intel"], "observed": row["intel"]["observed"],
                        "comparison": row["comparison"], "difference": row["comparison"]["first_difference"]}[location]
                node["private_extension"] = {"HOME": "/Users/alice", "secret": "private-token"}
                self._archive(source, [row])
                self._assert_rejected(source, root / "public")
                public = "\n".join(path.read_text() for path in (root / "public").rglob("*") if path.is_file())
                self.assertNotIn("private-token", public)
                self.assertNotIn("/Users/alice", public)

    def test_digest_count_length_and_fixed_path_are_checked(self):
        changes = ({"record_count": 2}, {"byte_length": 0}, {"sha256": "a" * 64},
                   {"file": "../../raw-manifest.json"}, {"record_count": True},
                   {"environment": {"HOME": "/Users/alice"}})
        for changed in changes:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                self._archive(source, [self._record()], metadata=changed)
                self._assert_rejected(source, root / "public")

    def test_record_ids_roles_bits_and_outcomes_are_checked(self):
        for defect in ("case", "input_id", "role", "bits", "match", "reason", "extra_error"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                row = self._record(outcome="not_comparable" if defect == "extra_error" else "mismatch")
                if defect == "case":
                    row["intel"]["case_id"] = "other.case"
                elif defect == "input_id":
                    row["input"]["operands"]["a"]["lanes"][0] = "0x000000ff"
                elif defect == "role":
                    row["target"]["runner"] = "intel"
                elif defect == "bits":
                    row["target"]["observed"]["return"]["lanes"][0] = "/Users/alice/private"
                elif defect == "match":
                    row["comparison"].update(outcome="match", mismatch_count=0, first_difference=None, reason=None)
                elif defect == "reason":
                    row["comparison"]["reason"] = "Bearer abcdefghijklmnopqrstuvwxyz"
                else:
                    row["target"]["error"]["message"] = "private-token"
                self._archive(source, [row])
                self._assert_rejected(source, root / "public")

    def test_unquoted_or_entity_encoded_claimed_download_requires_metadata(self):
        for href in ("differences.jsonl", '"differe&#110;ces.jsonl"'):
            with self.subTest(href=href), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                page = source / "canonical/compare-arm/showcase.html"
                page.write_text(page.read_text().replace("</body>", f'<a href={href}>Download</a></body>'))
                self._assert_rejected(source, root / "public")

    def test_archive_symlink_missing_metadata_and_missing_file_disable_link(self):
        for defect in ("symlink", "missing_metadata", "missing_archive"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                self._archive(source, [self._record()])
                directory = source / "canonical/compare-arm"
                if defect == "missing_metadata":
                    (directory / "summary.json").unlink()
                elif defect == "missing_archive":
                    (directory / "differences.jsonl").unlink()
                else:
                    external = root / "external.jsonl"
                    external.write_text((directory / "differences.jsonl").read_text())
                    (directory / "differences.jsonl").unlink()
                    (directory / "differences.jsonl").symlink_to(external)
                self._assert_rejected(source, root / "public")

    def test_incomplete_invalid_duplicate_key_and_oversized_lines_are_rejected(self):
        row = self._record()
        encoded = json.dumps(row, separators=(",", ":")).encode()
        for payload in (encoded, b"{invalid}\n", b"\xff\n", b'{"case_id":"x","case_id":"y"}\n', b"\n"):
            with self.subTest(payload=payload[:30]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self._source(root)
                self._archive(source, [row], payload=payload)
                self._assert_rejected(source, root / "public")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            self._archive(source, [row])
            with mock.patch.object(pages, "_MAX_LINE_BYTES", 32):
                self._assert_rejected(source, root / "public")

    def test_outcome_counts_must_match_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            self._archive(source, [self._record(), self._record(1, outcome="not_comparable")])
            summary_path = source / "canonical/compare-arm/summary.json"
            summary = json.loads(summary_path.read_text())
            summary["mismatched_inputs"] = 2
            summary_path.write_text(json.dumps(summary))
            self._assert_rejected(source, root / "public")

    def test_rvv_vlen256_uses_riscv_role_and_fixed_target_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            self._archive(source, [self._record(role="riscv")], target="riscv-vlen256")
            output = root / "public"
            summary = pages.export_pages(source, output)
            self.assertEqual(summary["difference_archives"]["riscv-vlen256"]["record_count"], 1)
            self.assertTrue((output / "riscv-vlen256/differences.jsonl").is_file())

    def test_valid_diagnostic_fields_remain_downloadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            row = self._record()
            row["comparison"]["first_difference"]["diagnostic"] = {
                "intel": {"classification": "normal", "decimal": "1.25e-7"},
                "arm": {"classification": "negative_zero"}, "ulp_distance": "123456789"}
            self._archive(source, [row])
            summary = pages.export_pages(source, root / "public")
            self.assertIn("arm", summary["difference_archives"])

    def test_empty_verified_archive_can_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            self._archive(source, [])
            summary = pages.export_pages(source, root / "public")
            self.assertEqual(summary["difference_archives"]["arm"]["record_count"], 0)
            self.assertEqual((root / "public/arm/differences.jsonl").stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
