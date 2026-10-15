"""Target identity survives comparison, portable failure capture and replay."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import atomic_write, dump_bytes, read_canonical_json
from equilane.cases import load_case_definitions
from equilane.compare import compare_result_records, compare_result_record_set
from equilane.comparison import load_comparison_context, compare_artifacts
from equilane.errors import ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.replay import load_failure_bundle, verify_replay_artifacts
from equilane.report import write_failure_bundle
from equilane.reproduction import reproduction_commands

PROJECT = Path(__file__).resolve().parents[1]


class TargetReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / 'contracts/isa-registry.json')
        cls.cases = load_case_definitions(PROJECT / '10_official_suite/cases/add-f32x4.py',
                                         isa_registry=cls.isa)

    def artifacts(self, root, role):
        generated = generate_artifact(cases=self.cases, isa_registry=self.isa,
                                      output=root / 'inputs', count_per_case=2)
        inputs = validate_input_artifact(generated.manifest_path, self.cases, self.isa)
        baseline = run_fixture(input_artifact=inputs, cases=self.cases, isa_registry=self.isa,
                               role='intel', output=root / 'intel')
        target = run_fixture(input_artifact=inputs, cases=self.cases, isa_registry=self.isa,
                             role=role, output=root / role)
        return inputs, baseline, target

    def test_target_record_identity_and_difference_names(self):
        for role in ('arm', 'riscv'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, intel, target = self.artifacts(Path(temporary), role)
                input_record = inputs.records[0]
                key = input_record['input_id']
                case = self.cases.get(input_record['case_id'])
                intel_records = {r['input_id']: r for r in intel.records}
                target_records = {r['input_id']: r for r in target.records}
                with self.assertRaises(ValidationError):
                    compare_result_records(case, input_record, intel_records[key], target_records[key])
                changed = copy.deepcopy(target_records[key])
                changed['observed']['return']['lanes'][0] = '0x3f000000'
                comparison = compare_result_records(case, input_record, intel_records[key], changed,
                                                    target_role=role)
                self.assertEqual(comparison.outcome, 'mismatch')
                self.assertIn(role, comparison.first_difference)
                self.assertNotIn('openpower', comparison.first_difference)
                diagnostic = comparison.first_difference['diagnostic']
                self.assertEqual(set(diagnostic), {'intel', role})
                self.assertEqual(diagnostic[role]['classification'], 'normal')
                ordered = compare_result_record_set(cases=self.cases, input_records=inputs.records,
                    intel_records=intel_records, openpower_records=target_records, target_role=role)
                self.assertTrue(all(r.outcome == 'match' for r in ordered))

    def test_target_failure_roundtrip_and_replay_preserve_actual_roles(self):
        for role in ('arm', 'riscv'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inputs, intel_run, target_run = self.artifacts(root, role)
                intel = validate_result_artifact(intel_run.manifest_path, self.cases, self.isa,
                                                input_artifact=inputs)
                target = validate_result_artifact(target_run.manifest_path, self.cases, self.isa,
                                                 input_artifact=inputs)
                record = inputs.records[0]
                key = record['input_id']
                target.records[key]['observed']['return']['lanes'][0] = '0x3f000000'
                difference = compare_result_records(self.cases.get(record['case_id']), record,
                    intel.records[key], target.records[key], target_role=role)
                path = write_failure_bundle(output=root / 'comparison', input_artifact=inputs,
                    intel_artifact=intel, openpower_artifact=target, cases=self.cases,
                    isa_registry=self.isa, input_record=record, comparison=difference, target_role=role)
                bundle = load_failure_bundle(path)
                self.assertEqual(bundle.target_role, role)
                self.assertEqual(bundle.baseline_target.role, role)
                self.assertIn(f'{role}_manifest', bundle.failure['baseline'])
                self.assertIn('--target', bundle.failure['reproduce']['verify'])
                captured_records = copy.deepcopy(bundle.baseline_target.records)
                captured_failure = copy.deepcopy(bundle.failure)
                verification, _, _ = verify_replay_artifacts(bundle,
                    intel_manifest=bundle.baseline_intel.manifest_path,
                    openpower_manifest=bundle.baseline_target.manifest_path, target_role=role)
                self.assertTrue(verification.reproduced)
                self.assertEqual(set(verification.environment_differences), {'intel', role})
                with self.assertRaises(ValidationError):
                    verify_replay_artifacts(bundle, intel_manifest=bundle.baseline_intel.manifest_path,
                        openpower_manifest=bundle.baseline_target.manifest_path,
                        target_role='arm' if role == 'riscv' else 'riscv')
                failure = read_canonical_json(path / 'failure.json')
                failure['baseline'][f'{role}_manifest'] = '../outside.manifest.json'
                atomic_write(path / 'failure.json', dump_bytes(failure, newline=True))
                with self.assertRaises(ValidationError):
                    load_failure_bundle(path)
                # A later edit of public paths cannot mutate a loaded snapshot.
                bundle.baseline_target.results_path.write_bytes(b'changed after load\n')
                self.assertEqual(bundle.baseline_target.records, captured_records)
                self.assertEqual(bundle.failure, captured_failure)

    def test_empty_record_set_still_rejects_unknown_target_identity(self):
        for role in ('intel', 'unknown', '', None):
            with self.subTest(role=role), self.assertRaises(ValidationError):
                compare_result_record_set(cases=self.cases, input_records=(),
                    intel_records={}, openpower_records={}, target_role=role)

    def test_legacy_power_bundle_has_unchanged_shape_paths_and_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, intel_run, power_run = self.artifacts(root, 'openpower')
            intel = validate_result_artifact(intel_run.manifest_path, self.cases, self.isa,
                                            input_artifact=inputs)
            power = validate_result_artifact(power_run.manifest_path, self.cases, self.isa,
                                            input_artifact=inputs)
            record = inputs.records[0]
            key = record['input_id']
            power.records[key]['observed']['return']['lanes'][0] = '0x3f000000'
            difference = compare_result_records(self.cases.get(record['case_id']), record,
                                                intel.records[key], power.records[key])
            path = write_failure_bundle(output=root / 'comparison', input_artifact=inputs,
                intel_artifact=intel, openpower_artifact=power, cases=self.cases,
                isa_registry=self.isa, input_record=record, comparison=difference)
            failure = read_canonical_json(path / 'failure.json')
            self.assertNotIn('target_role', failure)
            self.assertEqual(failure['baseline'], {
                'intel_manifest': 'baseline/intel/intel-results.manifest.json',
                'openpower_manifest': 'baseline/openpower/power-results.manifest.json',
            })
            expected_commands = {
                'intel': ['equilane', 'replay', '--failure', 'failure.json', '--role',
                          'intel', '--output', 'replay/intel'],
                'openpower': ['equilane', 'replay', '--failure', 'failure.json', '--role',
                              'openpower', '--output', 'replay/openpower'],
                'verify': ['equilane', 'verify-replay', '--failure', 'failure.json', '--intel',
                           'replay/intel/intel-results.manifest.json', '--openpower',
                           'replay/openpower/power-results.manifest.json'],
            }
            self.assertEqual(failure['reproduce'], expected_commands)
            self.assertEqual(reproduction_commands(), expected_commands)
            bundle = load_failure_bundle(path)
            self.assertEqual(bundle.target_role, 'openpower')
            self.assertIn('openpower', bundle.failure['first_difference']['diagnostic'])
            failure['reproduce'] = {
                key: ['ioitf', *value[1:]] for key, value in expected_commands.items()
            }
            atomic_write(path / 'failure.json', dump_bytes(failure, newline=True))
            self.assertEqual(load_failure_bundle(path).target_role, 'openpower')

    def test_complete_target_workflow_validates_arguments_and_publishes_target(self):
        for role in ('arm', 'riscv'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inputs, intel, target = self.artifacts(root, role)
                context = load_comparison_context(cases=self.cases, isa_registry=self.isa,
                    input_manifest=inputs.manifest_path, intel_manifest=intel.manifest_path,
                    openpower_manifest=target.manifest_path, target_role=role)
                run = compare_artifacts(context, output=root / 'comparison',
                                        allow_development_fixtures=True)
                self.assertEqual(run.exit_code, 0)
                self.assertEqual(read_canonical_json(run.files.summary_path)['target_role'], role)
