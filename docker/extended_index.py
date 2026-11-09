"""Index the measured results of expanded intrinsic backends."""
from __future__ import annotations
import argparse
from html import escape
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from equilane.i18n import translate_html


def index(directory):
    directory=Path(directory)
    rows=[]
    for role in ('openpower','arm','riscv'):
        path=directory/f'compare-{role}'/'summary.json'
        if path.is_file():
            row=json.loads(path.read_text())
            rows.append({key:row[key] for key in ('target','status','record_count','matched_inputs','mismatched_inputs','not_comparable_inputs')})
    report={'schema_version':1,'artifact_type':'equilane.extended-pipeline','comparisons':rows,
            'status':'incomplete' if len(rows)!=3 else 'findings' if any(row['status']!='pass' for row in rows) else 'pass',
            'scope':'Separate SIMD backend. Compiled functions and executed functions are distinct; unavailable Intel instructions remain unverified.'}
    (directory/'pipeline-summary.json').write_text(json.dumps(report,indent=2)+'\n')
    table=''.join('<tr>'+''.join(f'<td>{escape(str(row[k]))}</td>' for k in ('target','status','record_count','matched_inputs','mismatched_inputs','not_comparable_inputs'))+f'<td><a href="compare-{row["target"]}/showcase.en.html">English</a> / <a href="compare-{row["target"]}/showcase.html">Japanese</a></td></tr>' for row in rows)
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><title>EquiLane — Expanded intrinsic coverage</title>
<style>body{max-width:1100px;margin:3rem auto;padding:0 1rem;font:16px/1.6 system-ui}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.6rem}</style>
<h1>Expanded intrinsic coverage</h1><p>Actual Intel intrinsics are compared with the separately identified portable C backend. Unavailable CPU features and compile failures are unverified, never passing results. FP exception flags are retained as diagnostics.</p>
<table><tr><th>Target</th><th>Status</th><th>Inputs</th><th>Match</th><th>Mismatch</th><th>Not comparable</th><th>Details</th></tr>'''+table+'</table><p><a href="pipeline-summary.json">Summary JSON</a></p></html>'
    (directory/'index.en.html').write_text(html)
    (directory/'index.html').write_text(html.replace('lang="en"','lang="ja"').replace('Expanded intrinsic coverage','追加 intrinsic の検証範囲'))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    print(json.dumps(index(p.parse_args().input)))
