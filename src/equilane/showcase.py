"""Self-contained, non-normative HTML showcase report."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable, Mapping

from .canonical import JSONValue, atomic_write, dumps
from .cases import CaseDefinition
from .mascot import mascot_markup
from .metrics import collect_verification_metrics


_STYLES = """
    :root {
      --paper: #faf7f0;
      --panel: #fffdf9;
      --line: #e6dfec;
      --violet: #70538f;
      --mint: #e5f2e9;
      --rose: #f8e5e9;
      --text: #393244;
      --muted: #696273;
      --ok: #27664e;
      --amber: #815915;
      --red: #a83049;
      --display: "Avenir Next", "Hiragino Sans", "Yu Gothic", system-ui, sans-serif;
      --mono: "SFMono-Regular", "Cascadia Code", Consolas, monospace;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    [id] { scroll-margin-top: 84px; }
    html { background: var(--paper); scroll-behavior: smooth; }
    body {
      margin: 0;
      min-width: 320px;
      color: var(--text);
      background: radial-gradient(ellipse at 9% 4%, #f3e8f2 0, transparent 38rem), var(--paper);
      font: 14px/1.65 var(--display);
    }
    .shell { width: min(1160px, calc(100% - 48px)); margin: 0 auto; padding: 30px 0 40px; }
    .chrome { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 5px 22px; color: var(--muted); font: 10px/1.6 var(--mono); }
    .chrome-left { display: flex; align-items: center; gap: 10px; }
    .brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 12px; background: #e9def3; color: var(--violet); font: 800 25px/1 var(--display); }
    .brand-name { font: 750 17px/1 var(--display); color: var(--text); }
    .report-nav { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; position: sticky; top: 10px; z-index: 5; margin: 0 0 18px; padding: 10px; border: 1px solid var(--line); border-radius: 18px; background: #fffdf9f5; }
    .report-nav a { padding: 6px 12px; border-radius: 10px; color: var(--violet); text-decoration: none; font-size: 11px; font-weight: 650; }
    .report-nav a:hover { background: #f1eaf8; }
    .report-nav a:focus-visible, input:focus-visible, select:focus-visible, button:focus-visible { outline: 3px solid var(--violet); outline-offset: 3px; }
    .report-nav .nav-quality { margin-left: auto; background: #f8e5e9; color: var(--red); }
    .report-nav .nav-quality[data-status="pass"] { background: var(--mint); color: var(--ok); }
    .report-nav .nav-quality[data-status="unrecorded"] { background: #fcf2df; color: var(--amber); }
    .hero { position: relative; display: grid; grid-template-columns: 1.2fr .8fr; align-items: center; gap: 28px; min-height: 360px; padding: 44px 50px; background: var(--panel); border: 1px solid var(--line); border-radius: 30px; overflow: hidden; }
    .hero::before { content: ""; position: absolute; width: 330px; height: 330px; right: -100px; top: -110px; border-radius: 50%; background: #eef3e8; }
    .hero > * { position: relative; }
    .eyebrow, .kicker { color: var(--violet); font-size: 11px; font-weight: 750; letter-spacing: .13em; text-transform: uppercase; }
    h1 { margin: 12px 0 16px; font: 800 clamp(48px, 6.7vw, 82px)/1.03 var(--display); letter-spacing: -.055em; }
    h1 span { display: block; margin-top: 14px; color: var(--violet); font-size: clamp(17px, 2.1vw, 23px); font-weight: 650; letter-spacing: -.025em; }
    .lede { max-width: 570px; margin: 0; color: var(--muted); font-size: 14px; line-height: 1.8; }
    .status-line { display: inline-flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-top: 24px; padding: 10px 14px; border: 1px solid #bddac9; border-radius: 12px; background: var(--mint); color: var(--ok); font: 700 11px/1.6 var(--mono); }
    .status-line::before { content: "✓"; font: 800 16px/1 var(--display); }
    .mismatch .status-line { border-color: #edb9c3; background: var(--rose); color: var(--red); }
    .mismatch .status-line::before { content: "!"; }
    .incomplete .status-line { border-color: #e5d0a4; background: #fbefd6; color: var(--amber); }
    .incomplete .status-line::before { content: "?"; }
    .core-wrap { position: relative; display: grid; justify-items: center; gap: 5px; padding: 8px; }
    .mascot { width: min(270px, 100%); height: auto; animation: float 6s ease-in-out infinite; }
    @keyframes float { 50% { transform: translateY(-5px) rotate(1deg); } }
    .core-label { display: flex; align-items: center; gap: 13px; padding: 12px 20px; border: 1px solid #d8c8e8; border-radius: 18px; background: #f1eaf8; color: var(--violet); }
    .core-label strong { font: 800 34px/1 var(--display); letter-spacing: -.04em; }
    .core-label span { max-width: 90px; font: 650 9px/1.55 var(--mono); }
    .core-note { margin: 8px 0 0; color: var(--muted); font-size: 10px; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
    .metric { padding: 20px 24px; border: 1px solid var(--line); border-radius: 20px; background: var(--panel); }
    .metric:nth-child(2) { background: #f0eaf7; }
    .metric:nth-child(3) { background: #eaf4ee; }
    .metric:nth-child(4) { background: #fff0e2; }
    .metric strong { display: block; font: 800 clamp(27px, 3.5vw, 38px)/1.2 var(--display); letter-spacing: -.03em; }
    .metric span { display: block; margin-top: 7px; color: var(--muted); font-size: 11px; }
    .metrics[data-status="mismatch"] .metric:last-child { border-color: #edb9c3; background: var(--rose); }
    .load-spectrum-section { margin-top: 28px; border: 1px solid var(--line); border-radius: 26px; background: var(--panel); overflow: hidden; }
    .load-spectrum-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, .46fr); }
    .density-ledger { padding: 30px; }
    h2 { margin: 8px 0 0; font: 750 clamp(23px, 3.2vw, 34px)/1.25 var(--display); letter-spacing: -.035em; }
    .density-copy { max-width: 630px; margin: 14px 0 18px; color: var(--muted); font-size: 12px; line-height: 1.8; }
    .density-evidence { display: inline-block; margin-bottom: 18px; padding: 6px 10px; border: 1px solid #e5d0a4; border-radius: 8px; color: var(--amber); background: #fcf2df; font: 650 9px/1.4 var(--mono); }
    .load-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .load-cell { padding: 16px; border: 1px solid #ded6e8; border-radius: 15px; background: #f3edf8; }
    .load-cell:nth-child(2), .load-cell:nth-child(6) { background: #edf4ee; border-color: #d1e2d5; }
    .load-cell:nth-child(4) { background: #fcf0e4; border-color: #ecdbc7; }
    .load-cell:nth-child(5) { background: #eef0f8; }
    .load-cell.primary { grid-column: 1 / -1; background: #edf4ee; border-color: #d1e2d5; }
    .load-cell strong { display: block; margin-top: 8px; font: 800 clamp(25px, 3vw, 35px)/1.2 var(--display); letter-spacing: -.035em; }
    .load-cell.primary strong { color: var(--ok); font-size: 44px; }
    .load-cell span { color: #5f4a79; font: 650 9px/1.6 var(--mono); }
    .load-cell small { display: block; margin-top: 7px; color: var(--muted); font-size: 10px; line-height: 1.6; }
    .load-cell.divergence[data-status="mismatch"] { background: var(--rose); border-color: #edb9c3; }
    .load-cell.divergence[data-status="mismatch"] strong { color: var(--red); }
    .density-note { margin: 18px 0 0; padding-left: 12px; border-left: 3px solid #dac9a6; color: var(--muted); font-size: 10px; line-height: 1.75; }
    .load-chart-panel { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 30px 24px; background: #f0eaf6; border-left: 1px solid var(--line); }
    .load-chart-label { color: #5f4a79; font-size: 11px; font-weight: 650; text-align: center; }
    .load-profile { padding: 5px 11px; border: 1px solid #d7c5e8; border-radius: 20px; background: #fffdf9; color: var(--violet); font: 10px/1.5 var(--mono); }
    .load-chart { --load-level: 0%; width: min(300px, 100%); margin: 15px auto 0; }
    .load-chart-frame { --plot-height: 300px; height: var(--plot-height); display: grid; grid-template-columns: 44px minmax(0, 1fr); }
    .load-y-axis { position: relative; border-right: 1px solid #a590bc; }
    .load-axis-label { position: absolute; right: 9px; bottom: var(--level); transform: translateY(50%); color: #695579; font: 9px/1 var(--mono); }
    .load-plot { position: relative; border-bottom: 1px solid #a590bc; overflow: hidden; background: #faf6fc; border-radius: 14px 14px 0 0; }
    .load-gridline { position: absolute; right: 0; bottom: var(--level); left: 0; border-top: 1px dashed #d9cee3; }
    .load-bar { position: absolute; bottom: 0; left: 23%; width: 42%; height: var(--load-level); border-radius: 10px 10px 0 0; background: linear-gradient(to top, #a7d1cb 0%, #bdddbb 38%, #efdba5 70%, #e7b590 100%); background-position: bottom; background-size: 100% var(--plot-height); }
    .load-marker { position: absolute; right: 0; bottom: var(--load-level); left: 0; border-top: 2px solid #70538f; }
    .load-marker span { position: absolute; top: 7px; right: 4px; padding: 3px 6px; border-radius: 5px; background: #eee4f6; color: #5f4a79; font: 9px/1.5 var(--mono); }
    .load-x-label { margin-top: 12px; color: var(--muted); font: 9px/1.5 var(--mono); text-align: center; }
    .load-readout { text-align: center; }
    .load-readout strong { display: block; color: var(--violet); font: 800 35px/1.3 var(--display); }
    .load-readout span { display: block; color: var(--muted); font: 9px/1.5 var(--mono); }
    .load-baseline { color: #695579; font-size: 10px; line-height: 1.7; text-align: center; }
    .load-spectrum-key { width: min(190px, 100%); height: 7px; margin-top: auto; border-radius: 5px; background: linear-gradient(90deg, #a7d1cb, #bdddbb, #efdba5, #e7b590); }
    section { margin-top: 32px; }
    .section-head { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin: 0 4px 16px; }
    .section-code { color: var(--muted); font: 9px/1.7 var(--mono); text-align: right; overflow-wrap: anywhere; }
    .split { display: grid; grid-template-columns: 1.15fr .85fr; gap: 14px; }
    .panel { padding: 26px; border: 1px solid var(--line); border-radius: 22px; background: var(--panel); min-width: 0; }
    .panel-label { color: var(--violet); font: 650 10px/1.5 var(--mono); }
    .routes { display: grid; grid-template-columns: minmax(0, 1fr) 42px minmax(0, 1fr); align-items: center; gap: 10px; }
    .route-node { padding: 20px 16px; border: 1px solid #d1e2d5; border-radius: 16px; background: #edf4ee; }
    .route-node:last-child { background: #f3edf8; border-color: #ded6e8; }
    .route-node b { display: block; margin-bottom: 7px; font-size: 18px; }
    .route-node small { color: var(--muted); font: 9px/1.7 var(--mono); overflow-wrap: anywhere; }
    .route-beam { display: grid; place-items: center; }
    .route-beam::after { content: "≡"; display: grid; place-items: center; width: 35px; height: 35px; border: 1px dashed #b8a3cc; border-radius: 50%; color: var(--violet); font-size: 24px; }
    .matrix { display: grid; gap: 10px; margin-top: 18px; }
    .matrix-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 14px; padding-bottom: 10px; border-bottom: 1px dashed #e6dfec; font-size: 11px; }
    .matrix-row span { color: var(--muted); overflow-wrap: anywhere; }
    .matrix-row b { color: var(--text); font-family: var(--mono); overflow-wrap: anywhere; }
    .matrix-row[data-status="pass"] b { color: var(--ok); }
    .matrix-row[data-status="fail"] b, .matrix-row[data-status="mismatch"] b { color: var(--red); }
    .case-filters { display: grid; grid-template-columns: 1.7fr 1fr 1fr auto; gap: 10px; align-items: end; margin-bottom: 12px; padding: 18px; border: 1px solid var(--line); border-radius: 18px; background: var(--panel); }
    .filter-field { display: grid; gap: 6px; min-width: 0; }
    .filter-field label { color: var(--muted); font-size: 10px; font-weight: 650; }
    input, select, button { min-height: 40px; width: 100%; border: 1px solid #d7c9e3; border-radius: 10px; background: #fff; color: var(--text); font: 12px/1.5 var(--display); padding: 8px 10px; }
    input::placeholder { color: #77717e; }
    select { padding-right: 20px; }
    button { width: auto; cursor: pointer; background: #f1eaf8; color: #5f4a79; font-weight: 650; }
    button:hover { background: #e9def3; }
    .catalog-note { margin: 0 4px 15px; color: var(--muted); font-size: 11px; }
    .case-count { color: var(--violet); font: 650 12px/1.5 var(--mono); }
    .empty-state { padding: 30px; border: 1px dashed #bda7d0; border-radius: 20px; background: #f3edf8; color: #5f4a79; text-align: center; }
    .case-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .case-card { padding: 24px; border: 1px solid var(--line); border-radius: 22px; background: var(--panel); min-width: 0; }
    .case-card:nth-child(4n+2), .case-card:nth-child(4n+3) { background: #f7f2fb; }
    .case-index { display: inline-block; padding: 4px 9px; border-radius: 20px; background: #eae0f4; color: var(--violet); font: 650 9px/1.5 var(--mono); }
    .case-card h3 { margin: 14px 0 8px; font: 750 20px/1.25 var(--display); letter-spacing: -.02em; overflow-wrap: anywhere; }
    .case-card p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
    .signature { margin-top: 16px; padding: 10px 12px; border: 1px solid #d1e2d5; border-radius: 10px; background: #edf4ee; color: #355b49; font: 10px/1.7 var(--mono); overflow-wrap: anywhere; }
    .case-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
    .case-meta span { padding: 4px 7px; border: 1px solid #e1d9e8; border-radius: 7px; background: #fffdf9; color: #625570; font: 8px/1.5 var(--mono); }
    .evidence { display: grid; grid-template-columns: .8fr 1.2fr; gap: 14px; }
    .evidence-state { display: flex; flex-direction: column; justify-content: space-between; gap: 18px; background: #fcf2df; border-color: #e8d8b6; }
    .evidence-state strong { display: block; margin: 13px 0 10px; font: 750 24px/1.25 var(--display); color: var(--amber); }
    .evidence-state p { margin: 0; color: #745d39; font-size: 12px; line-height: 1.75; }
    .stamp { align-self: flex-start; padding: 6px 9px; border: 1px dashed #bda071; border-radius: 8px; color: #745d39; font: 9px/1.5 var(--mono); }
    .hashes { display: grid; gap: 16px; }
    .hash { padding-bottom: 14px; border-bottom: 1px dashed var(--line); }
    .hash:last-child { border: 0; padding-bottom: 0; }
    .hash span { display: block; margin-bottom: 6px; color: var(--violet); font: 650 9px/1.5 var(--mono); }
    .hash code { display: block; color: var(--muted); font: 10px/1.7 var(--mono); overflow-wrap: anywhere; }
    footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 15px 26px; margin-top: 34px; padding: 20px 4px 0; border-top: 1px dashed #d6cbdc; color: var(--muted); font: 9px/1.7 var(--mono); }
    footer span:nth-child(2) { max-width: 420px; }
    .oracle-report { margin-top: 22px; border-color: #e8d8b6; background: #fffaf0; }
    .oracle-report[data-status="fail"] { border-color: #edb9c3; background: #fff5f6; }
    .oracle-report[data-status="fail"] > .kicker, .oracle-report[data-status="fail"] > h2 { color: var(--red); }
    .oracle-report p { font-size: 12px; line-height: 1.85; overflow-wrap: anywhere; }
    .oracle-report h3 { font-size: 17px; line-height: 1.6; }
    .oracle-report .matrix { max-width: 780px; }
    details { margin-top: 16px; padding: 14px 16px; border: 1px solid #e6d4dc; border-radius: 12px; background: var(--panel); }
    summary { cursor: pointer; font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }
    summary:focus-visible { outline: 3px solid #70538f; outline-offset: 5px; border-radius: 3px; }
    .oracle-report pre { padding: 12px; border-radius: 9px; background: #f4eef5; white-space: pre-wrap; overflow-wrap: anywhere; font: 11px/1.7 var(--mono); }
    .oracle-report small, code { font-family: var(--mono); overflow-wrap: anywhere; }
    @media (max-width: 820px) {
      .hero { padding: 30px; gap: 18px; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .load-spectrum-layout, .split, .evidence { grid-template-columns: 1fr; }
      .load-chart-panel { border-top: 1px solid var(--line); border-left: 0; }
      .load-chart { width: min(280px, 100%); }
      .case-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 580px) {
      .shell { width: min(1160px, calc(100% - 24px)); padding-top: 18px; }
      .chrome { align-items: flex-start; flex-direction: column; gap: 9px; }
      .chrome-left { flex-wrap: wrap; }
      .hero { grid-template-columns: 1fr; padding: 28px 22px; border-radius: 22px; gap: 16px; }
      .core-wrap { padding-top: 0; }
      .mascot { width: 190px; }
      .metric { padding: 17px; }
      .density-ledger, .panel, .case-card { padding: 22px; }
      .case-grid { grid-template-columns: 1fr; }
      .report-nav { position: static; padding: 7px; gap: 2px; }
      .report-nav a { padding: 6px 9px; }
      .report-nav .nav-quality { margin-left: 0; }
      .case-filters { grid-template-columns: 1fr; }
      .case-filters button { justify-self: start; }
      .load-chart-frame { --plot-height: 250px; }
      .section-head { align-items: flex-start; flex-direction: column; gap: 10px; }
      .section-code { text-align: left; }
      .routes { grid-template-columns: 1fr; justify-items: stretch; }
      .route-beam { height: 34px; }
      .matrix-row { gap: 9px; }
      .matrix-row b { max-width: 120px; }
      footer { flex-direction: column; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation: none !important; scroll-behavior: auto !important; }
    }
    @media print {
      body { background: white; }
      .shell { width: 100%; padding: 0; }
      .hero, .panel, .case-card, .metric, .load-cell { break-inside: avoid; box-shadow: none; }
      .mascot { animation: none; }
      .report-nav { position: static; }
      .case-filters { display: none; }
      .load-spectrum-layout { grid-template-columns: 1fr 260px; }
      details { break-inside: avoid; }
    }
"""





_CATALOG_SCRIPT = """
(() => {
  const form = document.getElementById("case-filters");
  const search = document.getElementById("case-search");
  const type = document.getElementById("case-type");
  const mode = document.getElementById("case-mode");
  const count = document.getElementById("case-count");
  const empty = document.getElementById("case-empty");
  const reset = document.getElementById("case-reset");
  const cards = Array.from(document.querySelectorAll(".case-card"));
  const normalize = text => text.normalize("NFKC").toLowerCase();
  const searchable = cards.map(card => normalize(card.dataset.search));
  const update = () => {
    const terms = normalize(search.value).trim().split(/\\s+/).filter(Boolean);
    let visible = 0;
    cards.forEach((card, index) => {
      const matches = terms.every(term => searchable[index].includes(term)) &&
        (!type.value || card.dataset.element === type.value) &&
        (!mode.value || card.dataset.mode === mode.value);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    count.textContent = `${visible} / ${cards.length} cases`;
    empty.hidden = visible !== 0;
  };
  form.addEventListener("submit", event => event.preventDefault());
  search.addEventListener("input", update);
  type.addEventListener("change", update);
  mode.addEventListener("change", update);
  reset.addEventListener("click", () => {
    search.value = "";
    type.value = "";
    mode.value = "";
    update();
    search.focus();
  });
  update();
})();
"""


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _shape(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown"
    kind = str(value.get("type", "unknown"))
    element = str(value.get("element", ""))
    if kind == "vector":
        return f"{element} × {value.get('lanes', '?')}"
    if element:
        return f"{kind} / {element}"
    return kind


def _signature(case: CaseDefinition) -> str:
    signature = case.signature
    arguments = signature["arguments"]
    assert isinstance(arguments, list)
    argument_shapes = []
    for argument in arguments:
        assert isinstance(argument, dict)
        argument_shapes.append(f"{argument['name']}: {_shape(argument)}")
    return f"{' · '.join(argument_shapes)} → {_shape(signature['return'])}"


def _case_element(case: CaseDefinition) -> str:
    result = case.signature["return"]
    assert isinstance(result, dict)
    return str(result.get("element", result.get("type", "unknown")))


def _filter_options(values: Iterable[str]) -> str:
    return "".join(
        f'<option value="{_text(value)}">{_text(value)}</option>'
        for value in sorted(set(values))
    )


def _case_cards(cases: tuple[CaseDefinition, ...], vectors_each: int) -> str:
    cards: list[str] = []
    for index, case in enumerate(cases, start=1):
        description = case.data["description"]
        comparison = case.comparison["mode"]
        intel = case.data["intel"]
        openpower = case.data["openpower"]
        assert isinstance(intel, dict) and isinstance(openpower, dict)
        intel_isa = intel["required_isa"]
        power_isa = openpower["required_isa"]
        assert isinstance(intel_isa, list) and isinstance(power_isa, list)
        isa = " / ".join(str(item).upper() for item in intel_isa + power_isa)
        searchable = f"{case.id} {description} {_signature(case)} {comparison} {isa}"
        cards.append(
            f"""
            <article class="case-card" data-search="{_text(searchable)}" data-mode="{_text(comparison)}" data-element="{_text(_case_element(case))}">
              <div class="case-index">CASE // {index:02d}</div>
              <h3>{_text(case.id)}</h3>
              <p>{_text(description)}</p>
              <div class="signature">{_text(_signature(case))}</div>
              <div class="case-meta">
                <span>{_text(str(comparison).upper())}</span>
                <span>{vectors_each} VECTORS</span>
                <span>{_text(isa)}</span>
              </div>
            </article>"""
        )
    return "".join(cards)


def _matrix_row(label: object, value: object, status: object = "") -> str:
    return (
        f'<div class="matrix-row" data-status="{_text(status)}">'
        f'<span>{_text(label)}</span><b>{_text(value)}</b></div>'
    )


def _finding_status(value: object) -> str:
    if isinstance(value, int):
        return "fail" if value else "pass"
    return "unrecorded"


def _quality_section(quality: Mapping[str, JSONValue] | None) -> str:
    if quality is None:
        return '''<section class="panel oracle-report" id="model-oracle" data-status="unrecorded">
          <div class="kicker">Independent model check / not run</div>
          <h2>独立したモデル検証：未実施</h2>
          <p>このレポートの一致率は両経路の比較結果です。同じ Python モデルを使う
          fixture 同士の一致だけでは、モデルの正しさは確認できません。
          <code>--quality --showcase-report</code> で独立した期待値との照合結果を表示できます。</p></section>'''
    gates = quality.get("gates", {})
    if not isinstance(gates, dict):
        gates = {}
    python = gates.get("python_coverage", {})
    if not isinstance(python, dict):
        python = {}
    classification = python.get("failure_classification", {})
    rows = python.get("model_output_mismatches", [])
    environment = python.get("execution_environment", {})
    scopes = python.get("model_findings_by_contract_scope", {})
    oracle_reference = python.get("model_oracle_reference") or {}
    oracle_references = python.get("model_oracle_references") or [oracle_reference]
    coverage_label = (
        f'{python.get("coverage_percent", "未記録")}%'
        if python.get("coverage_collected") is True else
        "未計測（既定）" if python.get("coverage_collected") is False else "未記録"
    )
    reference_details = "".join(
        f'<details><summary>{_text(reference.get("oracle", "独立した期待値"))}</summary>'
        f'<pre>{_text(dumps(reference))}</pre></details>'
        for reference in oracle_references if isinstance(reference, dict) and reference
    )
    family_labels = {
        "nan_indefinite_sign": "NaN の符号",
        "nan_payload_priority": "NaN payload の選択",
        "rounding_arithmetic": "丸めモード未反映：算術",
        "rounding_sqrt": "丸めモード未反映：平方根",
        "rounding_float_to_integer": "丸めモード未反映：浮動小数点 → 整数",
        "rounding_narrowing": "丸めモード未反映：f64 → f32",
        "rounding_integer_to_float": "丸めモード未反映：整数 → 浮動小数点",
        "fp_boundary_float_to_integer": "浮動小数点 → 整数の境界",
        "fp_boundary_halfway_arithmetic": "算術の丸め中間値と隣接値",
        "fp_boundary_integer_to_float": "整数 → 浮動小数点の精度境界",
        "fp_boundary_nan_priority": "NaN payload と入力順序",
        "fp_boundary_narrowing": "f64 → f32 の丸め・桁落ち境界",
        "fp_boundary_signed_zero_infinity": "符号付きゼロ・無限大・無効演算",
        "fp_boundary_sqrt": "平方根の丸め境界",
        "fp_boundary_subnormal_arithmetic": "非正規数と通常数の境界",
    }
    family_rows = "".join(
        _matrix_row(family_labels.get(name, name), count, _finding_status(count))
        for name, count in sorted(python.get("model_findings_by_family", {}).items())
    )
    findings = []
    for row in rows:
        record = row["input"]
        oracle_label = {
            "literal_sse2_expected_bits": "固定 SSE2 ビット列",
            "captured_sse2_expected_bits": "SSE2 命令の記録値",
            "independent_integer_reference": "独立した整数演算",
        }.get(row.get("oracle", "literal_sse2_expected_bits"), "独立した期待値")
        findings.append(f'''<details><summary>{_text(record["case_id"])} — {_text(record["input_id"])}</summary>
          <p>検証範囲：{_text(row.get("contract_scope", "未記録"))}。
          環境指定：{_text(dumps(record.get("environment", {})))}。</p>
          <p>入力</p><pre>{_text(dumps(record))}</pre>
          <p>期待値（{_text(oracle_label)}）</p><pre>{_text(dumps(row["expected"]))}</pre>
          <p>実測値（Python モデル）</p><pre>{_text(dumps(row["actual"]))}</pre>
          <small>{_text(row["test_id"])} / {_text(row.get("oracle_reference", "未記録"))}</small></details>''')
    other_findings = "".join(
        f'<details><summary>{_text(row["test_id"])}</summary>'
        f'<p>{_text(row["exception"])}</p><pre>{_text(row["message"])}</pre></details>'
        for name in ("test_assertion_failures", "test_execution_errors")
        for row in python.get(name, [])
    )
    counts = "".join(
        _matrix_row(label, classification.get(key, "未集計"),
                    _finding_status(classification.get(key)))
        for key, label in (
            ("portable_model_output_mismatches", "モデル出力と期待値の不一致（入力パターン数）"),
            ("other_assertion_failures", "その他のテスト assertion 失敗"),
            ("test_execution_errors", "テスト実行エラー"),
        )
    )
    explanation = '''<p><strong>今回の不一致の対象は Python portable model の出力です。</strong>
      contract 検証とモデル実行が成功した後、比較エンジン <code>equilane.compare</code> を
      通さず、出力を独立した期待値と直接照合して検出しました。
      比較・集計処理の誤判定として検出されたものではありません。</p>
      <p>OpenPOWER 実機の不具合を示す結果ではありません。モデルはこのリポジトリに含まれる
      開発用実装です。この結果だけでフレームワーク全体にバグがないとは判断しません。</p>''' if rows else '''<p>モデル出力の不一致はこの実行では記録されていません。
      実施件数・その他の失敗・実行エラーも併せて確認してください。</p>'''
    gate_rows = "".join(
        _matrix_row(name, gate.get("status", "unknown"), gate.get("status", "unknown"))
        for name, gate in gates.items() if isinstance(gate, dict)
    )
    status = "pass" if quality.get("status") == "pass" else "fail"
    return f'''<section class="panel oracle-report" id="model-oracle" data-status="{status}">
      <div class="kicker">Independent model oracle / failure attribution</div>
      <h2>独立したモデル検証と失敗箇所</h2>
      {explanation}<div class="matrix">{counts}</div>
      <details class="oracle-context"><summary>検証範囲・期待値の出典・品質ゲート</summary>
        <h3>検証範囲と不一致の内訳</h3>
        <p>公式 nearest-even 契約での不一致：{_text(scopes.get("official_nearest_even", "未集計"))}件。
        丸めモードを追加した検証用契約での不一致：{_text(scopes.get("validated_rounding_extension", "未集計"))}件。
        即値の許可範囲を広げた検証用契約での不一致：{_text(scopes.get("validated_immediate_extension", "未集計"))}件。</p>
        <p>公式ケースの対応モードと即値の許可範囲は変更していません。拡張した契約も検証を通してから実行し、
        各不一致のJSONに保存しています。件数は失敗した入力パターン数で、根本原因の個数とは異なります。</p>
        <div class="matrix">{family_rows}</div>
        <p>モデル oracle テスト実施数：{_text(python.get("model_oracle_tests_run", "未集計"))} メソッド。
        独立した出力照合：{_text(python.get("model_oracle_checks_run", "未集計"))}件。
        入力IDを記録した重複のない入力：{_text(python.get("model_oracle_tracked_unique_inputs", "未集計"))}件。
        カバレッジ：{_text(coverage_label)}。
        実行環境：{_text(dumps(environment))}。</p>
        <h3>期待値の出典</h3>{reference_details}
        <p>固定ビット列、独立した整数演算、保存したSSE2実行結果を使用します。
        取得と再確認の手順は <code>tests/BUG_HUNT.md</code> に記載しています。
        この quality 実行では probe を再実行していません。実機間の native evidence ではありません。</p>
        <div class="matrix">{gate_rows}</div>
      </details>
      <details><summary>不一致の入力・期待値・実測値（{len(rows)}件）</summary>{"".join(findings)}</details>
      <details><summary>その他のテスト失敗・実行エラー</summary>{other_findings}</details>
      <p>機械可読の根拠：<code>quality/summary.json</code> の
      <code>gates.python_coverage</code>。入力ID、テストID、期待値、実測値、実行環境、
      検証に使ったソースの SHA-256 を保存しています。</p>
    </section>'''

def render_showcase_html(
    *,
    cases: Iterable[CaseDefinition],
    summary: Mapping[str, JSONValue],
    profile: str,
    seed: str,
    vector_sha256: str,
    case_definitions_sha256: str,
    isa_contract_sha256: str,
    generated_at: datetime,
    native_evidence: bool,
    quality: Mapping[str, JSONValue] | None = None,
) -> str:
    """Render a portable report. All supplied text is escaped before insertion."""

    ordered_cases = tuple(cases)
    metrics = collect_verification_metrics(ordered_cases, summary)
    case_count = metrics.case_count
    record_count = metrics.trials
    matched = metrics.matched_inputs
    mismatched = metrics.mismatched_inputs
    not_comparable = metrics.not_comparable_inputs
    mismatch_atoms = metrics.mismatch_atoms
    outcome = str(summary["outcome"])
    rate_text = f"{metrics.match_rate:.2f}".rstrip("0").rstrip(".")
    vectors_each = metrics.vectors_per_case
    path_executions = metrics.implementation_path_evaluations
    lane_positions = metrics.lane_verdicts
    paired_bit_positions = metrics.bit_positions
    bit_exact_cases = sum(
        case.comparison["mode"] == "bit_exact" for case in ordered_cases
    )
    load_percent = max(0.0, min(100.0, vectors_each / 1000 * 100))
    load_text = f"{load_percent:.2f}".rstrip("0").rstrip(".")
    meter_value = min(vectors_each, 1000)
    generated_text = generated_at.strftime("%Y-%m-%d // %H:%M:%S UTC")
    report_id = case_definitions_sha256[:12].upper()
    status_class = {
        "pass": "pass",
        "mismatch": "mismatch",
        "not_comparable": "incomplete",
    }.get(outcome, "incomplete")
    status_title = {
        "pass": "PAIRED COMPARISON PASSED",
        "mismatch": "COMPARISON MISMATCH",
        "not_comparable": "COMPARISON INCOMPLETE",
    }.get(outcome, outcome.upper())
    status_copy = {
        "pass": "Every observed lane completed the equivalence comparison without divergence.",
        "mismatch": "At least one observed lane returned a divergent result.",
        "not_comparable": "One or more signals could not be compared safely.",
    }.get(outcome, "Verification sequence completed.")
    headline_outcome = outcome
    if quality is not None and quality.get("status") != "pass":
        status_class = "mismatch"
        status_title = "QUALITY CHECK FAILED"
        headline_outcome = "quality_failed"
        status_copy = f"Paired comparison: {outcome}. Independent quality checks failed; see failure attribution."
    quality_section = _quality_section(quality)
    quality_status = "unrecorded" if quality is None else (
        "pass" if quality.get("status") == "pass" else "fail"
    )
    quality_nav_label = {
        "unrecorded": "Model check: not run",
        "pass": "Quality checks: pass",
        "fail": "Quality checks: failed",
    }[quality_status]
    evidence_label = "NATIVE EVIDENCE" if native_evidence else "DEVELOPMENT SIMULATION"
    evidence_copy = (
        "Results were captured from architecture-specific native runners."
        if native_evidence
        else "Portable development fixtures were used. This is not CPU-native evidence."
    )
    lane_metric_label = (
        "LANE VERDICTS" if outcome == "pass" else "MATRIX LANE POSITIONS"
    )
    output_metric_label = (
        "BIT-EXACT POSITIONS"
        if outcome == "pass" and case_count > 0 and bit_exact_cases == case_count
        else "PAIRED OUTPUT-BIT POSITIONS"
    )
    intel_path_copy = (
        "x86_64 // NATIVE RUNNER"
        if native_evidence
        else "SSE2 SEMANTICS // DEV FIXTURE"
    )
    power_path_copy = (
        "ppc64le // NATIVE RUNNER"
        if native_evidence
        else "VSX SEMANTICS // DEV FIXTURE"
    )
    observatory_label = (
        "CROSS-ARCHITECTURE OBSERVATORY"
        if native_evidence
        else "SEMANTIC EQUIVALENCE OBSERVATORY"
    )
    hero_copy = (
        "The same deterministic inputs crossed two native architectures. Compare their observed outputs, then follow the evidence below."
        if native_evidence
        else "The same deterministic inputs crossed paired development fixtures. Compare their observed outputs, then follow the evidence below."
    )
    trial_label = (
        "DETERMINISTIC CROSS-ARCH TRIALS"
        if native_evidence
        else "DETERMINISTIC PAIRED-FIXTURE TRIALS"
    )
    path_activity_label = (
        "PATH EXECUTIONS" if native_evidence else "FIXTURE PATH EVALUATIONS"
    )
    cards = _case_cards(ordered_cases, vectors_each)
    element_options = _filter_options(_case_element(case) for case in ordered_cases)
    comparison_options = _filter_options(str(case.comparison["mode"]) for case in ordered_cases)

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>EquiLane · {_text(status_title)}</title>
  <style>{_STYLES}
  </style>
</head>
<body class="{_text(status_class)}">
  <main class="shell">
    <div class="chrome">
      <div class="chrome-left"><span class="brand-mark" aria-hidden="true">≡</span><span class="brand-name">EquiLane</span><span>{_text(observatory_label)}</span></div>
      <div>REPORT {_text(report_id)} // {_text(generated_text)}</div>
    </div>

    <nav class="report-nav" aria-label="Report sections">
      <a href="#overview">Overview</a><a href="#workload">Workload</a>
      <a href="#paths">Paths</a><a href="#cases">Cases</a><a href="#evidence">Evidence</a>
      <a class="nav-quality" data-status="{quality_status}" href="#model-oracle">{quality_nav_label}</a>
    </nav>

    <header class="hero" id="overview">
      <div>
        <div class="eyebrow">A little care for every lane</div>
        <h1>EquiLane <span>Same inputs. Clear answers.</span></h1>
        <p class="lede">{_text(hero_copy)}</p>
        <div class="status-line">{_text(status_title)} // {_text(headline_outcome.upper())}</div>
      </div>
      <div class="core-wrap" aria-label="Paired comparison match rate">
        {mascot_markup()}
        <div class="core-label"><strong>{_text(rate_text)}%</strong><span>PAIRED COMPARISON MATCH RATE</span></div>
        <p class="core-note">Independent model checks are reported separately.</p>
      </div>
    </header>

    {quality_section}

    <div class="metrics" data-status="{_text(outcome)}">
      <div class="metric" data-code="A-01"><strong>{case_count}</strong><span>Intrinsic cases</span></div>
      <div class="metric" data-code="V-02"><strong>{record_count}</strong><span>Input records</span></div>
      <div class="metric" data-code="M-03"><strong>{matched}</strong><span>Matched inputs</span></div>
      <div class="metric" data-code="D-04"><strong>{mismatched + not_comparable}</strong><span>Mismatch / not comparable</span></div>
    </div>

    <section class="load-spectrum-section" id="workload" aria-labelledby="load-spectrum-title">
      <div class="load-spectrum-layout">
        <div class="density-ledger">
          <div class="kicker">Test load spectrum</div>
          <h2 class="density-title" id="load-spectrum-title">Verification density</h2>
          <p class="density-copy">A deterministic workload was evaluated across paired paths, then resolved at lane and bit-position granularity. These counts are derived from the canonical record stream and declared return-vector shapes.</p>
          <div class="density-evidence">{_text(evidence_label)}</div>
          <div class="load-grid">
            <div class="load-cell primary" data-channel="LOAD-01">
              <span>{_text(trial_label)}</span>
              <strong>{record_count:,}</strong>
              <small>Canonical input records presented to the paired equivalence comparison</small>
            </div>
            <div class="load-cell" data-channel="PATH-02">
              <span>{_text(path_activity_label)}</span>
              <strong>{path_executions:,}</strong>
              <small>Two implementation-path observations per deterministic trial</small>
            </div>
            <div class="load-cell" data-channel="LANE-03">
              <span>{_text(lane_metric_label)}</span>
              <strong>{lane_positions:,}</strong>
              <small>Declared return lanes multiplied by vectors per case</small>
            </div>
            <div class="load-cell" data-channel="BIT-04">
              <span>{_text(output_metric_label)}</span>
              <strong>{paired_bit_positions:,}</strong>
              <small>Observed output bits compared as paired positions at declared element width</small>
            </div>
            <div class="load-cell" data-channel="MODE-05">
              <span>Bit-exact contracts</span>
              <strong>{bit_exact_cases:,}</strong>
              <small>Cases whose comparison mode requires exact output representation</small>
            </div>
            <div class="load-cell divergence" data-channel="DIV-00" data-status="{_text(outcome)}">
              <span>Divergence atoms</span>
              <strong>{mismatch_atoms:,}</strong>
              <small>{not_comparable:,} non-comparable inputs reported independently</small>
            </div>
          </div>
          <p class="density-note">Workload counts do not expand the evidence boundary. The capture classification above identifies whether these observations came from native runners or development fixtures.</p>
        </div>
        <aside class="load-chart-panel" aria-label="Test load spectrum graph">
          <div class="load-chart-label">Standard load scale // 1,000 vectors per case</div>
          <div class="load-profile">Profile // {_text(str(profile).upper())}</div>
          <div class="load-chart" role="meter" aria-label="Verification density by vectors per case" aria-valuemin="0" aria-valuemax="1000" aria-valuenow="{meter_value}" aria-valuetext="{vectors_each:,} vectors per case; standard load scale {_text(load_text)} percent" style="--load-level:{_text(load_text)}%">
            <div class="load-chart-frame">
              <div class="load-y-axis" aria-hidden="true">
                <span class="load-axis-label" style="--level:100%">1000</span>
                <span class="load-axis-label" style="--level:75%">750</span>
                <span class="load-axis-label" style="--level:50%">500</span>
                <span class="load-axis-label" style="--level:25%">250</span>
                <span class="load-axis-label" style="--level:0%">0</span>
              </div>
              <div class="load-plot" aria-hidden="true">
                <i class="load-gridline" style="--level:100%"></i>
                <i class="load-gridline" style="--level:75%"></i>
                <i class="load-gridline" style="--level:50%"></i>
                <i class="load-gridline" style="--level:25%"></i>
                <i class="load-gridline" style="--level:0%"></i>
                <div class="load-bar"></div>
                <div class="load-marker"><span>ACTUAL // {vectors_each:,}</span></div>
              </div>
            </div>
            <div class="load-x-label">VECTORS / CASE</div>
          </div>
          <div class="load-readout"><strong>{vectors_each:,}</strong><span>ACTUAL VECTORS / CASE</span></div>
          <div class="load-baseline">Load density {_text(load_text)}% // 1,000 vectors per case = standard</div>
          <div class="load-spectrum-key" aria-hidden="true"></div>
          <div class="load-baseline">Mint → sage → butter → peach load spectrum<br>Failure indication remains red</div>
        </aside>
      </div>
    </section>

    <section id="paths">
      <div class="section-head">
        <div><div class="kicker">Two paths, one input</div><h2>Dual-path verification</h2></div>
        <div class="section-code">PROFILE {_text(str(profile).upper())}<br>SEED {_text(seed)}</div>
      </div>
      <div class="split">
        <div class="panel routes">
          <div class="route-node"><b>INTEL</b><small>{_text(intel_path_copy)}</small></div>
          <div class="route-beam"></div>
          <div class="route-node"><b>OPENPOWER</b><small>{_text(power_path_copy)}</small></div>
        </div>
        <div class="panel">
          <div class="panel-label">Integrity matrix</div>
          <div class="matrix">
            <div class="matrix-row"><span>Case contracts</span><b>LOCKED</b></div>
            <div class="matrix-row"><span>Vector stream</span><b>DETERMINISTIC</b></div>
            <div class="matrix-row"><span>Matched inputs</span><b>{matched:04d}</b></div>
            <div class="matrix-row"><span>Mismatch atoms</span><b>{mismatch_atoms:04d}</b></div>
            <div class="matrix-row" data-status="{_text(outcome)}"><span>Comparison outcome</span><b>{_text(outcome.upper())}</b></div>
          </div>
        </div>
      </div>
    </section>

    <section id="cases">
      <div class="section-head">
        <div><div class="kicker">Meet the cases</div><h2>Case collection</h2></div>
        <div class="section-code"><span class="case-count" id="case-count" role="status" aria-live="polite" aria-atomic="true">{case_count} / {case_count} cases</span><br>{vectors_each:,} VECTORS / CASE</div>
      </div>
      <form class="case-filters" id="case-filters" role="search" aria-label="Filter the case catalog">
        <div class="filter-field"><label for="case-search">Find a case</label><input id="case-search" type="search" placeholder="Name, operation, ISA…" autocomplete="off" spellcheck="false"></div>
        <div class="filter-field"><label for="case-type">Result type</label><select id="case-type"><option value="">All result types</option>{element_options}</select></div>
        <div class="filter-field"><label for="case-mode">Comparison mode</label><select id="case-mode"><option value="">All comparison modes</option>{comparison_options}</select></div>
        <button id="case-reset" type="button">Reset filters</button>
      </form>
      <p class="catalog-note">Filters affect this catalog only. Verification totals describe the complete run; individual case verdicts are not inferred from aggregate results.</p>
      <noscript><p class="catalog-note">Enable JavaScript to search this catalog. All cases are shown below.</p></noscript>
      <p class="empty-state" id="case-empty" hidden>No cases match these filters. Try another name or reset the filters.</p>
      <div class="case-grid">{cards}</div>
    </section>

    <section id="evidence">
      <div class="section-head">
        <div><div class="kicker">Evidence boundary</div><h2>Cryptographic trace</h2></div>
        <div class="section-code">SHA-256<br>FULL-LENGTH IDENTIFIERS</div>
      </div>
      <div class="evidence">
        <div class="panel evidence-state">
          <div>
            <div class="panel-label">Capture classification</div>
            <strong>{_text(evidence_label)}</strong>
            <p>{_text(evidence_copy)}</p>
          </div>
          <div class="stamp">NON-NORMATIVE SHOWCASE VIEW</div>
        </div>
        <div class="panel hashes">
          <div class="hash"><span>TEST VECTOR STREAM</span><code>{_text(vector_sha256)}</code></div>
          <div class="hash"><span>CASE DEFINITION CONTRACT</span><code>{_text(case_definitions_sha256)}</code></div>
          <div class="hash"><span>USED ISA CONTRACT</span><code>{_text(isa_contract_sha256)}</code></div>
        </div>
      </div>
    </section>

    <footer>
      <span>EquiLane · Intrinsics equivalence testing</span>
      <span>{_text(status_copy)}</span>
      <span>SOURCE OF TRUTH: CANONICAL JSON ARTIFACTS</span>
    </footer>
  </main>
  <script>{_CATALOG_SCRIPT}</script>
</body>
</html>
"""


def write_showcase_report(
    output: str | Path,
    *,
    cases: Iterable[CaseDefinition],
    summary: Mapping[str, JSONValue],
    profile: str,
    seed: str,
    vector_sha256: str,
    case_definitions_sha256: str,
    isa_contract_sha256: str,
    generated_at: datetime,
    native_evidence: bool,
    quality: Mapping[str, JSONValue] | None = None,
) -> Path:
    """Atomically write the optional presentation-only HTML report."""

    path = Path(output)
    html = render_showcase_html(
        cases=cases,
        summary=summary,
        profile=profile,
        seed=seed,
        vector_sha256=vector_sha256,
        case_definitions_sha256=case_definitions_sha256,
        isa_contract_sha256=isa_contract_sha256,
        generated_at=generated_at,
        native_evidence=native_evidence,
        quality=quality,
    )
    atomic_write(path, html.encode("utf-8"))
    return path
