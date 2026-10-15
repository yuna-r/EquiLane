from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.webui import render_dashboard  # noqa: E402


class DashboardMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: dict[str, dict[str, str | None]] = {}
        self.labels: set[str] = set()
        self.resources: list[str] = []
        self.script = ""
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids[attributes["id"]] = attributes
        if tag == "label" and "for" in attributes:
            self.labels.add(attributes["for"])
        for attribute in ("src", "href"):
            if attributes.get(attribute) and not attributes[attribute].startswith("data:image/"):
                self.resources.append(attributes[attribute])
        self.in_script = tag == "script" or self.in_script

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.script += data


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = render_dashboard()
        self.markup = DashboardMarkup()
        self.markup.feed(self.html)

    def test_dashboard_is_standalone_and_has_a_report_first_empty_state(self) -> None:
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn("EquiLane · Report studio", self.html)
        self.assertNotIn("{{MASCOT}}", self.html)
        self.assertEqual(self.markup.resources, [])
        self.assertIn("最初のレポートを、どうぞ。", self.html)
        self.assertIn("@media (max-width:540px)", self.html)
        self.assertIn("prefers-reduced-motion:reduce", self.html)
        self.assertIn("<noscript>", self.html)

    def test_form_labels_defaults_and_limits_match_the_local_api(self) -> None:
        self.assertEqual(self.markup.labels, {"profile", "count", "jobs", "quality", "coverage",
                                             "target-openpower", "target-arm", "target-riscv"})
        self.assertLessEqual(self.markup.labels, self.markup.ids.keys())
        self.assertEqual(self.markup.ids["count"]["value"], "8")
        self.assertEqual(self.markup.ids["count"]["min"], "1")
        self.assertEqual(self.markup.ids["count"]["max"], "100000")
        self.assertIn("disabled", self.markup.ids["check-controls"])
        self.assertNotIn("checked", self.markup.ids["quality"])
        self.assertNotIn("checked", self.markup.ids["coverage"])
        self.assertIn("disabled", self.markup.ids["coverage"])
        self.assertIn("checked", self.markup.ids["target-openpower"])
        self.assertNotIn("checked", self.markup.ids["target-arm"])
        self.assertNotIn("checked", self.markup.ids["target-riscv"])
        self.assertEqual(self.markup.ids["coverage"]["aria-describedby"], "coverage-hint")
        self.assertIn("実機によるCPU間検証とは区別", self.html)
        self.assertIn("経路同士の一致とは別", self.html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the coverage interaction check")
    def test_coverage_stays_opt_in_and_requires_quality_in_the_post_payload(self) -> None:
        start = self.markup.script.index("    const syncCoverage = () => {")
        end = self.markup.script.index("    const connect = async () => {", start)
        snippet = self.markup.script[start:end]
        harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = JSON.parse(fs.readFileSync(0, 'utf8'));
const ids = ['quality','coverage','profile','count','count-hint','jobs','check-form','api-notice',
             'target-openpower','target-arm','target-riscv'];
const elements = Object.fromEntries(ids.map(id => [id, {
  checked: false, disabled: false, value: '', textContent: '', handlers: {},
  addEventListener(name, callback) { this.handlers[name] = callback; }, focus() {}
}]));
elements.profile.value = 'smoke'; elements.count.value = '8'; elements.jobs.value = '1';
elements['target-openpower'].checked = true;
const state = {busy: false, submitting: false}; const payloads = [];
const context = {
  el: id => elements[id], state, defaults: {smoke: 8, standard: 1000, stress: 100000},
  targetRoles: ['openpower','arm','riscv'],
  notice() {}, jobMessage() {}, setBusy(value) {state.busy = value;},
  async refreshStatus() {state.busy = false;},
  async request(path, options) {assert.equal(path, '/api/check'); payloads.push(JSON.parse(options.body));}
};
vm.createContext(context); vm.runInContext(code, context);
(async () => {
  assert.equal(elements.coverage.disabled, true); assert.equal(elements.coverage.checked, false);
  const event = {preventDefault() {}};
  await elements['check-form'].handlers.submit(event);
  assert.equal(payloads.at(-1).quality, false); assert.equal(payloads.at(-1).coverage, false);
  assert.deepEqual(payloads.at(-1).targets, ['openpower']);
  elements.quality.checked = true; elements.quality.handlers.change();
  assert.equal(elements.coverage.disabled, false); assert.equal(elements.coverage.checked, false);
  await elements['check-form'].handlers.submit(event);
  assert.equal(payloads.at(-1).quality, true); assert.equal(payloads.at(-1).coverage, false);
  elements.coverage.checked = true;
  await elements['check-form'].handlers.submit(event);
  assert.equal(payloads.at(-1).quality, true); assert.equal(payloads.at(-1).coverage, true);
  elements.quality.checked = false; elements.quality.handlers.change();
  assert.equal(elements.coverage.checked, false); assert.equal(elements.coverage.disabled, true);
  elements.coverage.checked = true;
  await elements['check-form'].handlers.submit(event);
  assert.equal(payloads.at(-1).coverage, false);
  elements['target-arm'].checked = true; elements['target-riscv'].checked = true;
  await elements['check-form'].handlers.submit(event);
  assert.deepEqual(payloads.at(-1).targets, ['openpower','arm','riscv']);
  elements['target-openpower'].checked = false;
  await elements['check-form'].handlers.submit(event);
  assert.deepEqual(payloads.at(-1).targets, ['arm','riscv']);
  elements['target-arm'].checked = false; elements['target-riscv'].checked = false;
  const before = payloads.length;
  await elements['check-form'].handlers.submit(event);
  assert.equal(payloads.length, before);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=json.dumps(snippet), text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_cui_output_is_in_the_main_pane_and_keyboard_accessible(self) -> None:
        self.assertLess(self.html.index('class="report-toolbar"'), self.html.index('id="live-output"'))
        self.assertLess(self.html.index('id="live-output"'), self.html.index('id="report-frame"'))
        self.assertIn("hidden", self.markup.ids["live-output"])
        self.assertEqual(self.markup.ids["job-output"]["aria-live"], "off")
        self.assertEqual(self.markup.ids["job-output"]["tabindex"], "0")
        self.assertEqual(self.markup.ids["follow-output"]["aria-controls"], "job-output")
        self.assertIn("LIVE OUTPUT", self.html)
        self.assertIn("CUIの出力を、そのまま表示", self.html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the history count check")
    def test_history_distinguishes_unique_inputs_from_multiple_target_pairs(self):
        start = self.markup.script.index("    const reportCounts = report => {")
        end = self.markup.script.index("    const renderHistory = () => {", start)
        snippet = self.markup.script[start:end]
        harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {number: value => Number(value).toLocaleString('ja-JP')};
vm.createContext(context); vm.runInContext(code + '\nglobalThis.counts = reportCounts;', context);
assert.equal(context.counts({record_count:6096,unique_input_count:2032,targets:['openpower','arm','riscv']}), '2,032 inputs · 6,096 pairs');
assert.equal(context.counts({record_count:6096,targets:['openpower','arm','riscv']}), '2,032 inputs · 6,096 pairs');
assert.equal(context.counts({record_count:2032,targets:['arm']}), '2,032 inputs');
assert.equal(context.counts({record_count:2032}), '2,032 inputs');
"""
        completed = subprocess.run([shutil.which("node"), "-e", harness],
                                   input=json.dumps(snippet), text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the output interaction check")
    def test_output_keeps_raw_text_completed_logs_and_reader_scroll_position(self) -> None:
        start = self.markup.script.index("    const updateOutput = job => {")
        end = self.markup.script.index("    const stageText = value => {", start)
        snippet = self.markup.script[start:end]
        harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = JSON.parse(fs.readFileSync(0, 'utf8'));
const ids = ['live-output', 'job-output', 'output-state', 'output-placeholder', 'follow-output'];
const elements = Object.fromEntries(ids.map(id => [id, {
  textContent: '', hidden: true, open: false, dataset: {}, handlers: {},
  scrollHeight: 600, clientHeight: 180, top: 420,
  get scrollTop() { return this.top; },
  set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  addEventListener(name, callback) { this.handlers[name] = callback; }
}]));
const context = {el: id => elements[id], state: {outputJob: null, followOutput: true}};
vm.createContext(context);
vm.runInContext(code + '\nglobalThis.update = updateOutput;', context);
const raw = '[1/7] Generate inputs\n[████░░░░] 50%\n<script>alert("raw")</script>';
context.update({id: 'one', state: 'running', output: raw});
assert.equal(elements['job-output'].textContent, raw);
assert.equal(elements['job-output'].scrollTop, 420);
assert.equal(elements['live-output'].open, true);
assert.equal(elements['output-placeholder'].hidden, true);
assert.equal(elements['output-state'].textContent, '実行中');
elements['job-output'].scrollTop = 15;
elements['job-output'].handlers.scroll();
elements['job-output'].scrollHeight = 900;
context.update({id: 'one', state: 'running', output: raw + '\nnext line'});
assert.equal(elements['job-output'].scrollTop, 15);
elements['live-output'].open = false;
context.update({id: 'one', state: 'running', output: raw + '\nmore output'});
assert.equal(elements['live-output'].open, false);
assert.equal(elements['job-output'].scrollTop, 15);
elements['follow-output'].handlers.click();
assert.equal(elements['job-output'].scrollTop, 720);
const completed = raw + '\nMatched inputs: 2,032; model mismatches: 171\n';
context.update({id: 'one', state: 'completed', output: completed});
assert.equal(elements['job-output'].textContent, completed);
assert.equal(elements['live-output'].open, false);
assert.equal(elements['output-state'].textContent, '実行終了');
context.update(null);
assert.equal(elements['job-output'].textContent, completed);
context.update({id: 'two', state: 'running', output: ''});
assert.equal(elements['live-output'].open, true);
assert.equal(elements['job-output'].textContent, '');
assert.equal(elements['output-placeholder'].hidden, false);
assert.equal(elements['job-output'].hidden, true);
context.update({id: 'two', state: 'failed', output: 'actual stderr\n'});
assert.equal(elements['job-output'].textContent, 'actual stderr\n');
assert.equal(elements['output-state'].textContent, 'エラーで終了');
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=json.dumps(snippet), text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_report_frame_is_isolated_and_loading_never_reloads_the_page(self) -> None:
        self.assertEqual(self.markup.ids["report-frame"]["sandbox"], "allow-scripts")
        self.assertIn("title", self.markup.ids["report-frame"])
        self.assertEqual(self.markup.ids["open-report"]["rel"], "noopener noreferrer")
        self.assertIn("download", self.markup.ids["download-report"])
        self.assertIn("url.origin !== location.origin", self.markup.script)
        self.assertIn("url.pathname.startsWith('/reports/')", self.markup.script)
        self.assertNotIn("innerHTML", self.markup.script)
        self.assertNotIn("location.reload", self.markup.script)
        self.assertNotIn("document.write", self.markup.script)
        self.assertEqual(self.markup.ids["api-notice"]["role"], "alert")
        self.assertEqual(self.markup.ids["job-card"]["aria-live"], "polite")


if __name__ == "__main__":
    unittest.main()
