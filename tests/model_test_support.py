"""Validate model inputs before checking independent expected output.

This module contains no TestCase: importing shared helpers must not duplicate
the existing adversarial tests during unittest discovery.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.cases import load_case_definitions, validate_case_definition
from equilane.development import load_development_case
from equilane.oracle import ModelOutputMismatch
from equilane.isa import load_isa_registry
from equilane.records import derive_input_id, validate_input_record


@lru_cache(maxsize=1)
def _contracts():
    isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
    cases = load_case_definitions(PROJECT / "10_official_suite/cases", isa_registry=isa)
    return isa, cases


def initialize_models(cls):
    cls.isa, cls.cases = _contracts()


class VerifiedModelMixin:
    verification_subject = "portable_model_oracle"
    oracle_reference = "tests/BUG_HUNT.md"
    oracle_method = "independent_integer_reference"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        initialize_models(cls)

    def assert_model(self, case_id, operands, expected=None, *, family="output_mismatch",
                     buffers=None, immediates=None, observed=None, rounding="nearest_even", extended=False,
                     case_override=None, contract_scope=None, oracle_reference=None,
                     oracle_method=None):
        case = case_override if case_override is not None else self.cases.get(case_id)
        self.assertEqual(case.id, case_id)
        record = {
            "schema_version": 1,
            "case_id": case_id,
            "sequence": 1,
            "generation": {"class": "structured"},
            "environment": {"fp_mode": "ieee", "rounding": rounding},
            "operands": operands,
        }
        if buffers is not None:
            record["buffers"] = buffers
        if immediates is not None:
            record["immediates"] = immediates
        wanted = observed if observed is not None else {"return": expected}
        record["input_id"] = derive_input_id(record)
        if extended:
            data = copy.deepcopy(case.data)
            data["environment"]["fp_rounding_modes"] = sorted({"nearest_even", rounding})
            data["regressions"] = {"boundary-oracle.v1": {
                "input_id": record["input_id"],
                "expected_intel": {"status": "ok", "observed": wanted},
            }}
            case = validate_case_definition(data, isa_registry=self.isa, source_path=case.source_path)
            record["generation"] = {"class": "regression", "regression_id": "boundary-oracle.v1"}
        validate_input_record(record, case)
        actual = load_development_case(case).execute(record)
        self.oracle_checks = getattr(self, "oracle_checks", 0) + 1
        ids = getattr(self, "oracle_input_ids", None)
        if ids is None:
            ids = self.oracle_input_ids = set()
        ids.add(record["input_id"])
        if actual != wanted:
            scope = contract_scope or (
                "validated_rounding_extension" if extended else
                "validated_contract_extension" if case_override is not None else
                "official_nearest_even"
            )
            raise ModelOutputMismatch(
                input_record=record, expected=wanted, actual=actual,
                finding_family=family, contract_scope=scope,
                case_contract=case.data if scope != "official_nearest_even" else None,
                oracle_reference=oracle_reference or self.oracle_reference,
                oracle=oracle_method or self.oracle_method,
            )
