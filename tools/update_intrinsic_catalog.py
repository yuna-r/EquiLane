#!/usr/bin/env python3
"""Refresh factual Guide bindings from a supplied pinned offline XML file.

Only names, types, instruction names and original short summaries are copied;
Intel's description and operation text are deliberately excluded.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from equilane.canonical import atomic_write, dump_bytes
from equilane.cases import load_case_definitions
from equilane.isa import load_isa_registry


def summary(name):
    if 'setzero' in name:
        return 'すべてのレーンを0にする。', 'Set every lane to zero.'
    if name in {'_mm_loadh_pi', '_mm_loadl_pi'}:
        half = '上位' if 'loadh' in name else '下位'
        other = '下位' if 'loadh' in name else '上位'
        return f'{half}64bitへメモリを読み込み、{other}64bitは保持する。', f'Load the {"upper" if "loadh" in name else "lower"} 64 bits from memory and preserve the other half.'
    if name in {'_mm_storeh_pi', '_mm_storel_pi', '_mm_storel_pd'}:
        half = '上位' if 'storeh' in name else '下位'
        return f'{half}64bitをメモリへ書き込む。', f'Store the {"upper" if "storeh" in name else "lower"} 64 bits to memory.'
    if name.startswith('_mm_load'):
        return '16byte整列したメモリから128bitを読み込む。', 'Load 128 bits from 16-byte-aligned memory.'
    if name.startswith('_mm_store'):
        return '128bitを16byte整列したメモリへ書き込む。', 'Store 128 bits to 16-byte-aligned memory.'
    raise ValueError(f'Provide an original summary for {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', type=Path, required=True)
    args = parser.parse_args()
    target = ROOT / 'src/equilane/data/intel-intrinsics.json'
    data = json.loads(target.read_text())
    source = args.xml.read_bytes()
    if hashlib.sha256(source).hexdigest() != data['provenance']['decoded_xml_sha256']:
        raise ValueError('Guide XML does not match the pinned source')
    tree = ET.fromstring(source)
    guide = {}
    for node in tree.findall('intrinsic'):
        guide.setdefault(node.attrib['name'], node)
    cases = load_case_definitions(ROOT / '10_official_suite/cases',
                                  isa_registry=load_isa_registry(ROOT / 'contracts/isa-registry.json'))
    entries = {}
    for case in cases:
        name = str(case.data['intel']['symbol']).removeprefix('intel')
        node = guide[name]
        if case.id in data['intrinsics']:
            entry = data['intrinsics'][case.id]
        else:
            returned = node.find('return').attrib['type']
            parameters = ', '.join(f"{p.attrib['type']} {p.attrib.get('varname', '')}".strip()
                                   for p in node.findall('parameter')) or 'void'
            ja, en = summary(name)
            entry = {'name': name, 'intel_symbol': case.data['intel']['symbol'],
                     'isa': '/'.join(p.text for p in node.findall('CPUID')),
                     'guide_url': 'https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=' + name,
                     'header': node.findtext('header'), 'prototype': f'{returned} {name}({parameters});',
                     'instructions': [p.attrib['name'] for p in node.findall('instruction')],
                     'framework_required_isa': list(case.required_isa('intel')),
                     'summary_ja': ja, 'summary_en': en,
                     'case_call': name + '(' + ', '.join(p['name'] for p in case.signature['arguments']) + ')'}
        entries[case.id] = entry
    data['intrinsics'] = entries
    bindings = [{'case_id': c.id, 'intel_symbol': c.data['intel']['symbol'],
                 'framework_required_isa': list(c.required_isa('intel'))} for c in cases]
    data['provenance']['framework_case_count'] = len(cases)
    data['provenance']['framework_case_bindings_sha256'] = hashlib.sha256(dump_bytes(bindings)).hexdigest()
    atomic_write(target, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps({'bindings': len(entries), 'guide_names': len(guide)}))

if __name__ == '__main__':
    main()
