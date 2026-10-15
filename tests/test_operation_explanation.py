from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.operation_explanation import explain_operation


def vector(element, *lanes):
    return {"element": element, "lanes": list(lanes)}


class OperationExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = json.loads((PROJECT / "tests/data/mismatch-report-2026-10-16.json").read_text())
        cls.examples = {row["report_example_id"]: row for row in snapshot["representative_findings"]}

    def finding(self, case, operands, expected, actual, rounding="toward_negative"):
        return {"input": {"case_id": case, "operands": operands, "environment": {"rounding": rounding}},
                "expected": {"return": expected}, "actual": {"return": actual}}

    def test_all_saved_representative_operations_have_readable_steps_without_mutating_evidence(self):
        for name, row in self.examples.items():
            with self.subTest(example=name):
                before = copy.deepcopy(row)
                text = explain_operation(row)
                self.assertTrue(text["title"])
                self.assertTrue(text["description"])
                self.assertTrue(text["steps"])
                self.assertEqual(row, before)

    def test_float_to_integer_shows_one_point_five_expected_one_actual_two(self):
        row = self.finding("sse2.cvt.f32x4.i32.scalar",
            {"a": vector("f32", "0x3fc00000", "0x7fc00042", "0x80000000", "0x00000001")},
            {"element": "i32", "bits": "0x00000001"}, {"element": "i32", "bits": "0x00000002"})
        text = " ".join(explain_operation(row)["steps"])
        for expected in ("1.5", "下方向", "期待値：1", "実測値：2"):
            self.assertIn(expected, text)
        self.assertNotIn("ビット列を保ち", text, "scalar integer return has no preserved upper vector")

    def test_tiny_addend_is_explained_as_a_power_of_two(self):
        row = self.finding("sse2.add.f32x4.default",
            {"a": vector("f32", "0x3f800000"), "b": vector("f32", "0x33000000")},
            vector("f32", "0x3f800001"), vector("f32", "0x3f800000"), "toward_positive")
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("2^-25", text)
        self.assertIn("上方向", text)
        self.assertIn("1.0000001192092896", text)

    def test_scalar_arithmetic_explains_one_operation_and_inactive_lanes(self):
        row = self.finding("sse2.add.f32x4.scalar", {
            "a": vector("f32", "0x3f800000", "0x7fc00042", "0x80000000", "0x00000001"),
            "b": vector("f32", "0x40000000", "0x7fc00043", "0x3f800000", "0x3f800000")},
            vector("f32", "0x40400000", "0x7fc00042", "0x80000000", "0x00000001"),
            vector("f32", "0x40400000", "0x7fc00042", "0x80000000", "0x00000001"))
        text = explain_operation(row)
        self.assertEqual(sum("保存した期待値" in step for step in text["steps"]), 1)
        self.assertIn("レーン1〜3", " ".join(text["steps"]))

    def test_scalar_narrowing_reads_b_and_preserves_a_upper_lanes(self):
        row = self.finding("sse2.cvt.f64x2.f32x4.scalar", {
            "a": vector("f32", "0x7fc00042", "0x80000000", "0x7f800001", "0x00000001"),
            "b": vector("f64", "0x3ff8000000000000", "0x7ff8000000000001")},
            vector("f32", "0x3fc00000", "0x80000000", "0x7f800001", "0x00000001"),
            vector("f32", "0x3fc00000", "0x80000000", "0x7f800001", "0x00000001"))
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("bのレーン0：1.5", text)
        self.assertIn("上位レーン1〜3はa", text)

    def test_packed_narrowing_explains_zero_fill_after_two_conversions(self):
        row = self.finding("sse2.cvt.f64x2.f32x4", {"a": vector("f64", "0x3ff0000000000000", "0xbff0000000000000")},
            vector("f32", "0x3f800000", "0xbf800000", "0x00000000", "0x00000000"),
            vector("f32", "0x3f800000", "0xbf800000", "0x00000000", "0x00000000"))
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("変換した2レーンより上", text)
        self.assertIn("0で埋め", text)

    def test_scalar_sqrt_exposes_the_two_argument_guide_adapter(self):
        row = self.finding("sse2.sqrt.f64x2.scalar", {"a": vector("f64", "0x4000000000000000", "0x7ff8000000000001")},
            vector("f64", "0x3ff6a09e667f3bcd", "0x7ff8000000000001"),
            vector("f64", "0x3ff6a09e667f3bcd", "0x7ff8000000000001"))
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("√(2.0", text)
        self.assertIn("_mm_sqrt_sd(a, a)", text)
        self.assertIn("レーン1〜1", text)

    def test_truncation_is_independent_of_the_requested_rounding_direction(self):
        row = self.finding("sse2.cvtt.f32x4.i32.scalar", {"a": vector("f32", "0xbfc00000")},
            {"element": "i32", "bits": "0xffffffff"}, {"element": "i32", "bits": "0xffffffff"},
            "toward_positive")
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("ゼロ方向", text)
        self.assertIn("丸め指定とは独立", text)
        self.assertIn("期待値：-1", text)

    def test_nan_and_signed_zero_values_are_not_lost_in_numeric_labels(self):
        row = self.finding("sse2.add.f64x2.default", {
            "a": vector("f64", "0x8000000000000000", "0x7ff8000000000042"),
            "b": vector("f64", "0x0000000000000000", "0x7ff0000000000001")},
            vector("f64", "0x8000000000000000", "0x7ff8000000000042"),
            vector("f64", "0x0000000000000000", "0x7ff8000000000001"))
        text = " ".join(explain_operation(row)["steps"])
        self.assertIn("−0", text)
        self.assertIn("+0", text)
        self.assertIn("payload=0x42", text)
        self.assertIn("signaling NaN", text)

    def test_unknown_and_malformed_records_do_not_invent_a_computation(self):
        for value in (None, {}, {"input": {"case_id": []}}, {"input": {"case_id": "sse2.shuffle.i32x4.imm8"}}):
            with self.subTest(value=value):
                self.assertEqual(explain_operation(value), {})


if __name__ == "__main__":
    unittest.main()
