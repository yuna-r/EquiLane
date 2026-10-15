"""The report must preserve target identities and count shared Intel inputs once."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from equilane.cases import CaseDefinition
from equilane.metrics import collect_verification_metrics
from equilane.showcase import render_showcase_html


class TargetShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.roles = ('openpower', 'arm', 'riscv')
        self.cases = tuple(
            CaseDefinition(f'sse2.demo.{element}.default', {
                'comparison': {'mode': 'bit_exact'},
                'description': f'{element} review case',
                'signature': {
                    'arguments': [{'name': 'a', 'type': 'vector', 'element': element,
                                   'lanes': lanes}],
                    'return': {'type': 'vector', 'element': element, 'lanes': lanes},
                },
                'intel': {'required_isa': ['sse2'], 'symbol': 'intel_demo'},
                'openpower': {'required_isa': ['power8', 'vsx'], 'symbol': 'power_demo'},
                'arm': {'required_isa': ['neon'], 'symbol': 'arm_demo'},
                'riscv': {'required_isa': ['rvv'], 'symbol': 'riscv_demo'},
            }) for element, lanes in (('f64', 2), ('i8', 16))
        )
        per_target = {
            'record_count': 16, 'matched_inputs': 16, 'mismatched_inputs': 0,
            'not_comparable_inputs': 0, 'mismatch_atoms': 0, 'outcome': 'pass',
        }
        self.summary = {
            **{key: value * 3 for key, value in per_target.items() if isinstance(value, int)},
            'outcome': 'pass',
            'targets': {role: copy.deepcopy(per_target) for role in self.roles},
        }

    def render(self, *, summary=None, roles=None, quality=None, infer=False):
        return render_showcase_html(
            cases=self.cases, summary=self.summary if summary is None else summary,
            profile='smoke', seed='0x1234', vector_sha256='a' * 64,
            case_definitions_sha256='b' * 64, isa_contract_sha256='c' * 64,
            generated_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
            native_evidence=False,
            target_roles=None if infer else (self.roles if roles is None else roles),
            quality=quality,
        )

    def test_multi_metrics_count_one_shared_intel_path_and_all_target_pairs(self):
        metrics = collect_verification_metrics(self.cases, self.summary)
        self.assertEqual(metrics.trials, 48)
        self.assertEqual(metrics.vectors_per_case, 8)
        self.assertEqual(metrics.implementation_path_evaluations, 64)
        self.assertEqual(metrics.lane_verdicts, 432)
        self.assertEqual(metrics.bit_positions, 6144)
        html = self.render()
        self.assertIn('16 unique inputs × 4 implementation paths; Intel is evaluated once', html)
        for channel, expected in (('PATH-02', '64'), ('LANE-03', '432'), ('BIT-04', '6,144')):
            self.assertRegex(html, rf'data-channel="{channel}"[\s\S]*?<strong>{expected}</strong>')
        for label in ('OPENPOWER', 'ARM64', 'RISC-V64'):
            self.assertIn(f'<b>{label}</b>', html)
        self.assertEqual(html.count('16 / 16 matched'), 3)
        self.assertIn('This is not CPU-native evidence.', html)

    def test_mixed_outcomes_are_visible_per_target_without_synthetic_case_passes(self):
        summary = copy.deepcopy(self.summary)
        summary['targets']['arm'].update(matched_inputs=15, mismatched_inputs=1,
                                         mismatch_atoms=1, outcome='mismatch')
        summary.update(matched_inputs=47, mismatched_inputs=1, mismatch_atoms=1,
                       outcome='mismatch')
        html = self.render(summary=summary)
        self.assertIn('COMPARISON MISMATCH', html)
        self.assertRegex(html, r'data-status="mismatch"><span>ARM64</span><b>15 / 16 matched</b>')
        self.assertIn('individual case verdicts are not inferred from aggregate results', html)

    def test_target_roles_must_be_unique_and_agree_with_aggregate_identity(self):
        for roles in (('arm', 'arm'), ('arm',), ('openpower', 'arm'), ('intel',), ()):
            with self.subTest(roles=roles), self.assertRaises(ValueError):
                self.render(roles=roles)

    def test_target_identity_is_inferred_from_aggregate_or_single_metadata(self):
        html = self.render(infer=True)
        for label in ('OPENPOWER', 'ARM64', 'RISC-V64'):
            self.assertIn(f'<b>{label}</b>', html)
        single = copy.deepcopy(self.summary['targets']['arm'])
        single['target_role'] = 'arm'
        html = self.render(summary=single, infer=True)
        self.assertIn('<b>ARM64</b>', html)
        self.assertNotIn('<b>OPENPOWER</b>', html)
        self.assertNotIn('<b>RISC-V64</b>', html)
        with self.assertRaises(ValueError):
            self.render(summary=single, roles=('riscv',))
        del single['target_role']
        # Metadata-free v1 native summaries retain the historical PPC default.
        html = self.render(summary=single, infer=True)
        self.assertIn('<b>OPENPOWER</b>', html)
        self.assertNotIn('<b>ARM64</b>', html)

    def test_target_counts_cannot_invent_equal_workloads_from_an_aggregate_total(self):
        summary = copy.deepcopy(self.summary)
        summary['targets']['arm'].update(record_count=8, matched_inputs=8)
        summary['targets']['riscv'].update(record_count=24, matched_inputs=24)
        # The aggregate remains divisible by all targets, but the target streams differ.
        with self.assertRaises(ValueError):
            collect_verification_metrics(self.cases, summary)

    def test_metadata_free_single_summary_cannot_claim_multiple_targets(self):
        single = copy.deepcopy(self.summary['targets']['arm'])
        with self.assertRaises(ValueError):
            self.render(summary=single, roles=('arm', 'riscv'))
        # A native caller can still name its one target with the v1 summary shape.
        self.assertIn('<b>ARM64</b>', self.render(summary=single, roles=('arm',)))


if __name__ == '__main__':
    unittest.main()
