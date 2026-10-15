"""Stored mismatch explanations preserve bits, counts, provenance, and usability."""

from __future__ import annotations

import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.mismatch_details import (
    _SCRIPT, build_mismatch_details, decode_bits, failure_path, family_explanation,
    lane_diff, render_mismatch_details,
)
from equilane.cases import CaseDefinition
from equilane.showcase import _case_cards, _quality_section


def finding(expected="0xffc00000", actual="0x7fc00000", *, element="f32", **changes):
    row = {
        "input": {"case_id": "sse2.add.f32x4.default", "input_id": "a" * 64,
                  "environment": {"rounding": "nearest_even"}, "operands": {}},
        "expected": {"return": {"element": element, "lanes": [expected]}},
        "actual": {"return": {"element": element, "lanes": [actual]}},
        "finding_family": "nan_indefinite_sign", "contract_scope": "official_nearest_even",
        "test_id": "example.test_output", "oracle": "literal_sse2_expected_bits",
        "oracle_reference": "tests/BUG_HUNT.md",
    }
    row.update(changes)
    return row


class BitDecodeTests(unittest.TestCase):
    def test_positive_and_negative_zero_preserve_the_sign_in_both_float_widths(self):
        for element, positive, negative in (("f32", "0x00000000", "0x80000000"),
                                             ("f64", "0x0000000000000000", "0x8000000000000000")):
            with self.subTest(element=element):
                a, b = decode_bits(positive, element), decode_bits(negative, element)
                self.assertEqual((a["kind"], b["kind"]), ("zero", "zero"))
                self.assertEqual((a["sign"], b["sign"]), (0, 1))
                self.assertIn("−0", b["label"])

    def test_nan_payload_does_not_include_the_quiet_bit(self):
        for element, quiet, signaling in (("f32", "0x7fc00001", "0x7f800001"),
                                            ("f64", "0xfff8000000000042", "0xfff0000000000042")):
            with self.subTest(element=element):
                a, b = decode_bits(quiet, element), decode_bits(signaling, element)
                self.assertEqual((a["kind"], b["kind"]), ("nan", "nan"))
                self.assertTrue(a["quiet"])
                self.assertFalse(b["quiet"])
                self.assertEqual(a["payload"], b["payload"])
                self.assertIn("signaling NaN", b["label"])

    def test_infinity_is_distinct_from_nan(self):
        for element, bits in (("f32", "0xff800000"), ("f64", "0x7ff0000000000000")):
            with self.subTest(element=element):
                value = decode_bits(bits, element)
                self.assertEqual(value["kind"], "infinity")
                self.assertNotIn("payload", value)

    def test_smallest_subnormal_and_smallest_normal_are_distinguished(self):
        for element, tiny, normal in (("f32", "0x00000001", "0x00800000"),
                                        ("f64", "0x0000000000000001", "0x0010000000000000")):
            with self.subTest(element=element):
                self.assertEqual(decode_bits(tiny, element)["kind"], "subnormal")
                self.assertEqual(decode_bits(normal, element)["kind"], "normal")

    def test_signed_integer_widths_use_twos_complement(self):
        for width in (8, 16, 32, 64):
            with self.subTest(width=width):
                bits = "0x" + "f" * (width // 4)
                self.assertEqual(decode_bits(bits, f"i{width}")["signed_value"], -1)
                self.assertEqual(decode_bits(bits, f"u{width}")["signed_value"], (1 << width) - 1)

    def test_malformed_or_untyped_values_are_not_guessed_as_floating_point(self):
        for value, element in ((None, "f32"), ([], "f32"), (True, "f32"), ("0x1", "f32"),
                               ("0x123456789", "f32"), ("0x7fg00000", "f32"),
                               ("0x7fc00000", None), ("0x7fc00000", []), ("0x7fc00000", "f16")):
            with self.subTest(value=value, element=element):
                self.assertEqual(decode_bits(value, element)["kind"], "unknown")

    def test_uppercase_hex_is_normalized_without_changing_the_value(self):
        decoded = decode_bits("0xFFC000AB", "f32")
        self.assertEqual(decoded["bits"], "0xffc000ab")
        self.assertEqual(decoded["payload"], 0xAB)


class LaneDifferenceTests(unittest.TestCase):
    def test_nan_sign_difference_identifies_only_the_sign_bit(self):
        lane = lane_diff(finding())[0]
        self.assertEqual(lane["xor"], "0x80000000")
        self.assertEqual(lane["changed_bits"], [31])
        self.assertEqual(lane["changed_bit_count"], 1)
        self.assertEqual(lane["classification"], "NaN の符号差")

    def test_nan_quiet_and_payload_differences_are_reported_separately(self):
        lane = lane_diff(finding("0x7f800001", "0x7fc00002"))[0]
        self.assertIn("quiet bit", lane["classification"])
        self.assertIn("payload", lane["classification"])
        self.assertEqual(lane["changed_bits"], [0, 1, 22])

    def test_signed_zero_is_not_displayed_as_a_numerical_nonzero_error(self):
        lane = lane_diff(finding("0x0000000000000000", "0x8000000000000000", element="f64"))[0]
        self.assertEqual(lane["classification"], "符号付きゼロの符号差")
        self.assertEqual(lane["changed_bits"], [63])

    def test_unchanged_vector_lanes_remain_visible_as_context(self):
        row = finding()
        row["expected"]["return"]["lanes"].append("0x3f800000")
        row["actual"]["return"]["lanes"].append("0x3f800000")
        lanes = lane_diff(row)
        self.assertEqual(len(lanes), 2)
        self.assertFalse(lanes[1]["changed"])
        self.assertEqual(lanes[1]["xor"], "0x00000000")
        self.assertEqual(lanes[1]["classification"], "一致")

    def test_missing_lanes_are_retained_without_inventing_a_zero_value(self):
        row = finding()
        row["actual"]["return"]["lanes"] = []
        lane = lane_diff(row)[0]
        self.assertEqual(lane["actual"], "〈欠落〉")
        self.assertIsNone(lane["xor"])
        self.assertIn("欠落", lane["classification"])

    def test_element_mismatch_is_visible_even_when_the_raw_bits_match(self):
        row = finding("0x00000000", "0x00000000")
        row["actual"]["return"]["element"] = "i32"
        lane = lane_diff(row)[0]
        self.assertEqual(lane["classification"], "要素型の差")
        self.assertTrue(lane["changed"])

    def test_scalar_result_includes_signed_integer_value_and_exact_xor(self):
        row = {"expected": {"return": {"element": "i32", "bits": "0xffffffff"}},
               "actual": {"return": {"element": "i32", "bits": "0xfffffffe"}}}
        lane = lane_diff(row)[0]
        self.assertEqual(lane["path"], "return.bits")
        self.assertEqual(lane["xor"], "0x00000001")
        self.assertIn("-1", lane["expected_class"])

    def test_memory_results_include_changed_bytes_and_length_differences(self):
        row = {"expected": {"buffers": {"dst": {"byte_offset": 0, "bytes": "0x001122"}}},
               "actual": {"buffers": {"dst": {"byte_offset": 0, "bytes": "0x00103344"}}}}
        lanes = lane_diff(row)
        self.assertEqual([lane["path"] for lane in lanes], ["buffers.dst.bytes[1]", "buffers.dst.bytes[2]", "buffers.dst.bytes[3]"])
        self.assertEqual(lanes[0]["xor"], "0x01")
        self.assertEqual(lanes[-1]["expected"], "〈欠落〉")

    def test_unknown_structure_and_missing_optional_values_are_kept(self):
        for row in ({}, None, {"expected": None, "actual": {}},
                    {"expected": {"fp_exceptions": []}, "actual": {"fp_exceptions": ["invalid"]}},
                    {"expected": {"return": {"lanes": None}}, "actual": {"return": {"lanes": "bad"}}}):
            with self.subTest(row=row):
                self.assertIsInstance(lane_diff(row), list)


class AggregationTests(unittest.TestCase):
    def test_wrapped_quality_and_standalone_runner_summaries_are_equivalent(self):
        summary = {"model_output_mismatches": [finding()], "execution_environment": {"machine": "arm64"}}
        self.assertEqual(build_mismatch_details(summary), build_mismatch_details({"gates": {"python_coverage": summary}}))

    def test_finding_count_and_unique_input_count_are_distinct_and_groups_conserve_rows(self):
        rows = [finding(), finding(finding_family="fp_boundary_nan_priority")]
        other = finding()
        other["input"]["input_id"] = "b" * 64
        other["input"]["environment"]["rounding"] = "toward_positive"
        other["contract_scope"] = "validated_rounding_extension"
        rows.append(other)
        built = build_mismatch_details({"model_output_mismatches": rows})
        self.assertEqual((built["finding_count"], built["unique_input_count"], built["case_count"]), (3, 2, 1))
        self.assertEqual(sum(group["finding_count"] for group in built["groups"]), 3)
        self.assertEqual(sum(built["families"].values()), 3)
        self.assertEqual(sum(built["failure_paths"].values()), 3)
        self.assertEqual(len(built["groups"]), 2)

    def test_mixed_signed_zero_family_is_split_by_contract_scope(self):
        self.assertEqual(failure_path("fp_boundary_signed_zero_infinity", "official_nearest_even"), "nan_sign")
        self.assertEqual(failure_path("fp_boundary_signed_zero_infinity", "validated_rounding_extension"), "rounding_arithmetic")
        self.assertEqual(failure_path("fp_boundary_signed_zero_infinity", "future_scope"), "fp_boundary_signed_zero_infinity")

    def test_declared_counts_are_compared_to_stored_rows_without_coercion(self):
        for declared, expected in ((1, True), (2, False), (True, None), ("1", None), (-1, None), (None, None)):
            with self.subTest(declared=declared):
                built = build_mismatch_details({"model_output_mismatches": [finding()],
                    "failure_classification": {"portable_model_output_mismatches": declared}})
                self.assertIs(built["counts_match"], expected)

    def test_malformed_rows_are_counted_and_kept_in_an_unknown_group(self):
        built = build_mismatch_details({"model_output_mismatches": [None, [], {}, finding()]})
        self.assertEqual(built["finding_count"], 4)
        self.assertEqual(built["malformed_record_count"], 3)
        self.assertEqual(built["missing_input_id_count"], 3)
        self.assertEqual(sum(group["finding_count"] for group in built["groups"]), 4)

    def test_missing_or_malformed_optional_collections_are_safe(self):
        for summary in (None, [], {}, {"gates": None}, {"model_output_mismatches": None},
                        {"model_output_mismatches": {}}, {"gates": {"python_coverage": []}}):
            with self.subTest(summary=summary):
                self.assertEqual(build_mismatch_details(summary)["finding_count"], 0)
                self.assertIn("不一致の詳細", render_mismatch_details(summary))

    def test_unknown_families_preserve_their_name_and_get_a_conservative_explanation(self):
        built = build_mismatch_details({"model_output_mismatches": [finding(finding_family="future-family")]})
        self.assertEqual(built["families"], {"future-family": 1})
        self.assertIn("原因を特定できません", family_explanation("future-family"))

    def test_building_details_does_not_mutate_the_original_evidence(self):
        original = {"model_output_mismatches": [finding()]}
        snapshot = copy.deepcopy(original)
        build_mismatch_details(original)
        render_mismatch_details(original)
        self.assertEqual(original, snapshot)

    def test_guide_mapping_preserves_actual_isa_instead_of_using_the_case_namespace(self):
        row = finding()
        built = build_mismatch_details({"model_output_mismatches": [row]})
        intrinsic = built["rows"][0]["intrinsic"]
        self.assertEqual(intrinsic["name"], "_mm_add_ps")
        self.assertEqual(intrinsic["isa"], "SSE")
        self.assertEqual(built["groups"][0]["intrinsic"], intrinsic)
        self.assertEqual(built["intrinsic_case_count"], 1)

    def test_guide_mapping_distinguishes_double_precision_and_conversion_intrinsics(self):
        for case_id, name in (("sse2.add.f64x2.default", "_mm_add_pd"),
                              ("sse2.cvt.f64x2.i32x4", "_mm_cvtpd_epi32")):
            with self.subTest(case_id=case_id):
                row = finding()
                row["input"]["case_id"] = case_id
                intrinsic = build_mismatch_details({"model_output_mismatches": [row]})["rows"][0]["intrinsic"]
                self.assertEqual(intrinsic["name"], name)
                self.assertEqual(intrinsic["isa"], "SSE2")

    def test_unrecognized_case_has_no_invented_guide_correspondence(self):
        row = finding()
        row["input"]["case_id"] = "custom.add.f32"
        self.assertIsNone(build_mismatch_details({"model_output_mismatches": [row]})["rows"][0]["intrinsic"])
        self.assertIn("Guide 対応：未記録", render_mismatch_details({"model_output_mismatches": [row]}))


class Structure(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids, self.anchors, self.external, self.findings, self.guide_links = [], [], [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if attrs.get("href", "").startswith("#"):
            self.anchors.append(attrs["href"][1:])
        for key in ("href", "src"):
            if attrs.get(key) and not attrs[key].startswith("#"):
                if (tag == "a" and key == "href" and
                    attrs[key].startswith("https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=")):
                    self.guide_links.append(attrs)
                else:
                    self.external.append(attrs[key])
        if attrs.get("class") == "finding-row":
            self.findings.append(attrs)


class DetailRenderingTests(unittest.TestCase):
    def test_report_makes_the_model_subject_and_host_architecture_distinct(self):
        html = render_mismatch_details({"model_output_mismatches": [finding()], "execution_environment": {"machine": "arm64"}})
        self.assertIn("共有する Python モデル", html)
        self.assertIn("実行ホスト", html)
        self.assertIn("arm64", html)
        self.assertIn("実機同士の不一致を示す記録ではありません", html)
        self.assertIn("原因数とは異なります", html)

    def test_xor_classifications_and_complete_original_record_are_rendered(self):
        row = finding(case_contract={"note": "extended contract remains visible"})
        html = render_mismatch_details({"model_output_mismatches": [row]})
        self.assertIn("0x80000000", html)
        self.assertIn("NaN の符号差", html)
        self.assertIn("extended contract remains visible", html)
        self.assertIn("固定 SSE2 ビット列", html)
        self.assertIn("実測値（Python モデル）", html)

    def test_hostile_fields_never_break_out_of_text_or_attributes(self):
        hostile = '\"><img src=https://evil.test onerror=alert(1)></script>'
        row = finding(finding_family=hostile, oracle=["malformed"])
        row["input"]["case_id"] = hostile
        row["input"]["input_id"] = hostile
        row["test_id"] = hostile
        html = render_mismatch_details({"model_output_mismatches": [row], "execution_environment": {"machine": hostile}})
        structure = Structure(html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertEqual(structure.external, [])
        self.assertEqual(len(structure.findings), 1)
        self.assertEqual(structure.findings[0]["data-case"], hostile)

    def test_intrinsic_name_is_searchable_and_guide_isa_is_explicit(self):
        html = render_mismatch_details({"model_output_mismatches": [finding()]})
        self.assertIn("_mm_add_ps", Structure(html).findings[0]["data-search"])
        self.assertIn("Guide ISA: <strong>SSE</strong>", html)
        self.assertIn("名前空間にも", html)
        self.assertIn("_mm_add_ps — sse2.add.f32x4.default", html)

    def test_calculation_explanation_is_rendered_and_all_explanatory_text_is_escaped(self):
        row = finding()
        explanation = {"title": "この入力の計算", "description": "1.5 を整数に変換します。",
                       "steps": ["floor 方向なら 1。モデルは 2。", "<img onerror=alert(1)>"]}
        with mock.patch("equilane.operation_explanation.explain_operation", return_value=explanation) as explain:
            html = render_mismatch_details({"model_output_mismatches": [row]})
        explain.assert_called_once_with(row)
        self.assertIn("floor 方向なら 1。モデルは 2。", html)
        self.assertIn("&lt;img onerror=alert(1)&gt;", html)
        self.assertNotIn("<img onerror", html)

    def test_guide_metadata_cannot_inject_markup_or_a_nonofficial_link(self):
        hostile = '<script>alert("guide")</script>'
        binding = {"name": hostile, "isa": hostile, "guide_url": "javascript:alert(1)",
                   "prototype": hostile, "header": hostile, "instructions": [hostile], "summary_ja": hostile}
        with mock.patch("equilane.mismatch_details.intrinsic_for_case", return_value=binding):
            html = render_mismatch_details({"model_output_mismatches": [finding()]})
        self.assertNotIn("javascript:alert", html)
        self.assertNotIn(hostile, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(Structure(html).external, [])

    def test_regular_showcase_catalog_cards_display_and_search_the_official_intrinsic(self):
        case = CaseDefinition("sse2.add.f32x4.default", {
            "description": "add lanes", "comparison": {"mode": "bit_exact"},
            "signature": {"arguments": [{"name": "a", "type": "vector", "element": "f32", "lanes": 4}],
                          "return": {"type": "vector", "element": "f32", "lanes": 4}},
            "intel": {"required_isa": ["sse2"]}, "openpower": {"required_isa": ["vsx"]},
        })
        html = _case_cards((case,), 8, ("openpower",))
        self.assertIn("_mm_add_ps SSE", html)
        self.assertIn("Guide ISA: <strong>SSE</strong>", html)
        self.assertIn("<h3>sse2.add.f32x4.default</h3>", html)
        self.assertTrue(Structure(html).guide_links)

    def test_scalar_double_sqrt_explains_the_reduced_framework_binding(self):
        row = finding()
        row["input"]["case_id"] = "sse2.sqrt.f64x2.scalar"
        html = render_mismatch_details({"model_output_mismatches": [row]})
        self.assertIn("_mm_sqrt_sd(a, a)", html)
        self.assertIn("このケースの呼び出し", html)
        self.assertIn("Guide のシグネチャ・命令との対応", html)
        self.assertIn("2引数", html)

    def test_one_finding_list_has_unique_ids_and_working_representative_anchors(self):
        html = render_mismatch_details({"model_output_mismatches": [finding(), finding(finding_family="future")]})
        structure = Structure(html)
        self.assertEqual(len(structure.findings), 2)
        self.assertEqual(len(structure.ids), len(set(structure.ids)))
        self.assertLessEqual(set(structure.anchors), set(structure.ids))
        self.assertIn("model-mismatch-details", structure.ids)
        self.assertEqual(structure.external, [])
        self.assertTrue(structure.guide_links)
        for link in structure.guide_links:
            self.assertEqual(link["target"], "_blank")
            self.assertEqual(link["rel"], "noopener noreferrer")

    def test_different_declared_count_and_missing_record_fields_show_warnings(self):
        html = render_mismatch_details({"model_output_mismatches": [None],
            "failure_classification": {"portable_model_output_mismatches": 4}})
        self.assertIn("一致しません", html)
        self.assertIn("必須情報が欠けた", html)
        self.assertIn("未記録", html)

    def test_nonzero_aggregate_without_details_does_not_claim_no_mismatches(self):
        html = render_mismatch_details({"failure_classification": {"portable_model_output_mismatches": 4}})
        self.assertIn("集計には 4 件", html)
        self.assertNotIn("不一致はありません", html)

    def test_showcase_uses_one_shared_detail_list_and_handles_corrupt_optional_metadata(self):
        python = {"model_output_mismatches": [finding()], "failure_classification": None,
                  "model_findings_by_contract_scope": None, "model_findings_by_family": [],
                  "model_oracle_references": 7, "test_execution_errors": [None, {}],
                  "test_assertion_failures": None}
        html = _quality_section({"status": "fail", "gates": {"python_coverage": python}})
        self.assertEqual(len(Structure(html).findings), 1)
        self.assertIn("独立したモデル検証と失敗箇所", html)
        self.assertIn("その他のテスト失敗・実行エラー", html)


@unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the finding filters")
class FindingBrowserTests(unittest.TestCase):
    def test_filters_paginate_normalize_unicode_and_reveal_representative_examples(self):
        harness = r"""
const assert=require('node:assert/strict'); const vm=require('node:vm'); const fs=require('node:fs');
const code=JSON.parse(fs.readFileSync(0,'utf8'));
const names=['search','family','case','rounding','scope','count','empty','previous','next','reset'];
const elements=Object.fromEntries(names.map(name=>[name,{value:'',hidden:false,disabled:false,textContent:'',handlers:{},addEventListener(name,fn){this.handlers[name]=fn;},focus(){this.focused=true;}}]));
const cards=Array.from({length:52},(_,index)=>({hidden:false,open:false,dataset:{search:`Case ＡＤＤ ${index}`,family:index%2?'odd':'even',case:index%2?'second':'first',rounding:index%2?'toward_zero':'nearest_even',scope:index%2?'extension':'official'}}));
const links=[{dataset:{findingIndex:'52'},handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}}];
const root={querySelector:selector=>elements[selector.replace('#finding-','')],querySelectorAll:selector=>selector==='.finding-row'?cards:links};
vm.runInNewContext(code,{document:{getElementById:id=>id==='model-mismatch-details'?root:null}});
const visible=()=>cards.filter(card=>!card.hidden);
assert.equal(visible().length,25); assert.equal(elements.previous.disabled,true); assert.equal(elements.next.disabled,false);
elements.next.handlers.click(); assert.equal(visible().length,25); assert.equal(cards[25].hidden,false);
elements.next.handlers.click(); assert.equal(visible().length,2); assert.equal(elements.next.disabled,true);
elements.family.value='odd';elements.family.handlers.change();assert.equal(visible().length,25);assert.ok(visible().every(card=>card.dataset.family==='odd'));
elements.search.value='add 51';elements.search.handlers.input();assert.equal(visible().length,1);assert.equal(cards[51].hidden,false);
elements.scope.value='official';elements.scope.handlers.change();assert.equal(visible().length,0);assert.equal(elements.empty.hidden,false);
elements.reset.handlers.click();assert.equal(visible().length,25);assert.equal(elements.search.focused,true);assert.equal(elements.scope.value,'');
links[0].handlers.click();assert.equal(cards[51].hidden,false);assert.equal(cards[51].open,true);assert.equal(visible().length,2);
assert.ok(elements.count.textContent.includes('52 / 52'));assert.ok(elements.count.textContent.includes('3 / 3'));
"""
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(_SCRIPT),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_missing_renderer_root_is_a_safe_noop(self):
        result = subprocess.run([shutil.which("node"), "-e", "const vm=require('node:vm');const fs=require('node:fs');vm.runInNewContext(JSON.parse(fs.readFileSync(0,'utf8')),{document:{getElementById:()=>null}});"],
                                input=json.dumps(_SCRIPT), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
