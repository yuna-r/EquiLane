"""Self-contained, report-first interface for the local EquiLane server."""

from __future__ import annotations

from .mascot import mascot_markup


_DASHBOARD = r'''<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>EquiLane · Report studio</title>
  <style>
    :root { --paper:#faf7f0; --panel:#fffdf9; --line:#e6dfec; --text:#393244; --muted:#696273; --violet:#70538f; --mint:#e5f2e9; --rose:#f8e5e9; --ok:#27664e; --red:#a83049; --amber:#815915; --font:"Avenir Next","Hiragino Sans","Yu Gothic",system-ui,sans-serif; --mono:"SFMono-Regular",Consolas,monospace; }
    * { box-sizing:border-box; }
    [hidden] { display:none !important; }
    body { margin:0; min-width:320px; color:var(--text); background:radial-gradient(ellipse at 4% 0%,#f3e8f2,transparent 35rem),var(--paper); font:14px/1.65 var(--font); }
    button,input,select { font:inherit; }
    button,a,input,select { -webkit-tap-highlight-color:transparent; }
    button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible { outline:3px solid var(--violet); outline-offset:3px; }
    button { cursor:pointer; }
    button:disabled { cursor:wait; opacity:.65; }
    .studio { width:min(1640px,calc(100% - 44px)); margin:0 auto; padding:22px 0 28px; }
    .masthead { display:flex; align-items:center; justify-content:space-between; gap:24px; margin:0 4px 22px; }
    .brand { display:flex; align-items:center; gap:12px; }
    .brand-mark { display:grid; place-items:center; width:44px; height:44px; border-radius:15px; background:#e9def3; color:var(--violet); font:800 32px/1 var(--font); }
    .brand h1 { margin:0; font-size:25px; line-height:1.2; font-weight:800; letter-spacing:-.04em; }
    .brand p { margin:3px 0 0; font-size:11px; color:var(--muted); }
    .masthead-note { display:flex; align-items:center; gap:11px; color:var(--muted); font-size:11px; }
    .mascot { display:block; width:70px; height:56px; object-fit:contain; }
    .local-pill { display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border:1px solid #d1e2d5; border-radius:20px; background:#edf4ee; color:#42634e; font:10px/1.5 var(--mono); }
    .local-pill::before { content:""; width:5px; height:5px; background:#6c9c7f; border-radius:50%; }
    .workspace { display:grid; grid-template-columns:300px minmax(0,1fr); gap:20px; align-items:start; }
    .sidebar { display:grid; gap:15px; }
    .card { padding:22px; border:1px solid var(--line); border-radius:23px; background:var(--panel); min-width:0; }
    .section-kicker { margin:0 0 5px; color:var(--violet); font-size:9px; font-weight:750; letter-spacing:.12em; text-transform:uppercase; }
    h2 { margin:0; font-size:18px; line-height:1.4; letter-spacing:-.025em; }
    .form-copy { margin:7px 0 19px; color:var(--muted); font-size:11px; line-height:1.8; }
    fieldset { min-width:0; margin:0; padding:0; border:0; }
    .field { display:grid; gap:6px; margin-top:14px; }
    .field:first-child { margin-top:0; }
    label { color:#5c5367; font-size:11px; font-weight:650; }
    input[type="number"],select { width:100%; min-height:42px; padding:9px 11px; border:1px solid #d7c9e3; border-radius:11px; background:white; color:var(--text); font-size:12px; }
    .field-row { display:grid; grid-template-columns:1.2fr .8fr; gap:11px; }
    .field-hint { margin:0; color:var(--muted); font-size:10px; line-height:1.65; }
    .quality-option { display:flex; align-items:flex-start; gap:9px; padding:13px; margin-top:17px; border:1px solid #e1d7eb; border-radius:13px; background:#f7f2fb; }
    .quality-option input { width:15px; height:15px; margin:3px 0 0; accent-color:var(--violet); flex-shrink:0; }
    .quality-option label { display:block; cursor:pointer; }
    .quality-option small { display:block; margin-top:3px; font-size:10px; line-height:1.7; color:var(--muted); font-weight:400; }
    .coverage-option { margin-top:10px; background:#fffdf9; }
    .coverage-option input:disabled + label { color:var(--muted); }
    .primary-button { display:flex; align-items:center; justify-content:center; gap:8px; width:100%; min-height:44px; padding:10px 13px; margin-top:17px; border:1px solid #70538f; border-radius:13px; background:#70538f; color:#fff; font-size:12px; font-weight:700; transition:background .15s; }
    .primary-button:hover:not(:disabled) { background:#5f437f; }
    .form-footnote { margin:11px 0 0; color:var(--muted); font-size:10px; line-height:1.8; }
    .job-card { padding:17px 19px; border-color:#d8c8e8; background:#f1eaf8; }
    .job-card[data-tone="success"] { background:#edf4ee; border-color:#d1e2d5; }
    .job-card[data-tone="warning"] { background:#fcf2df; border-color:#e8d8b6; }
    .job-card[data-tone="error"] { background:#fff5f6; border-color:#edb9c3; }
    .job-title { margin:0; color:var(--violet); font-size:12px; font-weight:750; }
    .job-card[data-tone="success"] .job-title { color:var(--ok); }
    .job-card[data-tone="warning"] .job-title { color:var(--amber); }
    .job-card[data-tone="error"] .job-title { color:var(--red); }
    .job-stage { margin:5px 0 0; color:var(--muted); font-size:11px; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
    .progress-track { height:5px; margin-top:13px; border-radius:4px; background:#ddd0e8; overflow:hidden; }
    .progress-track::after { content:""; display:block; width:36%; height:100%; border-radius:4px; background:#9f83ba; animation:working 1.4s ease-in-out infinite; }
    @keyframes working { from { transform:translateX(-100%); } to { transform:translateX(380%); } }
    .history-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; }
    .count-pill { padding:3px 8px; border-radius:12px; background:#f1eaf8; color:var(--violet); font:10px/1.5 var(--mono); }
    .history-list { display:grid; gap:9px; max-height:450px; margin-top:14px; overflow-y:auto; padding:3px; scrollbar-color:#cfbddd transparent; scrollbar-width:thin; }
    .history-item { display:grid; gap:7px; width:100%; padding:13px; border:1px solid var(--line); border-radius:14px; background:#fff; text-align:left; color:var(--text); transition:border-color .15s,background .15s; }
    .history-item:hover { border-color:#bea5d0; background:#fcf9ff; }
    .history-item[aria-pressed="true"] { border-color:#a78abb; background:#f3edf8; box-shadow:inset 3px 0 0 #a78abb; }
    .history-title { font-size:11px; font-weight:750; }
    .history-meta { color:var(--muted); font-size:10px; }
    .badges { display:flex; gap:5px; flex-wrap:wrap; }
    .badge { display:inline-flex; align-items:center; gap:4px; padding:3px 7px; border:1px solid #e0d7e7; border-radius:7px; background:#f2edf6; color:#685375; font-size:9px; line-height:1.55; font-weight:600; }
    .badge[data-tone="success"] { background:#eaf4ee; border-color:#cfe2d6; color:var(--ok); }
    .badge[data-tone="warning"] { background:#fcf2df; border-color:#e8d8b6; color:var(--amber); }
    .badge[data-tone="error"] { background:#f8e5e9; border-color:#ecc4ce; color:var(--red); }
    .history-empty { margin:14px 0 0; color:var(--muted); font-size:11px; line-height:1.8; }
    .report-pane { min-width:0; overflow:hidden; border:1px solid var(--line); border-radius:25px; background:var(--panel); box-shadow:0 12px 40px #8d709309; }
    .report-toolbar { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; padding:18px 22px; border-bottom:1px solid var(--line); background:#fffdf9; }
    .report-toolbar h2 { font-size:17px; }
    .report-date { margin:3px 0 7px; color:var(--muted); font-size:10px; }
    .report-actions { display:flex; align-items:center; gap:7px; flex-wrap:wrap; }
    .quiet-action { display:inline-flex; align-items:center; justify-content:center; min-height:35px; padding:7px 11px; border:1px solid #d7c9e3; border-radius:10px; background:#fff; color:#70538f; text-decoration:none; font-size:10px; font-weight:650; }
    .quiet-action:hover { background:#f1eaf8; }
    .live-output { margin:18px 20px; border:1px solid #584961; border-radius:17px; overflow:hidden; background:#322b3e; color:#eee6f5; }
    .live-output > summary { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; cursor:pointer; list-style:none; font-size:11px; font-weight:650; }
    .live-output > summary::-webkit-details-marker { display:none; }
    .live-output > summary:focus-visible { outline:3px solid #cfb5e8; outline-offset:-4px; border-radius:14px; }
    .output-heading { display:flex; align-items:center; gap:8px; }
    .output-heading::before { content:"▸"; color:#c9a9e5; }
    .live-output[open] .output-heading::before { content:"▾"; }
    .output-kicker { color:#cfb8e3; font:10px/1.5 var(--mono); letter-spacing:.08em; }
    .output-state { flex-shrink:0; padding:3px 8px; border:1px solid #72617e; border-radius:8px; color:#dfcbea; font-size:10px; }
    .output-state[data-state="running"] { background:#514063; }
    .output-state[data-state="completed"] { color:#b8e3ce; border-color:#668d7a; }
    .output-state[data-state="failed"] { color:#ffccd7; border-color:#ac7988; }
    .output-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 16px; border-top:1px solid #51445d; background:#3b3149; }
    .output-toolbar p { margin:0; color:#cebedb; font-size:10px; }
    .output-toolbar button { min-height:28px; padding:4px 9px; border:1px solid #796289; border-radius:8px; background:#4a395a; color:#f2e5fa; font-size:10px; }
    .output-toolbar button:hover { background:#594369; }
    .job-output { max-height:280px; min-height:88px; margin:0; padding:16px 18px 19px; overflow:auto; color:#eee6f5; font:11px/1.8 var(--mono); white-space:pre; tab-size:4; scrollbar-color:#9272a7 #322b3e; scrollbar-width:thin; }
    .job-output:focus-visible { outline:2px solid #cfb5e8; outline-offset:-4px; }
    .output-placeholder { margin:0; padding:18px; color:#cebedb; font-size:11px; }
    iframe { display:block; width:100%; height:max(710px,calc(100vh - 230px)); min-height:600px; border:0; background:var(--paper); }
    .report-empty { display:grid; justify-items:center; align-content:center; min-height:640px; padding:40px; background:radial-gradient(ellipse at 50% 25%,#f2eaf8,transparent 24rem),#fffdf9; text-align:center; }
    .report-empty .mascot { width:220px; height:176px; margin-bottom:16px; animation:float 6s ease-in-out infinite; }
    @keyframes float { 50% { transform:translateY(-5px); } }
    .report-empty h3 { margin:0 0 8px; font-size:24px; line-height:1.4; letter-spacing:-.035em; }
    .report-empty p { max-width:380px; margin:0; color:var(--muted); font-size:12px; line-height:1.9; }
    .empty-tag { display:inline-block; padding:6px 11px; margin-top:22px; border:1px dashed #bda7d0; border-radius:10px; color:var(--violet); font-size:10px; }
    .notice { display:flex; align-items:center; justify-content:space-between; gap:15px; margin-bottom:18px; padding:13px 17px; border:1px solid #edb9c3; border-radius:15px; background:#fff5f6; color:var(--red); font-size:11px; }
    .notice p { margin:0; overflow-wrap:anywhere; }
    .notice button { flex-shrink:0; min-height:32px; padding:5px 10px; border:1px solid #dbadb9; border-radius:9px; background:#fff; color:var(--red); font-size:11px; }
    footer { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; margin:18px 4px 0; color:var(--muted); font-size:10px; }
    noscript p { margin:0 0 15px; padding:13px 17px; border:1px solid #e8d8b6; border-radius:13px; background:#fcf2df; font-size:12px; }
    @media (min-width:1400px) { .workspace { grid-template-columns:320px minmax(0,1fr); } }
    @media (max-width:1000px) { .workspace { grid-template-columns:260px minmax(0,1fr); gap:14px; } .card { padding:18px; } .studio { width:calc(100% - 28px); } .masthead-note > span:first-child { display:none; } }
    @media (max-width:760px) { .workspace { grid-template-columns:1fr; } .sidebar { grid-template-columns:1fr 1fr; align-items:start; } .new-check { grid-row:span 2; } .history { grid-column:2; } .history-list { max-height:240px; } .report-pane { grid-row:2; } .masthead { gap:12px; } .masthead-note .mascot { display:none; } .report-toolbar { padding:16px; } iframe { height:780px; } .report-empty { min-height:460px; } }
    @media (max-width:540px) { .studio { width:calc(100% - 22px); padding-top:17px; } .masthead { margin-bottom:17px; } .brand { gap:9px; } .brand h1 { font-size:22px; } .brand p { max-width:170px; font-size:10px; } .brand-mark { width:38px; height:38px; border-radius:13px; } .local-pill { font-size:8px; padding:4px 7px; } .sidebar { grid-template-columns:1fr; } .new-check { grid-row:auto; } .history { grid-column:auto; } .history-list { display:flex; max-height:none; overflow-x:auto; overflow-y:hidden; } .history-item { flex:0 0 220px; } .report-toolbar { gap:10px; } .report-empty { padding:28px; } .report-empty h3 { font-size:22px; } footer { font-size:9px; } }
    @media (max-width:540px) { .live-output { margin:12px; } .live-output > summary { padding:12px; } .job-output { max-height:240px; padding:12px; font-size:10px; } .output-toolbar { padding:8px 12px; } }
    @media (prefers-reduced-motion:reduce) { *,*::before,*::after { animation:none !important; transition:none !important; scroll-behavior:auto !important; } }
  </style>
</head>
<body>
  <main class="studio">
    <header class="masthead">
      <div class="brand"><span class="brand-mark" aria-hidden="true">≡</span><div><h1>EquiLane</h1><p>実行して、比べて、レポートへ。</p></div></div>
      <div class="masthead-note"><span>Small checks. Clear reports.</span>{{MASCOT}}<span class="local-pill">LOCAL STUDIO</span></div>
    </header>
    <noscript><p>この画面で実行・履歴表示を使うにはJavaScriptを有効にしてください。保存済みのHTMLレポートは単体でも開けます。</p></noscript>
    <div class="notice" id="api-notice" role="alert" hidden><p id="api-error"></p><button id="retry-button" type="button">再接続</button></div>
    <div class="workspace">
      <aside class="sidebar" aria-label="チェックの実行と履歴">
        <section class="card new-check" aria-labelledby="new-check-title">
          <p class="section-kicker">Make a report</p><h2 id="new-check-title">新しいチェック</h2>
          <p class="form-copy">同じ入力を2つの経路へ。<br>結果を、読みやすいレポートにまとめます。</p>
          <form id="check-form" aria-labelledby="new-check-title">
            <fieldset id="check-controls" disabled>
              <div class="field"><label for="profile">チェックの種類</label><select id="profile" name="profile"><option value="smoke">さくっと / Smoke</option><option value="standard">しっかり / Standard</option><option value="stress">たっぷり / Stress</option></select></div>
              <div class="field-row">
                <div class="field"><label for="count">入力数 / ケース</label><input id="count" name="count_per_case" type="number" value="8" min="1" max="100000" step="1" required inputmode="numeric" aria-describedby="count-hint"></div>
                <div class="field"><label for="jobs">並列実行</label><select id="jobs" name="jobs"><option value="1">1つずつ</option><option value="auto">自動</option></select></div>
              </div>
              <p class="field-hint" id="count-hint">まずは8入力で、レポートをひとつ。</p>
              <div class="quality-option"><input id="quality" name="quality" type="checkbox"><label for="quality">独立した検証も実施<small>モデルの出力を期待値と照合します。<br>経路同士の一致とは別に、結果を表示します。</small></label></div>
              <div class="quality-option coverage-option"><input id="coverage" name="coverage" type="checkbox" disabled aria-describedby="coverage-hint"><label for="coverage">カバレッジも計測<small id="coverage-hint">独立した検証の実行時だけ追加できます。<br>実行時間が増えるため、通常はオフです。</small></label></div>
              <button class="primary-button" id="run-button" type="submit"><span aria-hidden="true">＋</span><span id="run-label">レポートを作る</span></button>
            </fieldset>
          </form>
          <p class="form-footnote">この画面のチェックは開発用fixtureです。<br>実機によるCPU間検証とは区別して表示します。</p>
        </section>
        <section class="card job-card" id="job-card" data-tone="neutral" aria-label="実行状況" aria-live="polite" aria-atomic="true">
          <p class="job-title" id="job-title">スタジオを開いています</p><p class="job-stage" id="job-stage">保存済みのレポートを確認しています。</p>
          <div class="progress-track" id="job-progress" role="progressbar" aria-label="チェック実行中" hidden></div>
        </section>
        <section class="card history" aria-labelledby="history-title">
          <div class="history-heading"><div><p class="section-kicker">Your reports</p><h2 id="history-title">レポート履歴</h2></div><span class="count-pill" id="history-count">—</span></div>
          <div class="history-list" id="history-list" aria-label="保存済みレポート"></div>
          <p class="history-empty" id="history-empty" hidden>まだレポートがありません。<br>最初のチェックから始めましょう。</p>
        </section>
      </aside>
      <section class="report-pane" aria-labelledby="report-title">
        <div class="report-toolbar"><div><h2 id="report-title">レポートを読む</h2><p class="report-date" id="report-date">ここに、チェックの結果が並びます。</p><div class="badges" id="selected-badges"></div></div><div class="report-actions" id="report-actions" hidden><a class="quiet-action" id="open-report" target="_blank" rel="noopener noreferrer">別の画面で開く ↗</a><a class="quiet-action" id="download-report" download>HTMLを保存 ↓</a></div></div>
        <details class="live-output" id="live-output" hidden>
          <summary><span class="output-heading"><span class="output-kicker">LIVE OUTPUT</span><span>実行ログ</span></span><span class="output-state" id="output-state">待機中</span></summary>
          <div class="output-toolbar"><p>CUIの出力を、そのまま表示しています。</p><button id="follow-output" type="button" aria-controls="job-output">最新へ ↓</button></div>
          <p class="output-placeholder" id="output-placeholder">CUIの出力を待っています。</p>
          <pre class="job-output" id="job-output" tabindex="0" aria-label="チェックのCUI出力" aria-live="off" hidden></pre>
        </details>
        <div class="report-empty" id="report-empty">{{MASCOT}}<h3>最初のレポートを、どうぞ。</h3><p>「レポートを作る」を押すと、ここに結果が届きます。<br>ケースの検索も、検証の内訳も、ひとつのHTMLに。</p><span class="empty-tag">8 inputs / case · small and reproducible</span></div>
        <iframe id="report-frame" title="選択したEquiLaneレポート" sandbox="allow-scripts" hidden></iframe>
      </section>
    </div>
    <footer><span>EquiLane · Intrinsics equivalence testing</span><span>レポートはHTML単体で持ち出せます。</span></footer>
  </main>
  <script>
  (() => {
    const el = id => document.getElementById(id);
    const state = {reports: [], selectedId: null, busy: true, submitting: false, completedJob: null, timer: null, outputJob: null, followOutput: true};
    const number = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value).toLocaleString('ja-JP') : '—';
    const defaults = {smoke: 8, standard: 1000, stress: 100000};
    const formatDate = value => {
      if (!value) return '日時未記録';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value || '日時未記録') :
        new Intl.DateTimeFormat('ja-JP', {timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(date);
    };
    const reportURL = value => {
      if (typeof value !== 'string' || !value) return null;
      try {
        const url = new URL(value, location.origin);
        if (url.origin !== location.origin || !url.pathname.startsWith('/reports/') || url.username || url.password) return null;
        return url.href;
      } catch { return null; }
    };
    const notice = message => { el('api-error').textContent = String(message); el('api-notice').hidden = false; };
    const request = async (path, options) => {
      const response = await fetch(path, {cache: 'no-store', ...options});
      let data;
      try { data = await response.json(); } catch { throw new Error('サーバーの応答を読み取れませんでした。'); }
      if (!response.ok) {
        const error = new Error((data && data.error) || 'リクエストを完了できませんでした。');
        error.status = response.status;
        throw error;
      }
      return data;
    };
    const comparison = report => ({
      pass: ['経路比較は一致', 'success'], mismatch: ['比較に不一致', 'error'],
      not_comparable: ['比較できない入力あり', 'warning']
    }[report.outcome] || ['比較結果を確認', 'neutral']);
    const quality = report => {
      if (report.quality_status === 'fail') return [Number(report.quality_mismatches) > 0 ? `モデル検証: ${number(report.quality_mismatches)}不一致` : '独立検証: 要確認', 'error'];
      if (report.quality_status === 'pass') return ['独立検証: pass', 'success'];
      return ['独立検証: 未実施', 'warning'];
    };
    const addBadge = (container, info) => {
      const badge = document.createElement('span');
      badge.className = 'badge'; badge.dataset.tone = info[1]; badge.textContent = info[0];
      container.append(badge);
    };
    const displayBadges = (container, report) => {
      container.replaceChildren(); addBadge(container, comparison(report)); addBadge(container, quality(report));
    };
    const selectReport = report => {
      const url = reportURL(report.url);
      if (!url) { notice('このレポートのURLを開けません。履歴を再取得してください。'); return; }
      state.selectedId = report.id;
      el('report-title').textContent = 'レポートを読む';
      el('report-date').textContent = formatDate(report.generated_at);
      displayBadges(el('selected-badges'), report);
      el('open-report').href = url; el('download-report').href = url;
      el('download-report').download = `EquiLane-${String(report.id).replace(/[^a-zA-Z0-9_.-]/g, '_')}.html`;
      el('report-actions').hidden = false; el('report-empty').hidden = true;
      const frame = el('report-frame');
      if (frame.getAttribute('src') !== url) frame.src = url;
      frame.hidden = false;
      document.querySelectorAll('.history-item').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.reportId === String(report.id))));
    };
    const renderHistory = () => {
      const list = el('history-list'); list.replaceChildren();
      el('history-count').textContent = String(state.reports.length);
      el('history-empty').hidden = state.reports.length !== 0;
      state.reports.forEach(report => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'history-item';
        button.dataset.reportId = String(report.id); button.setAttribute('aria-pressed', String(report.id === state.selectedId));
        const title = document.createElement('span'); title.className = 'history-title'; title.textContent = formatDate(report.generated_at);
        const meta = document.createElement('span'); meta.className = 'history-meta'; meta.textContent = `${number(report.case_count)} cases · ${number(report.record_count)} inputs`;
        const badges = document.createElement('span'); badges.className = 'badges'; displayBadges(badges, report);
        button.append(title, meta, badges); button.addEventListener('click', () => selectReport(report)); list.append(button);
      });
    };
    const refreshReports = async (preferredURL = null) => {
      const data = await request('/api/reports');
      state.reports = Array.isArray(data.reports) ? data.reports.filter(report => report && reportURL(report.url)) : [];
      renderHistory();
      const preferred = reportURL(preferredURL);
      const chosen = (preferred && state.reports.find(report => reportURL(report.url) === preferred)) ||
        state.reports.find(report => report.id === state.selectedId) || state.reports[0];
      if (chosen) selectReport(chosen);
      return chosen;
    };
    const setBusy = busy => {
      state.busy = busy; el('check-controls').disabled = busy;
      el('run-label').textContent = busy ? '実行状況を確認中…' : 'レポートを作る';
      el('job-progress').hidden = !busy;
    };
    const jobMessage = (title, stage, tone = 'neutral') => {
      if (el('job-title').textContent !== title) el('job-title').textContent = title;
      if (el('job-stage').textContent !== stage) el('job-stage').textContent = stage;
      el('job-card').dataset.tone = tone;
    };
    const updateOutput = job => {
      if (!job) return;
      const panel = el('live-output'); const output = el('job-output');
      const text = typeof job.output === 'string' ? job.output : '';
      const newJob = state.outputJob !== job.id;
      if (newJob) {
        state.outputJob = job.id; state.followOutput = true;
        panel.open = true;
      }
      panel.hidden = false;
      const label = {running: '実行中', completed: '実行終了', failed: 'エラーで終了'}[job.state] || '実行ログ';
      if (el('output-state').textContent !== label) el('output-state').textContent = label;
      el('output-state').dataset.state = String(job.state || '');
      el('output-placeholder').hidden = text.length !== 0; output.hidden = text.length === 0;
      if (output.textContent !== text) {
        const previousTop = output.scrollTop;
        output.textContent = text;
        output.scrollTop = state.followOutput ? output.scrollHeight : previousTop;
      }
    };
    el('job-output').addEventListener('scroll', () => {
      const output = el('job-output');
      state.followOutput = output.scrollHeight - output.clientHeight - output.scrollTop <= 24;
    });
    el('live-output').addEventListener('toggle', () => {
      if (el('live-output').open && state.followOutput) el('job-output').scrollTop = el('job-output').scrollHeight;
    });
    el('follow-output').addEventListener('click', () => {
      const output = el('job-output');
      state.followOutput = true; output.scrollTop = output.scrollHeight;
    });
    const stageText = value => {
      const stage = String(value || 'レポートを準備しています。');
      if (/generat.*vector|generate input/i.test(stage)) return '入力データを用意しています。';
      if (/run.*intel|intel runner/i.test(stage)) return 'Intel側の経路を実行しています。';
      if (/run.*openpower|openpower runner/i.test(stage)) return 'OpenPOWER側の経路を実行しています。';
      if (/compar/i.test(stage)) return '2つの出力を比べています。';
      if (/coverage/i.test(stage)) return 'カバレッジを計測しています。';
      if (/quality|sanitizer/i.test(stage)) return '独立した品質検証を実施しています。';
      if (/showcase|finaliz|report/i.test(stage)) return '結果をレポートにまとめています。';
      return stage.length > 220 ? stage.slice(-220) : stage;
    };
    const scheduleStatus = () => {
      clearTimeout(state.timer);
      if (state.busy) state.timer = setTimeout(refreshStatus, 1000);
    };
    const refreshStatus = async () => {
      try {
        const data = await request('/api/status'); const job = data.job;
        updateOutput(job);
        if (!job) {
          if (!state.submitting) { setBusy(false); jobMessage('いつでも、次のチェックを。', 'レポートは履歴からいつでも開けます。'); }
        } else if (job.state === 'running') {
          setBusy(true); el('run-label').textContent = 'レポートを作成中…';
          jobMessage('チェックを実行しています', stageText(job.stage));
        } else {
          if (state.completedJob !== job.id) {
            const selected = await refreshReports(job.report_url);
            state.completedJob = job.id;
            const reportAvailable = Boolean(job.report_url && selected && reportURL(selected.url) === reportURL(job.report_url));
            if (job.state === 'completed' && reportAvailable) {
              const warning = selected.quality_status === 'fail' || selected.outcome !== 'pass';
              jobMessage('レポートができました', warning ? '要確認の項目があります。内訳をレポートで確認できます。' : '経路比較の結果を、レポートで確認できます。', warning ? 'warning' : 'success');
            } else if (job.state === 'failed') {
              jobMessage('チェックで問題が見つかりました', String(job.error || (reportAvailable ? 'レポートに検証結果を保存しました。内訳をご確認ください。' : 'レポートを完成できませんでした。入力を確認して、もう一度実行できます。')), 'error');
            } else {
              jobMessage('チェックが終了しました', reportAvailable ? '結果をレポートで確認できます。' : '保存済みのレポートを履歴から選んでください。');
            }
          }
          setBusy(false);
        }
      } catch (error) { notice(error.message); }
      finally { scheduleStatus(); }
    };
    const syncCoverage = () => {
      el('coverage').disabled = !el('quality').checked;
      if (!el('quality').checked) el('coverage').checked = false;
    };
    el('quality').addEventListener('change', syncCoverage);
    syncCoverage();
    el('profile').addEventListener('change', () => {
      el('count').value = String(defaults[el('profile').value]);
      el('count-hint').textContent = el('profile').value === 'smoke' ? 'まずは8入力で、レポートをひとつ。' : '入力数は調整できます。多いほど時間がかかります。';
    });
    el('check-form').addEventListener('submit', async event => {
      event.preventDefault(); if (state.busy || state.submitting) return;
      const count = Number(el('count').value);
      if (!Number.isInteger(count) || count < 1 || count > 100000) { notice('入力数は1〜100,000の整数で指定してください。'); el('count').focus(); return; }
      const payload = {profile: el('profile').value, count_per_case: count, jobs: el('jobs').value === 'auto' ? 'auto' : 1, quality: el('quality').checked, coverage: el('quality').checked && el('coverage').checked};
      state.submitting = true; state.completedJob = null; setBusy(true); el('api-notice').hidden = true;
      jobMessage('チェックを開始しています', '新しいレポートを準備します。');
      try {
        await request('/api/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      } catch (error) {
        notice(error.status === 409 ? '別のチェックが実行中です。完了を待ってから、次のレポートを作れます。' : error.message);
        if (error.status !== 409) setBusy(false);
      } finally { state.submitting = false; await refreshStatus(); }
    });
    const connect = async () => {
      el('retry-button').disabled = true; el('api-notice').hidden = true;
      const results = await Promise.allSettled([refreshReports(), refreshStatus()]);
      results.forEach(result => { if (result.status === 'rejected') notice(result.reason.message); });
      el('retry-button').disabled = false;
    };
    el('retry-button').addEventListener('click', connect);
    connect();
  })();
  </script>
</body>
</html>
'''


def render_dashboard() -> str:
    """Return the local dashboard without external assets or user-supplied HTML."""
    return _DASHBOARD.replace("{{MASCOT}}", mascot_markup())
