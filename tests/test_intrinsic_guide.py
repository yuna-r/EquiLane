"""Pinned official API facts and safe, model-free packaged Guide lookups."""

from __future__ import annotations

import copy
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import sys
import tomllib
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dump_bytes
from equilane.cases import load_case_definitions
from equilane.intrinsic_guide import GUIDE_URL, _catalog, _parse_catalog, catalog_provenance, intrinsic_for_case
from equilane.isa import load_isa_registry


class IntrinsicGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases", isa_registry=cls.isa)
        cls.catalog_text = files("equilane").joinpath("data", "intel-intrinsics.json").read_text(encoding="utf-8")
        cls.catalog = json.loads(cls.catalog_text)

    def test_all_254_official_case_symbols_have_exactly_their_recorded_api(self):
        self.assertEqual(len(self.cases), 254)
        self.assertEqual(set(self.catalog["intrinsics"]), set(self.cases.ids))
        for case in self.cases:
            with self.subTest(case=case.id):
                entry = intrinsic_for_case(case.id)
                self.assertIsNotNone(entry)
                self.assertEqual(entry["intel_symbol"], case.data["intel"]["symbol"])
                self.assertEqual(entry["name"], case.data["intel"]["symbol"].removeprefix("intel"))
                self.assertEqual(entry["framework_required_isa"], list(case.required_isa("intel")))
                self.assertEqual(entry["guide_url"], GUIDE_URL + "#text=" + entry["name"])
                self.assertIn(entry["name"] + "(", entry["prototype"])
                self.assertTrue(entry["summary_ja"])

    def test_live_case_binding_digest_detects_changed_names_or_required_isa(self):
        bindings = [{"case_id": case.id, "intel_symbol": case.data["intel"]["symbol"],
                     "framework_required_isa": list(case.required_isa("intel"))} for case in self.cases]
        digest = hashlib.sha256(dump_bytes(bindings)).hexdigest()
        self.assertEqual(catalog_provenance()["framework_case_bindings_sha256"], digest)
        modified = copy.deepcopy(bindings)
        modified[0]["intel_symbol"] = "intel_mm_sub_ps"
        self.assertNotEqual(hashlib.sha256(dump_bytes(modified)).hexdigest(), digest)
        modified = copy.deepcopy(bindings)
        modified[0]["framework_required_isa"] = ["sse"]
        self.assertNotEqual(hashlib.sha256(dump_bytes(modified)).hexdigest(), digest)

    def test_guide_source_version_and_archive_are_pinned(self):
        source = catalog_provenance()
        self.assertEqual((source["version"], source["date"]), ("3.6.9", "07/12/2024"))
        self.assertEqual(source["framework_case_count"], 254)
        self.assertEqual(source["offline_archive_sha256"], "4de3a0c59a944f153f6dc47cfb2d0e455ef16e54a8878f8882db82237947f5e7")
        self.assertEqual(source["data_js_sha256"], "67307df98241728ff14ea2ad4102a3316b4674d7e4629a7293e39952eec59d69")
        self.assertEqual(source["decoded_xml_sha256"], "6762a50652a35ef663fd532ed83b789d19bbc2ac7e94b1771f19d666d96c2c30")

    def test_sse_add_ps_uses_actual_guide_isa_separate_from_framework_sse2(self):
        entry = intrinsic_for_case("sse2.add.f32x4.default")
        self.assertEqual(entry["name"], "_mm_add_ps")
        self.assertEqual(entry["isa"], "SSE")
        self.assertEqual(entry["framework_required_isa"], ["sse2"])
        self.assertEqual(entry["header"], "xmmintrin.h")
        self.assertEqual(entry["instructions"], ["ADDPS"])
        self.assertEqual(entry["prototype"], "__m128 _mm_add_ps(__m128 a, __m128 b);")

    def test_sse2_add_pd_has_the_double_precision_prototype_and_instruction(self):
        entry = intrinsic_for_case("sse2.add.f64x2.default")
        self.assertEqual(entry["isa"], "SSE2")
        self.assertEqual(entry["header"], "emmintrin.h")
        self.assertEqual(entry["prototype"], "__m128d _mm_add_pd(__m128d a, __m128d b);")
        self.assertEqual(entry["instructions"], ["ADDPD"])

    def test_scalar_double_sqrt_preserves_guide_two_argument_api_and_reduced_binding(self):
        entry = intrinsic_for_case("sse2.sqrt.f64x2.scalar")
        self.assertEqual(entry["name"], "_mm_sqrt_sd")
        self.assertEqual(entry["prototype"], "__m128d _mm_sqrt_sd(__m128d a, __m128d b);")
        self.assertEqual(entry["case_call"], "_mm_sqrt_sd(a, a)")
        self.assertIn("部分集合", entry["binding_note"])
        self.assertEqual(len(self.cases.get("sse2.sqrt.f64x2.scalar").signature["arguments"]), 1)

    def test_set_api_without_a_fixed_instruction_does_not_invent_a_mnemonic(self):
        entry = intrinsic_for_case("sse2.set.f32x4.high-low")
        self.assertEqual(entry["name"], "_mm_set_ps")
        self.assertEqual(entry["instructions"], [])
        self.assertEqual(entry["case_call"], "_mm_set_ps(lane3, lane2, lane1, lane0)")

    def test_low_to_high_constructor_retains_argument_order(self):
        entry = intrinsic_for_case("sse2.setr.f32x4.low-high")
        self.assertEqual(entry["case_call"], "_mm_setr_ps(lane0, lane1, lane2, lane3)")
        self.assertIn("下位から上位", entry["summary_ja"])

    def test_packed_and_scalar_conversions_are_different_apis(self):
        packed = intrinsic_for_case("sse2.cvt.f64x2.f32x4")
        scalar = intrinsic_for_case("sse2.cvt.f64x2.f32x4.scalar")
        self.assertEqual(packed["name"], "_mm_cvtpd_ps")
        self.assertEqual(packed["case_call"], "_mm_cvtpd_ps(a)")
        self.assertEqual(scalar["name"], "_mm_cvtsd_ss")
        self.assertEqual(scalar["case_call"], "_mm_cvtsd_ss(a, b)")
        self.assertNotEqual(packed["prototype"], scalar["prototype"])

    def test_move_integer_and_float_are_not_given_the_same_semantics(self):
        self.assertIn("上位64ビットを0", intrinsic_for_case("sse2.move.i64x2.default")["summary_ja"])
        self.assertIn("第二入力", intrinsic_for_case("sse2.move.f64x2.default")["summary_ja"])

    def test_unknown_or_invalid_ids_are_not_inferred_from_names(self):
        for case_id in (None, [], {}, True, 42, "", "_mm_add_ps", "sse2.unknown.f32x4.default", "sse2.add.f32x4.future"):
            with self.subTest(case=case_id):
                self.assertIsNone(intrinsic_for_case(case_id))

    def test_returned_rows_and_provenance_cannot_mutate_the_cached_catalog(self):
        entry = intrinsic_for_case("sse2.add.f32x4.default")
        entry["instructions"].clear()
        entry["name"] = "changed"
        source = catalog_provenance()
        source["version"] = "changed"
        self.assertEqual(intrinsic_for_case("sse2.add.f32x4.default")["instructions"], ["ADDPS"])
        self.assertEqual(intrinsic_for_case("sse2.add.f32x4.default")["name"], "_mm_add_ps")
        self.assertEqual(catalog_provenance()["version"], "3.6.9")

    def test_lookup_does_not_execute_models_fixtures_or_probes(self):
        with mock.patch("equilane.development.load_development_case", side_effect=AssertionError("no model execution")), \
             mock.patch("equilane.fixture.run_fixture", side_effect=AssertionError("no fixture execution")), \
             mock.patch("subprocess.run", side_effect=AssertionError("no process launch")):
            self.assertEqual(intrinsic_for_case("sse2.add.f64x2.default")["name"], "_mm_add_pd")

    def test_catalog_facts_are_packaged_and_omit_large_copyright_text(self):
        package_data = tomllib.loads((PROJECT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["equilane"]
        self.assertIn("data/*.json", package_data)
        self.assertEqual(set(self.catalog), {"schema_version", "provenance", "intrinsics"})
        for entry in self.catalog["intrinsics"].values():
            self.assertNotIn("description", entry)
            self.assertNotIn("operation", entry)
            self.assertLess(len(entry["summary_ja"]), 180)
        self.assertNotIn("/Users/", self.catalog_text)

    def test_malformed_catalog_data_fails_closed_without_exceptions(self):
        for text in ("", "not json", "[]", "null", '{"schema_version":true}',
                     '{"schema_version":1,"provenance":{},"intrinsics":[]}',
                     '{"schema_version":1,"schema_version":1,"provenance":{},"intrinsics":{}}'):
            with self.subTest(text=text):
                self.assertEqual(_parse_catalog(text), {})

    def test_malformed_binding_and_foreign_guide_url_fail_closed(self):
        original = {"schema_version": 1, "provenance": {}, "intrinsics": {
            "sse2.add.f32x4.default": intrinsic_for_case("sse2.add.f32x4.default")}}
        for field, value in (("name", []), ("name", "_not_mm_add_ps"), ("guide_url", "https://evil.test/"),
                             ("intel_symbol", "intel_mm_sub_ps"), ("prototype", None),
                             ("instructions", [None]), ("framework_required_isa", []),
                             ("summary_ja", ""), ("binding_note", [])):
            with self.subTest(field=field, value=value):
                candidate = copy.deepcopy(original)
                candidate["intrinsics"]["sse2.add.f32x4.default"][field] = value
                self.assertEqual(_parse_catalog(json.dumps(candidate)), {})

    def test_missing_packaged_resource_is_a_safe_unknown_lookup(self):
        self.addCleanup(_catalog.cache_clear)
        _catalog.cache_clear()
        with mock.patch("equilane.intrinsic_guide.files", side_effect=FileNotFoundError("missing package data")):
            self.assertIsNone(intrinsic_for_case("sse2.add.f32x4.default"))
            self.assertEqual(catalog_provenance(), {})
        _catalog.cache_clear()

    def test_invalid_utf8_resource_is_a_safe_unknown_lookup(self):
        self.addCleanup(_catalog.cache_clear)
        _catalog.cache_clear()
        resource = mock.Mock()
        resource.joinpath.return_value.read_text.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        with mock.patch("equilane.intrinsic_guide.files", return_value=resource):
            self.assertIsNone(intrinsic_for_case("sse2.add.f32x4.default"))
        _catalog.cache_clear()


if __name__ == "__main__":
    unittest.main()
