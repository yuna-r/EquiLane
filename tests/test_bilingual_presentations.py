"""English views retain the Japanese default and the original evidence values."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equilane.cases import load_case_definitions
from equilane.i18n import CATALOG_PATH, MissingTranslationError, translate_html, translate_text, untranslated_phrases
from equilane.intrinsic_guide import intrinsic_for_case
from equilane.isa import load_isa_registry
from equilane.mismatch_report import render_mismatch_report, write_mismatch_report
from equilane.showcase import render_showcase_html, write_showcase_report
from equilane.webui import _DASHBOARD, render_dashboard
from test_mismatch_report import quality, observations


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []
        self.in_script = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a":
            self.links.append(values.get("href"))
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, value):
        if self.in_script:
            self.scripts[-1] += value


def _structure(html):
    parser = _Markup()
    parser.feed(html)
    return parser


class BilingualPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        isa = load_isa_registry(ROOT / "contracts/isa-registry.json")
        registry = load_case_definitions(ROOT / "10_official_suite/cases", isa_registry=isa)
        cls.cases = tuple(registry)
        cls.summary = {"record_count": len(cls.cases), "matched_inputs": len(cls.cases), "mismatched_inputs": 0,
                       "not_comparable_inputs": 0, "mismatch_atoms": 0, "outcome": "pass"}
        cls.arguments = {"cases": cls.cases, "summary": cls.summary, "profile": "smoke", "seed": "0x0123456789abcdef",
                         "vector_sha256": "a" * 64, "case_definitions_sha256": "b" * 64,
                         "isa_contract_sha256": "c" * 64, "generated_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
                         "native_evidence": False}

    def test_language_enum_and_unknown_phrases_fail_explicitly(self):
        for language in ("fr", "", None, True):
            with self.subTest(language=language), self.assertRaises(ValueError):
                translate_html("<html></html>", language=language)
        with self.assertRaises(MissingTranslationError):
            translate_text("新規未翻訳用語", strict=True)
        self.assertEqual(translate_html("<html>日本語</html>", language="ja", strict=True), "<html>日本語</html>")

    def test_longest_phrase_translation_preserves_values_and_interpolations(self):
        self.assertEqual(translate_text("対応する要素を加算する。", strict=True), "Add corresponding elements.")
        text = "bのレーン2：0xfff8000000000042をf64からf32へ数値変換。"
        self.assertEqual(translate_text(text, strict=True), "Convert b lane 2 from f64 to f32: 0xfff8000000000042. ")
        self.assertEqual(translate_text("sse2.add.f64x2.default 0x7ff0000000000000 123456"),
                         "sse2.add.f64x2.default 0x7ff0000000000000 123456")

    def test_english_studio_is_complete_and_retains_controls(self):
        japanese, english = render_dashboard(), render_dashboard(language="en")
        self.assertIn('lang="ja"', japanese)
        self.assertIn("レポートを作る", japanese)
        self.assertIn('lang="en"', english)
        self.assertIn("Create report", english)
        self.assertIn("Send the same input to Intel and the selected targets.", english)
        self.assertEqual(untranslated_phrases(english), ())
        self.assertEqual(_structure(japanese).ids, _structure(english).ids)
        self.assertIn("?lang=ja", _structure(english).links)
        self.assertIn("?lang=en", _structure(japanese).links)
        self.assertIn("JSON.stringify(payload)", english)
        self.assertIn("textContent = text", english, "raw process output must remain intact")

    def test_english_showcase_keeps_hashes_counts_and_all_case_ids(self):
        before = copy.deepcopy(self.arguments)
        japanese = render_showcase_html(**self.arguments)
        english = render_showcase_html(**self.arguments, language="en")
        self.assertEqual(untranslated_phrases(english), ())
        self.assertIn('lang="en"', english)
        self.assertEqual(_structure(japanese).ids, _structure(english).ids)
        for case in self.cases:
            self.assertIn(case.id, english)
        for digest in ("a" * 64, "b" * 64, "c" * 64):
            self.assertIn(digest, english)
        self.assertEqual(self.arguments, before)

    def test_native_english_view_retains_port_scope_and_actual_counts(self):
        arguments = {**self.arguments, "native_evidence": True}
        html = render_showcase_html(**arguments, language="en")
        self.assertEqual(untranslated_phrases(html), ())
        self.assertIn("against the Intel reference case contract", html)
        self.assertIn("does not mean that all intrinsics have been implemented", html)
        self.assertIn(str(len(self.cases)), html)

    def test_guide_scope_is_explicit_in_both_languages(self):
        japanese = render_showcase_html(**self.arguments)
        english = render_showcase_html(**self.arguments, language="en")
        self.assertIn("全 intrinsics の対応完了を意味しません", japanese)
        self.assertIn("This does not mean that all intrinsics have been implemented", english)
        self.assertIn("7,126", english)
        self.assertIn("documentation/intrinsic-coverage.json", english)

    def test_all_registered_intrinsic_summaries_have_an_english_fallback(self):
        catalog = json.loads((ROOT / "src/equilane/data/intel-intrinsics.json").read_text())["intrinsics"]
        self.assertTrue(catalog)
        for case_id in catalog:
            with self.subTest(case=case_id):
                metadata = intrinsic_for_case(case_id)
                original = metadata["summary_ja"]
                translated = translate_text(original, strict=True)
                self.assertTrue(translated)
                self.assertEqual(untranslated_phrases(translated), ())
                self.assertEqual(intrinsic_for_case(case_id)["summary_ja"], original)

    def test_showcase_writer_emits_linked_english_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.html"
            returned = write_showcase_report(path, **self.arguments)
            english = path.with_name("report.en.html")
            self.assertEqual(returned, path)
            self.assertTrue(path.is_file())
            self.assertTrue(english.is_file())
            for page in (path, english):
                self.assertIn('href="report.html"', page.read_text())
                self.assertIn('href="report.en.html"', page.read_text())
            self.assertEqual(untranslated_phrases(english.read_text()), ())

    def test_mismatch_report_english_preserves_evidence_and_scope(self):
        value, snapshot = quality(), observations()
        before = copy.deepcopy((value, snapshot))
        english = render_mismatch_report(value, observations=snapshot, language="en")
        self.assertEqual(untranslated_phrases(english), ())
        self.assertIn("0xfff8000000000000", english)
        self.assertIn("0x7ff8000000000000", english)
        self.assertIn("official_nearest_even", english)
        self.assertEqual((value, snapshot), before)

    def test_mismatch_writer_emits_linked_english_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "details.html"
            write_mismatch_report(path, summary=quality(), observations=observations())
            english = path.with_name("details.en.html")
            self.assertTrue(english.is_file())
            self.assertIn('href="details.html"', english.read_text())
            self.assertIn('href="details.en.html"', path.read_text())
            self.assertEqual(untranslated_phrases(english.read_text()), ())

    def test_presentation_source_text_has_no_untranslated_japanese(self):
        for relative in ("src/equilane/showcase.py", "src/equilane/webui.py", "src/equilane/mismatch_details.py",
                         "src/equilane/mismatch_report.py", "src/equilane/operation_explanation.py", "src/equilane/mascot.py",
                         "docker/export_pages.py"):
            with self.subTest(source=relative):
                self.assertEqual(untranslated_phrases(translate_text((ROOT / relative).read_text(), strict=True)), ())

    def test_snapshot_labels_translate_without_modifying_the_fixture(self):
        path = ROOT / "tests/data/mismatch-report-2026-10-16.json"
        before = path.read_bytes()
        original = json.loads(before)
        strings = []
        def walk(value):
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(original)
        for text in strings:
            with self.subTest(value=text[:50]):
                self.assertEqual(untranslated_phrases(translate_text(text, strict=True)), ())
        self.assertEqual(path.read_bytes(), before)

    def test_preview_xml_text_has_english_translation(self):
        root = ET.parse(ROOT / "assets/showcase-report-preview.svg").getroot()
        for node in root.iter():
            for text in (node.text or "", node.tail or "", *node.attrib.values()):
                if not text.startswith("data:image/"):
                    with self.subTest(value=text[:50]):
                        self.assertEqual(untranslated_phrases(translate_text(text, strict=True)), ())

    def test_catalog_is_packaged_json_with_no_japanese_targets(self):
        value = json.loads(CATALOG_PATH.read_text())
        self.assertEqual(value["source_language"], "ja")
        self.assertEqual(value["target_language"], "en")
        for target in value["phrases"].values():
            self.assertEqual(untranslated_phrases(target), ())

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to parse presentation scripts")
    def test_english_and_japanese_inline_javascript_stays_valid(self):
        for html in (render_dashboard(), render_dashboard(language="en"),
                     render_showcase_html(**self.arguments, language="en"),
                     render_mismatch_report(quality(), observations=observations(), language="en")):
            for script in _structure(html).scripts:
                completed = subprocess.run([shutil.which("node"), "-e",
                    "const vm=require('node:vm');new vm.Script(require('node:fs').readFileSync(0,'utf8'));"],
                    input=script, text=True, capture_output=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
