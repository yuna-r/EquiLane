"""Guide-oriented report reading keeps execution and equivalence distinct."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from equilane.cases import CaseDefinition, load_case_definitions
from equilane.isa import load_isa_registry
from equilane.showcase import _CATALOG_SCRIPT, _case_cards, render_showcase_html

ROOT = Path(__file__).resolve().parents[1]


class IntrinsicHeadingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.heading = False
        self.headings = []
        self.current = ""

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self.heading, self.current = True, ""

    def handle_data(self, data):
        if self.heading:
            self.current += data

    def handle_endtag(self, tag):
        if tag == "h3" and self.heading:
            self.headings.append(self.current)
            self.heading = False


class ShowcaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(ROOT / "contracts/isa-registry.json")
        cls.registry = load_case_definitions(ROOT / "10_official_suite/cases", isa_registry=cls.isa)
        cls.cases = tuple(cls.registry.get(case_id) for case_id in (
            "sse2.add.f32x4.default", "sse2.add.f64x2.default", "sse2.storeu.f64x2.default",
            "sse2.sqrt.f64x2.scalar"))

    def render(self, *, native=True, changes=None, quality=None):
        summary = {"record_count": 32, "matched_inputs": 31, "mismatched_inputs": 1,
                   "not_comparable_inputs": 0, "mismatch_atoms": 1, "outcome": "mismatch", "target_role": "arm",
                   "execution_counts": {"execution_errors": 0, "unsupported": 0, "invalid_inputs": 0}}
        summary.update(changes or {})
        return render_showcase_html(cases=self.cases, summary=summary, profile="smoke", seed="0x1234",
                                    vector_sha256="a" * 64, case_definitions_sha256="b" * 64,
                                    isa_contract_sha256="c" * 64, generated_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
                                    native_evidence=native, quality=quality)

    def test_intrinsic_name_prototype_and_operation_precede_framework_contract(self):
        html = self.render()
        parser = IntrinsicHeadingParser()
        parser.feed(html)
        self.assertIn("_mm_add_ps", parser.headings)
        self.assertIn("_mm_add_pd", parser.headings)
        self.assertIn("__m128 _mm_add_ps(__m128 a, __m128 b);", html)
        self.assertLess(html.index("__m128 _mm_add_ps"), html.index('class="case-contract"'))
        self.assertIn('guide-isa=SSE;', html)
        self.assertIn('guide-isa=SSE2;', html)
        self.assertIn('id="case-isa"', html)
        self.assertIn('value="SSE">SSE</option>', html)
        self.assertIn('value="SSE2">SSE2</option>', html)
        self.assertIn("_mm_sqrt_sd(a, a)", html)
        self.assertIn("部分集合", html)
        self.assertIn("arm_mm_add_ps", html)
        self.assertIn("Guide ISA とフレームワークの required_isa は区別", html)

    def test_native_execution_success_is_distinct_from_output_mismatch(self):
        html = self.render()
        self.assertIn("実行エラーなし", html)
        self.assertIn("ポートの適合性：未適合あり", html)
        self.assertIn("一致 31件 ／ 不一致 1件 ／ 比較不能 0件", html)
        self.assertIn("Docker / QEMU", html)
        self.assertIn("要求を満たさない結果は移植の未適合として確認します", html)
        self.assertIn("CPU固有の挙動も、移植契約への適合が必要です", html)
        self.assertNotIn("不一致を直ちに実装バグと断定しません", html)
        self.assertIn("4 ケース / 合計 32 入力", html)
        self.assertNotIn("4 ケース × 32", html)

    def test_partial_case_preview_keeps_unknown_bindings_unknown(self):
        original = self.cases[0]
        data = copy.deepcopy(original.data)
        del data["intel"]["symbol"]
        del data["arm"]["symbol"]
        del data["environment"]
        html = _case_cards((CaseDefinition(original.id, data),), 8, ("arm",))
        self.assertIn(f"<h3>{original.id}</h3>", html)
        self.assertIn("_mm_add_ps SSE", html)
        self.assertIn("未記録 // sse2", html)
        self.assertIn("FP exceptions：未記録", html)
        self.assertNotIn("intel_mm_add_ps //", html)

    def test_missing_execution_breakdown_does_not_claim_no_runtime_errors(self):
        html = self.render(changes={"execution_counts": None})
        self.assertIn("実行の内訳は未集計", html)
        self.assertNotIn("実行エラーなし", html)
        html = self.render(changes={"execution_counts": {"execution_errors": False, "unsupported": 0, "invalid_inputs": 0}})
        self.assertIn("実行の内訳は未集計", html)

    def test_unsupported_and_execution_errors_keep_separate_counts(self):
        html = self.render(changes={"execution_counts": {"execution_errors": 2, "unsupported": 3, "invalid_inputs": 1},
                                    "matched_inputs": 26, "not_comparable_inputs": 5, "outcome": "not_comparable"})
        self.assertIn("実行エラー 2件 ／ unsupported 3件 ／ invalid input 1件", html)
        self.assertIn("まず unsupported・invalid input・signal・runtime error", html)
        self.assertNotIn("実行エラーなし", html)

    def test_fixture_result_is_never_presented_as_a_cpu_execution(self):
        html = self.render(native=False)
        self.assertIn("各CPUの C 実装を実行した結果ではありません", html)
        self.assertIn("CPU別C実装の実測値は記録されていません", html)
        self.assertIn("モデル自体の正しさは、この一致だけでは確認できません", html)

    def test_pass_is_limited_to_observed_input_and_quality_failure_remains_visible(self):
        html = self.render(changes={"outcome": "pass", "matched_inputs": 32, "mismatched_inputs": 0, "mismatch_atoms": 0},
                           quality={"status": "fail", "gates": {}})
        self.assertIn("今回の入力では移植契約に適合", html)
        self.assertIn("一致率100%は今回観測した範囲の結果", html)
        self.assertIn("独立したモデル検証または品質ゲートに失敗があります", html)
        self.assertIn("QUALITY CHECK FAILED", html)

    def test_native_details_keep_saved_intel_bits_target_bits_memory_and_flags(self):
        record = {"case_id": "sse2.add.f64x2.default", "input_id": "a" * 64,
                  "environment": {"rounding": "nearest_even", "fp_mode": "ieee"},
                  "operands": {"a": {"element": "f64", "lanes": ["0x3ff0000000000000", "0x8000000000000000"]}}}
        intel = {"status": "ok", "observed": {"return": {"element": "f64", "lanes": ["0x3ff0000000000000", "0x8000000000000000"]},
                                                  "buffers": {"out": {"byte_offset": 0, "bytes": "0x80"}}, "fp_exceptions": ["inexact"]}}
        target = copy.deepcopy(intel)
        target["observed"]["return"]["lanes"][1] = "0x0000000000000000"
        target["observed"]["buffers"]["out"]["bytes"] = "0x00"
        target["observed"]["fp_exceptions"] = []
        html = self.render(changes={"native_findings": [{"case_id": record["case_id"], "input_id": record["input_id"],
            "input": record, "intel": intel, "target": target, "target_role": "arm", "first_difference": {"kind": "return", "lane": 1}}]})
        section = html.split('id="native-results"', 1)[1].split('<section class="panel oracle-report"', 1)[0]
        self.assertIn("_mm_add_pd → ARM64", section)
        self.assertIn("Intel baseline", section)
        self.assertIn("0x8000000000000000", section)
        self.assertIn("0x0000000000000000", section)
        self.assertIn("buffers.out.bytes[0]", section)
        self.assertIn("fp_exceptions[0]", section)
        self.assertIn("nearest_even", section)
        self.assertNotIn("Python モデル", section)

    def test_hostile_saved_details_stay_escaped(self):
        hostile = "</pre><img src=x onerror=alert(1)>"
        html = self.render(changes={"native_findings": [{"case_id": hostile, "input_id": hostile, "target_role": "arm",
            "input": {"environment": {"rounding": hostile}}, "intel": {"status": hostile}, "target": {"status": hostile}}]})
        self.assertNotIn(hostile, html)
        self.assertIn("&lt;/pre&gt;&lt;img", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the catalog filter")
    def test_guide_isa_filter_keeps_sse_distinct_from_sse2_and_resets(self):
        harness = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
let code=''; process.stdin.setEncoding('utf8'); process.stdin.on('data',part=>code+=part);
process.stdin.on('end',()=>{
const ids=['case-filters','case-search','case-type','case-mode','case-isa','case-count','case-empty','case-reset'];
const el=Object.fromEntries(ids.map(id=>[id,{value:'',textContent:'',hidden:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;},focus(){}}]));
const cards=[{dataset:{search:'_mm_add_ps ADDPS guide-isa=SSE;',element:'f32',mode:'bit_exact'}},
             {dataset:{search:'_mm_add_pd ADDPD guide-isa=SSE2;',element:'f64',mode:'bit_exact'}}];
const context={document:{getElementById:id=>el[id],querySelectorAll:()=>cards}};
vm.createContext(context); vm.runInContext(JSON.parse(code),context);
el['case-isa'].value='SSE'; el['case-isa'].handlers.change();
assert.equal(cards[0].hidden,false); assert.equal(cards[1].hidden,true);
el['case-isa'].value='SSE2'; el['case-isa'].handlers.change();
assert.equal(cards[0].hidden,true); assert.equal(cards[1].hidden,false);
el['case-search'].value='addpd'; el['case-search'].handlers.input(); assert.equal(el['case-count'].textContent,'1 / 2 cases');
el['case-reset'].handlers.click(); assert.equal(el['case-isa'].value,''); assert.ok(cards.every(c=>!c.hidden));
});'''
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(_CATALOG_SCRIPT),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
