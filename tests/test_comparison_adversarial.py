"""Independent comparator truth tables from specification section 13.

IEEE bit patterns, decimal thresholds and expected ULP distances are literal
reference cases. Expected results never call a product model or comparator.
Status combinations are separate tests; richer numeric matrices use subtests.
"""

from __future__ import annotations

import copy
import itertools
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.cases import validate_case_definition
from equilane.compare import compare_float_bits, compare_result_records
from equilane.records import derive_input_id
from equilane.report import RecordReport, write_reports
from equilane.canonical import read_canonical_json


NAN_IGNORE = {"both_nan": "equal", "payload": "ignore", "quiet_signaling": "ignore", "sign": "ignore"}
FLAGS = ["invalid", "divide-by-zero", "overflow", "underflow", "inexact"]
STATUSES = ["ok", "infrastructure_error", "invalid_input", "runtime_error", "signal", "unsupported"]
STAGES = {"infrastructure_error": "runner", "invalid_input": "input_validation",
          "runtime_error": "execution", "signal": "signal", "unsupported": "capability"}
BITS = {
    "f32": {"zero": "0x00000000", "negative_zero": "0x80000000", "tiny": "0x00000001",
            "negative_tiny": "0x80000001", "one": "0x3f800000", "next_one": "0x3f800001",
            "negative_one": "0xbf800000", "negative_next": "0xbf800001", "quarter_more": "0x3fa00000",
            "inf": "0x7f800000", "negative_inf": "0xff800000", "nan": "0x7fc00042",
            "nan_other": "0x7fc00043", "snan": "0x7f800042", "negative_nan": "0xffc00042"},
    "f64": {"zero": "0x0000000000000000", "negative_zero": "0x8000000000000000", "tiny": "0x0000000000000001",
            "negative_tiny": "0x8000000000000001", "one": "0x3ff0000000000000", "next_one": "0x3ff0000000000001",
            "negative_one": "0xbff0000000000000", "negative_next": "0xbff0000000000001", "quarter_more": "0x3ff4000000000000",
            "inf": "0x7ff0000000000000", "negative_inf": "0xfff0000000000000", "nan": "0x7ff8000000000042",
            "nan_other": "0x7ff8000000000043", "snan": "0x7ff0000000000042", "negative_nan": "0xfff8000000000042"},
}


def _case(*, mask=False, memory=False, flags=False):
    returned = {"type": "mask" if mask else "vector", "element": "u32", "lanes": 4}
    arguments = [{"name": "a", **returned}]
    data = {
        "schema_version": 1, "id": "sse2.adversarial.compare.default", "description": "Comparator truth table",
        "tags": [], "intel": {"symbol": "intel_compare", "required_isa": ["sse2"]},
        "openpower": {"symbol": "power_compare", "required_isa": ["power8", "vsx"]},
        "signature": {"arguments": arguments, "return": returned},
        "input_domain": {"exclude": []}, "comparison": {"mode": "bit_exact"},
        "environment": {"fp_rounding_modes": ["nearest_even"], "observe_fp_exceptions": flags},
    }
    if memory:
        data["signature"]["arguments"] = [{"name": "left", "type": "pointer"}, {"name": "right", "type": "pointer"}]
        data["memory_contract"] = {name: {"access": "write", "required_alignment": 1,
            "read_ranges": [], "write_ranges": [{"offset": 0, "byte_length": 2}]} for name in ("left", "right")}
    if flags:
        data["regressions"] = {"flag-witness.v1": {"input_id": "0" * 64, "expected_intel": {
            "status": "ok", "observed": {"return": {"element": "u32", "lanes": ["0x00000000"] * 4}, "fp_exceptions": FLAGS[:]}}}}
    return validate_case_definition(data)


def _input(case, *, memory=False):
    record = {"schema_version": 1, "case_id": case.id, "sequence": 1, "generation": {"class": "structured"},
              "environment": {"fp_mode": "ieee", "rounding": "nearest_even"},
              "operands": {"a": {"element": "u32", "lanes": ["0x00000000"] * 4}}}
    if memory:
        record["operands"] = {"left": {"buffer": "shared", "offset": 0}, "right": {"buffer": "shared", "offset": 1}}
        record["buffers"] = {"shared": {"alignment": 1, "bytes": "0x00010203"}}
    record["input_id"] = derive_input_id(record)
    return record


def _result(case, record, role, status="ok", *, lanes=None, flags=None, buffer=None):
    result = {"schema_version": 1, "case_id": case.id, "input_id": record["input_id"],
              "runner": role, "duration_ns": 17 if role == "intel" else 1009, "status": status}
    if status != "ok":
        result["error"] = {"code": "known." + status, "stage": STAGES[status]}
    else:
        result["observed"] = {"return": {"element": "u32", "lanes": ["0x00000000"] * 4 if lanes is None else lanes}}
        if flags is not None:
            result["observed"]["fp_exceptions"] = flags
        if buffer is not None:
            result["observed"]["buffers"] = {"shared": {"byte_offset": 0, "bytes": buffer}}
    return result


class ComparisonAdversarialTests(unittest.TestCase):
    def check_status(self, intel_status, power_status):
        case = _case()
        record = _input(case)
        intel = _result(case, record, "intel", intel_status)
        power = _result(case, record, "openpower", power_status, lanes=["0xffffffff"] * 4)
        outcome = compare_result_records(case, record, intel, power)
        if intel_status != power_status:
            self.assertEqual((outcome.outcome, outcome.mismatch_count), ("mismatch", 1))
            self.assertEqual(outcome.first_difference, {"kind": "status", "intel": intel_status, "openpower": power_status})
        elif intel_status == "ok":
            self.assertEqual((outcome.outcome, outcome.mismatch_count), ("mismatch", 4))
            self.assertEqual(outcome.first_difference["lane"], 0)
        else:
            self.assertEqual((outcome.outcome, outcome.mismatch_count, outcome.first_difference), ("not_comparable", 0, None))
            self.assertEqual(outcome.reason, "both_" + intel_status)

    def check_nan_policy(self, both_nan, payload, quiet_signaling, sign):
        policy = {"both_nan": both_nan, "payload": payload, "quiet_signaling": quiet_signaling, "sign": sign}
        # Attribute-equality columns are independent literal semantic facts.
        pairs = [("nan", "nan", True, True, True), ("nan", "nan_other", False, True, True),
                 ("nan", "snan", True, False, True), ("nan", "negative_nan", True, True, False),
                 ("snan", "snan", True, True, True)]
        for element in BITS:
            comparison = {"mode": "ieee_value", "signed_zero": "equal", "nan": policy}
            for a, b, same_payload, same_kind, same_sign in pairs:
                expected = both_nan == "equal" and (payload == "ignore" or same_payload) and (quiet_signaling == "ignore" or same_kind) and (sign == "ignore" or same_sign)
                for left, right in ((a, b), (b, a)):
                    with self.subTest(element=element, left=left, right=right, policy=policy):
                        self.assertEqual(compare_float_bits(BITS[element][left], BITS[element][right], element=element, comparison=comparison), expected)
            for non_nan in ("one", "inf", "zero"):
                with self.subTest(element=element, other=non_nan):
                    self.assertFalse(compare_float_bits(BITS[element]["nan"], BITS[element][non_nan], element=element, comparison=comparison))

    def test_mask_return_compares_bits_instead_of_boolean_truth(self):
        case = _case(mask=True)
        record = _input(case)
        for lane, bit in itertools.product(range(4), range(32)):
            left = ["0x00000000"] * 4
            right = left[:]
            left[lane] = f"0x{1 << bit:08x}"
            right[lane] = "0xffffffff"
            with self.subTest(lane=lane, bit=bit):
                compared = compare_result_records(case, record, _result(case, record, "intel", lanes=left), _result(case, record, "openpower", lanes=right))
                self.assertEqual(compared.mismatch_count, 1)
                self.assertEqual(compared.first_difference["lane"], lane)

    def test_all_fp_exception_subsets_count_flags_and_keep_normative_order(self):
        case = _case(flags=True)
        record = _input(case)
        for left, right in itertools.product(range(32), repeat=2):
            a = [flag for index, flag in enumerate(FLAGS) if left & (1 << index)]
            b = [flag for index, flag in enumerate(FLAGS) if right & (1 << index)]
            with self.subTest(intel=left, openpower=right):
                compared = compare_result_records(case, record, _result(case, record, "intel", flags=a), _result(case, record, "openpower", flags=b))
                self.assertEqual(compared.mismatch_count, (left ^ right).bit_count())
                self.assertEqual(compared.outcome, "match" if left == right else "mismatch")
                if left != right:
                    self.assertEqual(compared.first_difference, {"kind": "fp_exceptions", "intel": a, "openpower": b})

    def test_aliased_pointer_write_ranges_form_a_union_without_double_counting(self):
        case = _case(memory=True)
        record = _input(case, memory=True)
        for a, b, atoms, first_kind in [
            ("0x11223303", "0x11223303", 0, None),
            ("0x0001ff03", "0x00010203", 1, "buffer"),
            ("0x000102ff", "0x000102ff", 2, "memory_contract"),
            ("0x000102ff", "0x00010203", 1, "memory_contract"),
            ("0x001122ff", "0x000102ee", 4, "memory_contract"),
        ]:
            with self.subTest(intel=a, openpower=b):
                compared = compare_result_records(case, record, _result(case, record, "intel", buffer=a), _result(case, record, "openpower", buffer=b))
                self.assertEqual(compared.mismatch_count, atoms)
                if first_kind is not None:
                    self.assertEqual(compared.first_difference["kind"], first_kind)

    def check_ulp(self, element):
        table = [("one", "next_one", "equal", 1), ("negative_one", "negative_next", "equal", 1),
                 ("negative_zero", "tiny", "equal", 1), ("negative_zero", "tiny", "distinct", 2),
                 ("negative_tiny", "zero", "equal", 1), ("negative_tiny", "tiny", "equal", 2),
                 ("negative_tiny", "tiny", "distinct", 3),
                 ("negative_one", "one", "distinct", 2130706433 if element == "f32" else 9214364837600034817),
                 ("negative_one", "one", "equal", 2130706432 if element == "f32" else 9214364837600034816)]
        for a, b, zero_policy, distance in table:
            for threshold in (distance - 1, distance, distance + 1):
                comparison = {"mode": "ulp", "max_ulps": str(threshold), "signed_zero": zero_policy, "nan": NAN_IGNORE}
                for left, right in ((a, b), (b, a)):
                    with self.subTest(element=element, a=left, b=right, max_ulps=threshold, zero=zero_policy):
                        self.assertEqual(compare_float_bits(BITS[element][left], BITS[element][right], element=element, comparison=comparison), threshold >= distance)

    def check_abs_rel(self, element):
        for absolute, relative, expected in [("0.249999999999999999999999999999", "0", False),
                ("0.25", "0", True), ("0", "0.199999999999999999999999999999", False),
                ("0", "0.2", True), ("0.249", "0.199", False),
                ("1e1000000", "0", True), ("1e-1000000", "0", False), ("0e1000000", "0", False)]:
            comparison = {"mode": "abs_rel", "abs_tolerance": absolute, "rel_tolerance": relative, "signed_zero": "equal", "nan": NAN_IGNORE}
            with self.subTest(element=element, absolute=absolute, relative=relative):
                self.assertEqual(compare_float_bits(BITS[element]["one"], BITS[element]["quarter_more"], element=element, comparison=comparison), expected)
                self.assertEqual(compare_float_bits(BITS[element]["quarter_more"], BITS[element]["one"], element=element, comparison=comparison), expected)
        for tolerance, expected in ([("1e-45", False), ("2e-45", True)] if element == "f32" else [("4e-324", False), ("5e-324", True)]):
            comparison = {"mode": "abs_rel", "abs_tolerance": tolerance, "rel_tolerance": "0", "signed_zero": "equal", "nan": NAN_IGNORE}
            with self.subTest(element=element, smallest_subnormal=tolerance):
                self.assertEqual(compare_float_bits(BITS[element]["zero"], BITS[element]["tiny"], element=element, comparison=comparison), expected)

    def test_special_values_override_even_extremely_large_numeric_tolerances(self):
        for element, mode, zero_policy in itertools.product(BITS, ("ieee_value", "ulp", "abs_rel", "classification"), ("equal", "distinct")):
            comparison = {"mode": mode, "signed_zero": zero_policy, "nan": NAN_IGNORE}
            if mode == "ulp":
                comparison["max_ulps"] = "18446744073709551615"
            if mode == "abs_rel":
                comparison.update(abs_tolerance="1e1000000", rel_tolerance="1e1000000")
            for left, right, expected in [("inf", "inf", True), ("negative_inf", "negative_inf", True),
                    ("inf", "negative_inf", False), ("inf", "one", False), ("nan", "inf", False),
                    ("zero", "negative_zero", zero_policy == "equal")]:
                with self.subTest(element=element, mode=mode, zero_policy=zero_policy, a=left, b=right):
                    self.assertEqual(compare_float_bits(BITS[element][left], BITS[element][right], element=element, comparison=comparison), expected)

    def test_junit_and_summary_keep_status_failures_distinct_from_noncomparability(self):
        case = _case()
        record = _input(case)
        outcomes = []
        for intel, power in (("ok", "ok"), ("ok", "unsupported"), ("unsupported", "unsupported")):
            comparison = compare_result_records(case, record, _result(case, record, "intel", intel), _result(case, record, "openpower", power))
            outcomes.append(RecordReport(case.id, record["input_id"] + intel + power, comparison))
        with tempfile.TemporaryDirectory() as temporary:
            files = write_reports(temporary, reversed(outcomes))
            summary = read_canonical_json(files.summary_path)
            self.assertEqual((summary["matched_inputs"], summary["mismatched_inputs"], summary["not_comparable_inputs"]), (1, 1, 1))
            self.assertEqual(summary["outcome"], "not_comparable")
            suite = ET.parse(files.junit_path).getroot()
            self.assertEqual((suite.attrib["tests"], suite.attrib["failures"], suite.attrib["errors"]), ("3", "1", "1"))


def _install(name, action):
    def test(self):
        action(self)
    test.__name__ = "test_" + name
    setattr(ComparisonAdversarialTests, test.__name__, test)


for _intel, _power in itertools.product(STATUSES, repeat=2):
    _install(f"status_{_intel}_vs_{_power}", lambda self, a=_intel, b=_power: self.check_status(a, b))
for _both, _payload, _quiet, _sign in itertools.product(("equal", "unequal"), ("ignore", "match"), ("ignore", "match"), ("ignore", "match")):
    _install(f"nan_{_both}_payload_{_payload}_quiet_{_quiet}_sign_{_sign}",
             lambda self, b=_both, p=_payload, q=_quiet, s=_sign: self.check_nan_policy(b, p, q, s))
for _element in BITS:
    _install(f"ulp_literal_boundaries_{_element}", lambda self, e=_element: self.check_ulp(e))
    _install(f"abs_rel_exact_thresholds_{_element}", lambda self, e=_element: self.check_abs_rel(e))


if __name__ == "__main__":
    unittest.main()
