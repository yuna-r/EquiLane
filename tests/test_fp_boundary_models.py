"""Unfiltered model assertions against independently captured SSE2 boundaries.

The fixed corpus comes from tests/native/probe_fp_boundary.c, executed as an
x86_64 program under Rosetta. No expected output is derived from Python float,
EquiLane's comparison engine or either portable fixture. Mismatches deliberately
remain ordinary failures with complete input/expected/actual evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_test_support import PROJECT, VerifiedModelMixin  # noqa: E402


REFERENCE = "tests/data/fp-boundary-oracles.json"
FAMILIES = (
    "fp_boundary_comi", "fp_boundary_compare", "fp_boundary_float_to_integer",
    "fp_boundary_halfway_arithmetic", "fp_boundary_integer_to_float",
    "fp_boundary_minmax", "fp_boundary_nan_priority", "fp_boundary_narrowing",
    "fp_boundary_signed_zero_infinity", "fp_boundary_sqrt",
    "fp_boundary_subnormal_arithmetic", "fp_boundary_widening",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_key(row):
    return canonical({key: row[key] for key in ("case_id", "rounding", "operands")})


class FPBoundaryModelTests(VerifiedModelMixin, unittest.TestCase):
    verification_subject = "portable_model_oracle"
    oracle_reference = REFERENCE
    oracle_method = "captured_sse2_expected_bits"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corpus = json.loads((PROJECT / REFERENCE).read_text())
        cls.oracle_reference_metadata = {
            key: value for key, value in cls.corpus.items() if key != "rows"
        }

    def test_captured_sources_and_row_digest_are_pinned(self):
        for relative, expected in self.corpus["source_sha256"].items():
            with self.subTest(source=relative):
                self.assertEqual(hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest(), expected,
                                 "Rebuild and independently recapture/verify changed SSE2 oracle sources")
        expected = hashlib.sha256(canonical(self.corpus["rows"]).encode()).hexdigest()
        self.assertEqual(self.corpus["rows_sha256"], expected)
        self.assertEqual(self.corpus["row_count"], len(self.corpus["rows"]))
        self.assertFalse(self.corpus["native_cross_host_evidence"])
        self.assertIn("Rosetta", self.corpus["execution_context"])

    def test_corpus_is_deduplicated_and_expands_the_previous_oracle(self):
        rows = self.corpus["rows"]
        keys = [input_key(row) for row in rows]
        self.assertEqual(len(keys), len(set(keys)), "do not inflate oracle checks with duplicate inputs")
        old_rows = json.loads((PROJECT / "tests/data/rounding-oracles.json").read_text())["rows"]
        self.assertGreaterEqual(len(set(keys) - {input_key(row) for row in old_rows}), 1000)
        self.assertEqual({row["family"] for row in rows}, set(FAMILIES))
        self.assertEqual({row["rounding"] for row in rows},
                         {"nearest_even", "toward_negative", "toward_positive", "toward_zero"})
        for row in rows:
            expected = hashlib.sha256(input_key(row).encode()).hexdigest()
            self.assertEqual(row["row_id"], expected)

    def check_family(self, family):
        rows = [row for row in self.corpus["rows"] if row["family"] == family]
        self.assertTrue(rows, f"the independent corpus must exercise {family}")
        for row in rows:
            with self.subTest(case_id=row["case_id"], rounding=row["rounding"],
                              input=row["label"], row_id=row["row_id"][:16]):
                self.assert_model(
                    row["case_id"], row["operands"], observed=row["expected"],
                    family=family, rounding=row["rounding"],
                    extended=row["rounding"] != "nearest_even",
                    oracle_reference=REFERENCE, oracle_method="captured_sse2_expected_bits",
                )

    def test_subnormal_normal_transition_arithmetic(self):
        self.check_family("fp_boundary_subnormal_arithmetic")

    def test_halfway_and_nextafter_neighbors_in_arithmetic(self):
        self.check_family("fp_boundary_halfway_arithmetic")

    def test_signed_zero_infinities_and_exact_cancellation(self):
        self.check_family("fp_boundary_signed_zero_infinity")

    def test_nan_payload_order_priority_and_inactive_scalar_poison(self):
        self.check_family("fp_boundary_nan_priority")

    def test_sqrt_subnormal_neighbor_nan_and_poison_lanes(self):
        self.check_family("fp_boundary_sqrt")

    def test_minmax_second_operand_selection_and_mixed_lanes(self):
        self.check_family("fp_boundary_minmax")

    def test_all_twelve_ordered_unordered_vector_predicates(self):
        self.check_family("fp_boundary_compare")

    def test_comi_return_predicates_with_nan_and_inactive_poison_lanes(self):
        self.check_family("fp_boundary_comi")

    def test_float_to_integer_limits_ties_rounding_and_truncation(self):
        self.check_family("fp_boundary_float_to_integer")

    def test_narrowing_nextafter_neighbors_underflow_overflow_and_nan_payloads(self):
        self.check_family("fp_boundary_narrowing")

    def test_widening_subnormals_nan_payloads_and_unused_lanes(self):
        self.check_family("fp_boundary_widening")

    def test_integer_to_float_precision_boundaries_and_scalar_lane_preservation(self):
        self.check_family("fp_boundary_integer_to_float")


if __name__ == "__main__":
    unittest.main()
