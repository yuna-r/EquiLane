"""Readable execution reports preserve every observed difference and its scope."""
from __future__ import annotations

import copy
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("execution_report_html", ROOT / "tools/render_execution_report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.language = None
        self.data_parts = []
        self.in_data = False
        self.links = []
        self.scripts = []
        self.text = path.read_text(encoding="utf-8")
        self.feed(self.text)
        self.data = json.loads("".join(self.data_parts))

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "html":
            self.language = values["lang"]
        if tag == "a":
            self.links.append(values["href"])
        if tag == "script":
            self.scripts.append(values)
            self.in_data = values.get("id") == "report-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, text):
        if self.in_data:
            self.data_parts.append(text)


class ExecutionReportHTMLTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.destination = self.root / "source", self.root / "html"
        self.source.mkdir()
        self.raw_name, self.cpu_name, self.match_name = "_mm256_rcp_ps", "_mm512_test_epi32_mask", "_mm_add_ps"
        self.private = "PRIVATE_NOT_COMPARABLE_RECORD_MUST_NOT_BE_EMBEDDED"
        self.catalog = {"byte_order": "little", "intrinsics": [self.case(name) for name in (self.raw_name, self.cpu_name, self.match_name)]}
        self.inputs = [hashlib.sha256(f"input-{i}".encode()).hexdigest() for i in range(42)]
        intel = [{"name": self.raw_name if i < 40 else self.cpu_name if i == 40 else self.match_name,
                  "input_id": identifier, "status": "unsupported_cpu" if i == 40 else "ok"}
                 for i, identifier in enumerate(self.inputs)]
        self.summary = {
            "candidate_count": 3, "distinct_inputs": 42,
            "catalog": self.write("inputs/catalog.json", self.catalog),
            "roles": {"intel": {"results": self.write_lines("intel/results.jsonl", intel),
                                  "unsupported_candidates": 1, "executed_candidates": 2}},
            "comparisons": {}, "guide_scope": {"distinct_api_names": 7126},
            "assembled_at": "2026-11-09T22:33:01+00:00", "source_commit": "e" * 40,
            "private_environment": "/private/host/path/must/not/leak",
        }
        for role, count in zip(report.ROLES, (40, 39, 38)):
            comparison = {"target": role, "cases": [
                {"name": self.raw_name, "matched": 40 - count, "mismatched": count, "not_comparable": 0},
                {"name": self.cpu_name, "matched": 0, "mismatched": 0, "not_comparable": 1},
                {"name": self.match_name, "matched": 1, "mismatched": 0, "not_comparable": 0},
            ]}
            rows = [self.difference(i, role) for i in range(count)]
            rows.append({"name": self.cpu_name, "outcome": "not_comparable", "secret": self.private})
            self.summary["comparisons"][role] = {
                "summary": self.write(f"compare-{role}/summary.json", comparison),
                "differences": self.write_lines(f"compare-{role}/differences.jsonl", rows),
                "raw_byte_differences": count, "matched_inputs": 41 - count, "not_comparable_inputs": 1,
                "fp_flag_differences_diagnostic_only": 3,
                "review_candidates": {"approximate_reciprocal_or_rsqrt": {self.raw_name: count},
                                      "undefined_widening_casts": {self.match_name: 1}},
            }

    @staticmethod
    def case(name):
        return {"name": name, "input_byte_length": 8, "output_byte_length": 8,
                "arguments": [{"name": key, "guide_type": "unsigned int", "offset": i * 4, "byte_length": 4}
                              for i, key in enumerate(("a", "b"))],
                "return": {"guide_type": "unsigned long long"}, "cpuid": ["SSE2", "AVX"]}

    def metadata(self, name, records=None):
        raw = (self.source / name).read_bytes()
        value = {"file": name, "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if records is not None:
            value["record_count"] = records
        return value

    def write(self, name, value):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return self.metadata(name)

    def write_lines(self, name, rows):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return self.metadata(name, len(rows))

    def difference(self, index, role):
        encoded = "0x" + index.to_bytes(4, "little").hex() + "0100807f"
        common = {"input_id": self.inputs[index], "name": self.raw_name, "input": encoded, "status": "ok"}
        return {"name": self.raw_name, "input_id": self.inputs[index], "outcome": "mismatch",
                "input": {"name": self.raw_name, "input": encoded, "rounding": "nearest_even"},
                "intel": dict(common, output="0x000000800100c07f", fp_flags=0),
                "target": dict(common, output="0x000000000100807f", fp_flags=17, runner=role)}

    def render(self):
        return report.render_reports(self.source, self.destination, self.summary)

    def page(self, name="index.html"):
        return Page(self.destination / name)

    def mutate_difference(self, change):
        overview = self.summary["comparisons"]["arm"]
        name = overview["differences"]["file"]
        rows = [json.loads(line) for line in (self.source / name).read_text().splitlines()]
        change(rows)
        overview["differences"] = self.write_lines(name, rows)

    def test_all_eight_pages_have_correct_languages_and_local_navigation(self):
        outcome = self.render()
        expected = {prefix + filename for prefix in ("", "arm/", "openpower/", "riscv/")
                    for filename in ("index.html", "index.en.html")}
        self.assertEqual({item["file"] for item in outcome["files"]}, expected)
        for name in expected:
            with self.subTest(page=name):
                page = self.page(name)
                self.assertEqual(page.language, "en" if ".en." in name else "ja")
                self.assertEqual(page.data["candidate_count"], 3)
                self.assertEqual(len(page.data["cases"]), 3)
                self.assertEqual(len(page.scripts), 2)
                self.assertTrue(all("src" not in script for script in page.scripts))
                self.assertFalse(re.search(r"<(?:link|iframe)\b", page.text))
        self.assertIn("arm/index.html", self.page().links)
        self.assertIn("../index.en.html", self.page("arm/index.en.html").links)

    def test_all_raw_rows_are_embedded_with_exact_bytes_and_no_display_cap(self):
        outcome = self.render()
        self.assertEqual(outcome["embedded_raw_differences"], {"arm": 40, "openpower": 39, "riscv": 38})
        root = self.page().data
        for role, count in zip(report.ROLES, (40, 39, 38)):
            with self.subTest(role=role):
                rows = root["raw"][role][self.raw_name]
                self.assertEqual(len(rows), count)
                for i, row in enumerate(rows):
                    original = self.difference(i, role)
                    self.assertEqual(row, [self.inputs[i], original["input"]["input"],
                                           original["intel"]["output"], original["target"]["output"],
                                           0, 17, "nearest_even"])
                cpu_page = self.page(role + "/index.html").data
                self.assertEqual(set(cpu_page["raw"]), {role})
                self.assertEqual(cpu_page["raw"][role], root["raw"][role])
        self.assertIn("rows.forEach((row,i)=>detail.append(record(c,row,i)))", report.JS)
        self.assertNotIn("rows.slice", report.JS)

    def test_named_arguments_prototype_and_guide_match_the_execution_layout(self):
        self.render()
        case = self.page().data["cases"][0]
        self.assertEqual(case["arguments"], [["a", "unsigned int", 0, 4], ["b", "unsigned int", 4, 4]])
        self.assertEqual(case["prototype"], "unsigned long long _mm256_rcp_ps(unsigned int a, unsigned int b);")
        self.assertEqual(case["guide"], report.GUIDE + self.raw_name)
        row = self.page().data["raw"]["arm"][self.raw_name][-1]
        encoded = bytes.fromhex(row[1][2:])
        self.assertEqual(encoded[:4], (39).to_bytes(4, "little"))
        self.assertEqual(encoded[4:], bytes.fromhex("0100807f"))

    def test_noncomparable_records_and_host_metadata_are_not_embedded(self):
        self.render()
        for name in ("index.html", "index.en.html", "arm/index.html"):
            page = self.page(name)
            self.assertNotIn(self.private, page.text)
            self.assertNotIn(self.summary["private_environment"], page.text)
            unsupported = next(case for case in page.data["cases"] if case["name"] == self.cpu_name)
            self.assertEqual(unsupported["intel_status"], {"unsupported_cpu": 1})
            self.assertEqual(unsupported["counts"]["arm"], [0, 0, 1])
            self.assertNotIn(self.cpu_name, page.data["raw"]["arm"])
        english = self.page("index.en.html").text
        self.assertIn("remain unverified", english)
        self.assertIn("FP flags are diagnostic", english)
        self.assertIn("undefined upper lanes", english)
        self.assertIn("allowed error", english)

    def test_english_pages_do_not_contain_japanese_text(self):
        self.render()
        for name in ("index.en.html", "arm/index.en.html", "openpower/index.en.html", "riscv/index.en.html"):
            with self.subTest(page=name):
                self.assertIsNone(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", self.page(name).text))

    def test_downloads_point_to_the_published_compressed_streams(self):
        self.render()
        self.assertIn("execution-summary.json", self.page().links)
        for role in report.ROLES:
            links = self.page(role + "/index.en.html").links
            for target in ("../data/inputs.jsonl.gz", "../data/results-intel.jsonl.gz",
                           f"../data/results-{role}.jsonl.gz", "differences.jsonl.gz", "summary.json"):
                self.assertIn(target, links)

    def test_script_terminators_in_catalog_text_are_inert_and_preserved(self):
        attack = "unsigned int </script><script>alert(1)</script>&\u2028"
        self.catalog["intrinsics"][0]["arguments"][0]["guide_type"] = attack
        self.summary["catalog"] = self.write("inputs/catalog.json", self.catalog)
        self.render()
        page = self.page()
        self.assertEqual(len(page.scripts), 2)
        self.assertIn(attack, page.data["cases"][0]["prototype"])
        self.assertNotIn(attack, page.text)
        self.assertIn(r"\u003c/script\u003e", page.text)

    def test_approximate_and_undefined_review_notes_do_not_rewrite_counts(self):
        self.render()
        cases = {case["name"]: case for case in self.page().data["cases"]}
        self.assertTrue(cases[self.raw_name]["notes"]["arm"]["approximate"])
        self.assertTrue(cases[self.match_name]["notes"]["arm"]["undefined"])
        self.assertEqual(cases[self.raw_name]["counts"]["arm"], [0, 40, 0])
        self.assertEqual(cases[self.match_name]["counts"]["arm"], [1, 0, 0])

    def test_tampered_sources_are_rejected_before_any_html_is_written(self):
        path = self.source / self.summary["catalog"]["file"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.render()
        self.assertFalse(self.destination.exists())

    def test_source_path_traversal_and_symlinks_are_rejected(self):
        for name in ("../outside.json", "/tmp/outside.json"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                report._source(self.source.resolve(), name)
        original = self.source / self.summary["catalog"]["file"]
        other = self.root / "outside.json"
        other.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.render()

    def test_inconsistent_counts_are_rejected(self):
        self.summary["comparisons"]["arm"]["raw_byte_differences"] -= 1
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            self.render()

    def test_nonmatching_raw_identity_is_rejected(self):
        self.mutate_difference(lambda rows: rows[0]["target"].update(input="0x" + "00" * 8))
        with self.assertRaisesRegex(ValueError, "input or function mismatch"):
            self.render()

    def test_identical_returns_cannot_be_presented_as_a_raw_difference(self):
        self.mutate_difference(lambda rows: rows[0]["target"].update(output=rows[0]["intel"]["output"]))
        with self.assertRaisesRegex(ValueError, "identical outputs"):
            self.render()

    def test_input_and_output_byte_lengths_cannot_be_truncated(self):
        for side, key in (("target", "output"), ("input", "input")):
            with self.subTest(side=side, field=key):
                changed = copy.deepcopy(self.summary)
                original = (self.source / changed["comparisons"]["arm"]["differences"]["file"]).read_bytes()
                self.mutate_difference(lambda rows: rows[0][side].update({key: "0x00"}))
                with self.assertRaisesRegex(ValueError, "raw-byte"):
                    self.render()
                (self.source / changed["comparisons"]["arm"]["differences"]["file"]).write_bytes(original)
                self.summary = changed

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_search_filters_selection_and_lazy_raw_rows_execute(self):
        self.render()
        data = self.page("index.en.html").data
        fixture = self.root / "data.json"
        fixture.write_text(json.dumps(data), encoding="utf-8")
        path = self.root / "interaction.js"
        # A small DOM adapter runs the actual shipped script without a network
        # or browser dependency. Assertions concern visible user interactions.
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.events={};this.value='';this._text='';this.classList={toggle:()=>{}};}
 set textContent(v){this._text=String(v);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;this._text='';}
 setAttribute(k,v){this[k]=v;}
 addEventListener(k,f){(this.events[k]||=[]).push(f);}
 fire(k){for(const f of this.events[k]||[])f();}
}
const data=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),ids=new Map();
for(const id of ['report-data','function-list','detail','search','more','list-meta'])ids.set(id,new Element('div'));
ids.get('report-data').textContent=JSON.stringify(data);
const filters=['all','diff','cpu'].map(kind=>{const e=new Element('button');e.dataset.filter=kind;return e;});
const document={getElementById:id=>ids.get(id),createElement:t=>new Element(t),createTextNode:s=>{const e=new Element('#text');e.textContent=s;return e;},querySelectorAll:()=>filters};
vm.runInNewContext(fs.readFileSync(process.argv[3],'utf8'),{document,Intl,Map,JSON,history:{replaceState:()=>{}},location:{hash:''}});
const list=ids.get('function-list'),detail=ids.get('detail'),search=ids.get('search');
const descend=(n,p)=>[...(p(n)?[n]:[]),...n.children.flatMap(c=>descend(c,p))];
assert.equal(list.children.length,1);
assert.equal(list.children[0].dataset.name,'_mm256_rcp_ps');
assert.equal(descend(detail,n=>n.className==='raw-record').length,40);
let records=descend(detail,n=>n.className==='raw-record');
assert.ok(records[0].textContent.includes('0x000000800100c07f'));
assert.ok(!records[1].textContent.includes('0x000000800100c07f'));
records[1].open=true;records[1].fire('toggle');
assert.ok(records[1].textContent.includes('0x000000800100c07f'));
assert.ok(records[1].textContent.includes('0x000000000100807f'));
assert.ok(records[1].textContent.includes('FP flags (diagnostic)'));
assert.ok(records[1].textContent.includes('0x01000000'));
filters[0].fire('click');assert.equal(list.children.length,3);
search.value='_mm_add';search.fire('input');assert.equal(list.children.length,1);assert.equal(list.children[0].dataset.name,'_mm_add_ps');
search.value='avx';search.fire('input');assert.equal(list.children.length,3);
search.value='unsigned long long';search.fire('input');assert.equal(list.children.length,3);
search.value='';search.fire('input');filters[2].fire('click');assert.equal(list.children.length,1);
list.children[0].fire('click');assert.ok(detail.textContent.includes('port correctness remains unverified'));
assert.equal(descend(detail,n=>n.className==='raw-record').length,0);
filters[0].fire('click');search.value='_mm_add';search.fire('input');list.children[0].fire('click');
assert.ok(detail.textContent.includes('No returned-byte differences'));
assert.ok(detail.textContent.includes('undefined upper lanes'));
search.value='missing';search.fire('input');assert.ok(list.textContent.includes('No functions match'));
console.log('search/filter/selection/lazy/raw bytes: OK');
"""
        path.write_text(harness, encoding="utf-8")
        script = self.root / "report.js"
        script.write_text(report.JS, encoding="utf-8")
        checked = subprocess.run([shutil.which("node"), str(path), str(fixture), str(script)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_inline_javascript_has_valid_syntax(self):
        path = self.root / "report.js"
        path.write_text(report.JS, encoding="utf-8")
        checked = subprocess.run([shutil.which("node"), "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
