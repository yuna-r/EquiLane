"""Input-space checks independent of portable or native result computation."""

from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
import io
from itertools import islice, product
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.adversarial import (  # noqa: E402
    adversarial_candidates,
    adversarial_case_count,
    boundary_bits,
    build_adversarial_coverage,
    structured_scenarios,
)
from equilane.artifacts import validate_input_artifact  # noqa: E402
from equilane.canonical import read_canonical_json  # noqa: E402
from equilane.cases import CaseRegistry, load_case_definitions  # noqa: E402
from equilane.generator import DEFAULT_SEED, PROFILE_COUNTS, _all_records, generate_artifact  # noqa: E402
from equilane.isa import load_isa_registry  # noqa: E402
from equilane.records import derive_input_id, validate_input_record  # noqa: E402


def materialize(case, candidate, sequence=1):
    record = {**candidate, "case_id": case.id, "schema_version": 1, "sequence": sequence}
    record["input_id"] = derive_input_id(record)
    return record


class AdversarialInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases", isa_registry=cls.isa)

    def case(self, case_id):
        return self.cases.get(case_id)

    def test_literal_float_boundaries_cover_both_signs_and_nan_payloads(self):
        required = {
            "f32": {0x00000000, 0x80000000, 0x00000001, 0x80000001,
                    0x007FFFFF, 0x00800000, 0x7F7FFFFF, 0x7F800000,
                    0xFF800000, 0x7FC00001, 0xFFC00042, 0x7F800001,
                    0xFF800042, 0x3F7FFFFF, 0x3F800000, 0x3F800001,
                    0x3EFFFFFF, 0x3F000000, 0x3F000001, 0x4EFFFFFF,
                    0x4F000000, 0x4F000001, 0x5F000000},
            "f64": {0x0000000000000000, 0x8000000000000000,
                    0x0000000000000001, 0x8000000000000001,
                    0x000FFFFFFFFFFFFF, 0x0010000000000000,
                    0x7FEFFFFFFFFFFFFF, 0x7FF0000000000000,
                    0xFFF0000000000000, 0x7FF8000000000001,
                    0xFFF8000000000042, 0x7FF0000000000001,
                    0xFFF0000000000042, 0x3FEFFFFFFFFFFFFF,
                    0x3FF0000000000000, 0x3FF0000000000001,
                    0x3FDFFFFFFFFFFFFF, 0x3FE0000000000000,
                    0x3FE0000000000001, 0x41DFFFFFFFFFFFFF,
                    0x41E0000000000000, 0x43E0000000000000},
        }
        for element, expected in required.items():
            with self.subTest(element=element):
                self.assertTrue(expected <= set(boundary_bits(element)))
                self.assertEqual(len(boundary_bits(element)), len(set(boundary_bits(element))))

    def test_all_integer_widths_include_signed_unsigned_and_narrowing_edges(self):
        for width in (8, 16, 32, 64):
            mask = (1 << width) - 1
            sign = 1 << (width - 1)
            for prefix in ("i", "u"):
                with self.subTest(element=f"{prefix}{width}"):
                    values = set(boundary_bits(f"{prefix}{width}"))
                    self.assertTrue({0, 1, 2, mask, mask - 1, sign, sign - 1, sign + 1} <= values)
                    self.assertTrue(all(0 <= value <= mask for value in values))
                    for bit in (7, 15, 23, 24, 31, 52, 53):
                        if bit < width:
                            self.assertTrue({(1 << bit) - 1, 1 << bit, (1 << bit) + 1} <= values)

    def test_ordered_pairs_really_include_both_nan_operand_orders(self):
        for shape, nan, other_nan, one in (
            ("f32x4", 0x7FC00001, 0x7F800042, 0x3F800000),
            ("f64x2", 0x7FF8000000000001, 0x7FF0000000000042, 0x3FF0000000000000),
        ):
            case = self.case(f"sse2.min.{shape}.default")
            observed = set()
            for group, candidate in structured_scenarios(case):
                if group != "ordered-boundary-pairs":
                    continue
                a = candidate["operands"]["a"]["lanes"]
                b = candidate["operands"]["b"]["lanes"]
                observed.update((int(x, 16), int(y, 16)) for x, y in zip(a, b, strict=True))
            with self.subTest(shape=shape):
                self.assertTrue(set(product((0, nan, other_nan, one), repeat=2)) <= observed)
                self.assertIn((nan, other_nan), observed)
                self.assertIn((other_nan, nan), observed)

    def test_every_vector_bit_walks_through_every_operand(self):
        for case_id in ("sse2.and.i32x4.default", "sse2.and.f64x2.default",
                        "sse2.add.i8x16.default"):
            case = self.case(case_id)
            positions = {group: {"a": set(), "b": set()}
                         for group in ("walking-one", "walking-zero")}
            for group, candidate in structured_scenarios(case):
                if group not in positions:
                    continue
                for name, operand in candidate["operands"].items():
                    width = int(operand["element"][1:])
                    mask = (1 << width) - 1
                    for lane, raw in enumerate(operand["lanes"]):
                        value = int(raw, 16)
                        active = value if group == "walking-one" else value ^ mask
                        if active:
                            self.assertEqual(active.bit_count(), 1)
                            positions[group][name].add(lane * width + active.bit_length() - 1)
            with self.subTest(case=case_id):
                for operands in positions.values():
                    for bits in operands.values():
                        self.assertEqual(bits, set(range(128)))

    def test_vector_shift_counts_cover_width_edges_and_ignored_upper_lane(self):
        for width, lanes in ((16, 8), (32, 4), (64, 2)):
            case = self.case(f"sse2.sll.i{width}x{lanes}.vector-count")
            observed = set()
            for group, candidate in structured_scenarios(case):
                if group == "vector-shift-counts":
                    observed.add(tuple(int(x, 16) for x in candidate["operands"]["count"]["lanes"]))
            for low in (0, 1, width - 1, width, width + 1, 255, 1 << 32, 1 << 63, (1 << 64) - 1):
                for high in (0, 1, (1 << 64) - 1):
                    with self.subTest(width=width, low=low, high=high):
                        self.assertIn((low, high), observed)

    def test_default_profile_validates_all_contracts_immediates_and_memory_residues(self):
        counts = Counter()
        immediate_values = {}
        memory_offsets = {}
        ids = set()
        for record in _all_records(self.cases, count_per_case=PROFILE_COUNTS["adversarial"],
                                   seed_text=DEFAULT_SEED, profile="adversarial"):
            case = self.case(record["case_id"])
            counts[case.id] += 1
            self.assertNotIn(record["input_id"], ids)
            ids.add(record["input_id"])
            for name, value in record.get("immediates", {}).items():
                immediate_values.setdefault((case.id, name), set()).add(value)
            for name, operand in record["operands"].items():
                if "buffer" in operand:
                    memory_offsets.setdefault((case.id, name), set()).add(operand["offset"])
        self.assertEqual(len(ids), sum(adversarial_case_count(case, 512) for case in self.cases))
        self.assertEqual(len(counts), len(self.cases))
        for case in self.cases:
            with self.subTest(case=case.id):
                self.assertEqual(counts[case.id], adversarial_case_count(case, 512))
                for name, definition in case.data.get("immediates", {}).items():
                    self.assertEqual(immediate_values[(case.id, name)], set(definition["values"]))
                for name, contract in case.data.get("memory_contract", {}).items():
                    alignment = contract["required_alignment"]
                    self.assertTrue(set(range(0, 32, alignment)) <= memory_offsets[(case.id, name)])

    def test_finite_scalar_space_is_exhaustive_and_never_waits_for_impossible_uniqueness(self):
        case = self.case("sse2.set1.i8x16.default")
        rows = list(_all_records((case,), count_per_case=100000, seed_text=DEFAULT_SEED,
                                 profile="adversarial"))
        self.assertEqual(len(rows), 256)
        self.assertEqual({int(row["operands"]["value"]["bits"], 16) for row in rows}, set(range(256)))
        self.assertEqual(adversarial_case_count(case, 7), 7)
        self.assertEqual(adversarial_case_count(case, 512), 256)

    def test_seed_changes_fuzz_only_and_does_not_invoke_an_output_oracle(self):
        case = self.case("sse2.min.f32x4.default")
        with patch("equilane.development.load_development_case", side_effect=AssertionError("model executed")):
            first = list(islice(adversarial_candidates(case, seed_text=DEFAULT_SEED), 1200))
            second = list(islice(adversarial_candidates(case, seed_text="0x0000000000000001"), 1200))
        first_structured = [row for row in first if row["generation"]["class"] != "random"]
        second_structured = [row for row in second if row["generation"]["class"] != "random"]
        self.assertEqual(first_structured, second_structured)
        first_fuzz = [row for row in first if row["generation"]["class"] == "random"]
        second_fuzz = [row for row in second if row["generation"]["class"] == "random"]
        self.assertTrue(first_fuzz)
        self.assertNotEqual(first_fuzz, second_fuzz)
        for index, candidate in enumerate(first, 1):
            validate_input_record(materialize(case, candidate, index), case, expected_sequence=index)

    def test_coverage_is_tied_to_actual_input_ids_and_reports_missing_scenarios(self):
        case = self.case("sse2.loadu.f64x2.default")
        rows = list(_all_records((case,), count_per_case=512, seed_text=DEFAULT_SEED, profile="adversarial"))
        report = build_adversarial_coverage((case,), rows, vectors_sha256="a" * 64)
        self.assertEqual(report["scope"], "generated-inputs-only")
        self.assertEqual(report["test_vectors_sha256"], "a" * 64)
        coverage = report["cases"][0]
        self.assertEqual(coverage["record_count"], 512)
        self.assertEqual(coverage["unique_input_count"], 512)
        self.assertEqual(coverage["memory"]["source"]["offset_residues_mod_16"], list(range(16)))
        self.assertGreater(coverage["memory"]["source"]["exact_fit_inputs"], 0)
        truncated = build_adversarial_coverage((case,), rows[:8], vectors_sha256="b" * 64)
        self.assertFalse(truncated["all_structured_scenarios_covered"])
        self.assertEqual(truncated["cases"][0]["record_count"], 8)

    def test_artifact_writes_coverage_before_completion_marker_and_parallel_is_identical(self):
        subset_cases = (self.case("sse2.set1.i8x16.default"), self.case("sse2.shuffle.f32x4.imm8"))
        subset = CaseRegistry({case.id: case for case in subset_cases},
                              self.cases.projected_sha256({case.id for case in subset_cases}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            serial = generate_artifact(cases=subset, isa_registry=self.isa, output=root / "serial",
                                       profile="adversarial", jobs=1)
            parallel = generate_artifact(cases=subset, isa_registry=self.isa, output=root / "parallel",
                                         profile="adversarial", jobs=2)
            self.assertEqual(serial.record_count, 768)
            self.assertEqual(serial.sha256, parallel.sha256)
            self.assertEqual(serial.vectors_path.read_bytes(), parallel.vectors_path.read_bytes())
            coverage = read_canonical_json(root / "serial/adversarial-coverage.json")
            self.assertEqual(coverage["record_count"], 768)
            self.assertEqual(coverage["test_vectors_sha256"], serial.sha256)
            self.assertEqual(coverage, read_canonical_json(root / "parallel/adversarial-coverage.json"))
            validated = validate_input_artifact(root / "parallel", cases=subset, isa_registry=self.isa)
            self.assertEqual(validated.record_count, 768)

    def test_cli_check_uses_adversarial_default_and_preserves_explicit_small_count(self):
        from equilane.cli import main
        for count, expected in ((None, 256), (2, 2)):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "check"
                args = ["check", "--cases", str(PROJECT / "10_official_suite/cases/set1-i8x16.py"),
                        "--isa-registry", str(PROJECT / "contracts/isa-registry.json"),
                        "--profile", "adversarial", "--output", str(output), "--quiet"]
                if count is not None:
                    args += ["--count-per-case", str(count)]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                manifest = read_canonical_json(output / "vectors/test-vectors.manifest.json")
                self.assertEqual(manifest["test_vectors"]["record_count"], expected)


if __name__ == "__main__":
    unittest.main()
