"""Read the pinned official archive as data and emit name-coverage facts only."""
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path, default=ROOT / "documentation/intrinsic-coverage.json")
args = parser.parse_args()
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

archive = args.archive
member = 'Intel Intrinsics Guide/files/data.js'
archive_bytes = archive.read_bytes()
with ZipFile(archive) as package:
    js_bytes = package.read(member)
js_text = js_bytes.decode('utf-8')
assert js_text.startswith('var data_js = ') and js_text.rstrip().endswith(';')
xml_text = ast.literal_eval(js_text.removeprefix('var data_js = ').strip().removesuffix(';'))
assert isinstance(xml_text, str)
root = ET.fromstring(xml_text)
assert root.tag == 'intrinsics_list'
entries = root.findall('intrinsic')
by_name = defaultdict(list)
for entry in entries:
    by_name[entry.attrib['name']].append(entry)
all_names = set(by_name)
catalog_path = ROOT / 'src/equilane/data/intel-intrinsics.json'
catalog_bytes = catalog_path.read_bytes()
catalog = json.loads(catalog_bytes)
local = catalog['intrinsics']
registered = {value['name'] for value in local.values()}
assert len(registered) == len(local) and registered
case_bindings = {}
for path in sorted((ROOT / '10_official_suite/cases').glob('*.py')):
    module = ast.parse(path.read_text())
    literals = [node.value.value for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'CASE_YAML' for target in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if not literals:
        continue
    assert len(literals) == 1
    case_id = re.search(r'^id:\s*(\S+)\s*$', literals[0], re.MULTILINE).group(1)
    symbol = re.search(r'^intel:\s*\n\s+symbol:\s*(\S+)', literals[0], re.MULTILINE).group(1)
    case_bindings[case_id] = symbol.removeprefix('intel')
assert case_bindings == {key: value['name'] for key, value in local.items()}
missing = all_names - registered
unknown = registered - all_names
assert not unknown

def sha(value):
    return hashlib.sha256(value).hexdigest()

def labels(entry):
    return sorted({(value.text or '').strip() for value in entry.findall('CPUID') if (value.text or '').strip()})

def stats(selected):
    names = {entry.attrib['name'] for entry in selected}
    return {
        'guide_entry_count': len(selected),
        'guide_unique_name_count': len(names),
        'registered_name_count': len(names & registered),
        'missing_name_count': len(names - registered),
        'registered_name_coverage_basis_points': len(names & registered) * 10000 // len(names) if names else 0,
    }

def prototype(entry):
    result = entry.find('return')
    return_type = result.attrib.get('type', 'void') if result is not None else 'void'
    parameters = []
    for parameter in entry.findall('parameter'):
        parameter_type = parameter.attrib.get('type', '')
        name = parameter.attrib.get('varname', '')
        parameters.append((parameter_type + (' ' + name if name else '')).strip())
    return f"{return_type} {entry.attrib['name']}({', '.join(parameters) if parameters else 'void'});"

cpuid_labels = sorted({label for entry in entries for label in labels(entry)})
technologies = sorted({entry.attrib.get('tech', '') for entry in entries})
by_cpuid = []
for label in cpuid_labels:
    selected = [entry for entry in entries if label in labels(entry)]
    row = {'cpuid': label, **stats(selected)}
    row['technology_breakdown'] = [
        {'technology': tech, **stats([entry for entry in selected if entry.attrib.get('tech', '') == tech])}
        for tech in sorted({entry.attrib.get('tech', '') for entry in selected})
    ]
    by_cpuid.append(row)
sse_detail = {}
for label in ('SSE', 'SSE2'):
    selected = [entry for entry in entries if label in labels(entry)]
    names = {entry.attrib['name'] for entry in selected}
    details = []
    for name in sorted(names - registered):
        variants = [entry for entry in selected if entry.attrib['name'] == name]
        details.append({
            'name': name,
            'prototypes': sorted({prototype(entry) for entry in variants}),
            'headers': sorted({(value.text or '').strip() for entry in variants for value in entry.findall('header')}),
            'technologies': sorted({entry.attrib.get('tech', '') for entry in variants}),
            'cpuid_requirement_sets': sorted([list(value) for value in {tuple(labels(entry)) for entry in variants}]),
            'categories': sorted({(value.text or '').strip() for entry in variants for value in entry.findall('category')}),
            'guide_url': catalog['provenance']['guide_url'] + '#text=' + name,
        })
    sse_detail[label] = {
        **stats(selected),
        'registered_names': sorted(names & registered),
        'missing_intrinsics': details,
        'non_svml': stats([entry for entry in selected if entry.attrib.get('tech') != 'SVML']),
        'svml': stats([entry for entry in selected if entry.attrib.get('tech') == 'SVML']),
    }
regular_sse = [entry for entry in entries if entry.attrib.get('tech') != 'SVML'
               and any(label in ('SSE', 'SSE2') for label in labels(entry))]
report = {
    'schema_version': 1,
    'artifact_type': 'equilane.intrinsic-name-coverage',
    'language': 'en',
    'audit_date': '2026-11-10',
    'title': 'Registered Intel intrinsic names versus the complete Intel Intrinsics Guide',
    'scope': {
        'claim': f'EquiLane currently registers {len(registered)} distinct Intel intrinsic names. This is not an implementation of every intrinsic in the Intel Intrinsics Guide, or even every SSE/SSE2 API name.',
        'meaning_of_registered': 'The exact API name is mapped to an official EquiLane case and its Intel C symbol. Name registration alone does not prove a complete signature, all inputs, all rounding modes, or conforming output on any target CPU.',
        'meaning_of_missing': 'The exact Guide API name has no registered official EquiLane case. This includes aliases, macros, MMX-related names, control/memory APIs, undefined-value APIs, and SVML library functions. Missing names are not necessarily distinct machine instructions.',
        'reduced_bindings': [{
            'case_id': 'sse2.sqrt.f64x2.scalar',
            'name': '_mm_sqrt_sd',
            'guide_prototype': local['sse2.sqrt.f64x2.scalar']['prototype'],
            'case_call': local['sse2.sqrt.f64x2.scalar']['case_call'],
            'note': 'The registered case passes the same vector as both Guide arguments and therefore covers a subset of this two-argument API.'
        }],
        'semantic_conformance': 'This audit compares names and factual API metadata only. It neither runs native code nor changes existing port-conformance findings.',
        'copyright_scope': 'Only API names, prototypes, short classification labels, counts, and source provenance are retained. Guide descriptions and Operation pseudocode are not reproduced.',
    },
    'provenance': {
        'title': 'Intel Intrinsics Guide',
        'version': root.attrib['version'],
        'date_as_published': root.attrib['date'],
        'guide_url': catalog['provenance']['guide_url'],
        'download_url': 'https://cdrdv2.intel.com/v1/dl/getContent/671338',
        'download_link_label': 'Download Offline',
        'downloaded_on': '2026-11-10',
        'archive_byte_length': len(archive_bytes),
        'offline_archive_sha256': sha(archive_bytes),
        'source_member': member,
        'data_js_sha256': sha(js_bytes),
        'decoded_xml_sha256': sha(xml_text.encode('utf-8')),
        'matches_existing_catalog_pinned_source': all([
            catalog['provenance']['offline_archive_sha256'] == sha(archive_bytes),
            catalog['provenance']['data_js_sha256'] == sha(js_bytes),
            catalog['provenance']['decoded_xml_sha256'] == sha(xml_text.encode('utf-8')),
        ]),
        'registered_catalog': str(catalog_path.relative_to(ROOT)),
        'registered_catalog_sha256': sha(catalog_bytes),
        'registered_case_name_mapping_sha256': sha(json.dumps(case_bindings, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()),
    },
    'counting_method': {
        'guide_entries': 'Count every XML /intrinsics_list/intrinsic entry.',
        'global_names': 'Deduplicate by the case-sensitive intrinsic name attribute before calculating registered and missing name counts.',
        'cpuid_rows': 'A name belongs to every CPUID label present on any of its Guide entries. Rows therefore overlap and must not be added to obtain the global total.',
        'technologies': 'Use the Guide tech attribute without interpreting it as a CPUID requirement. SVML is counted separately even where its CPUID label is SSE.',
        'duplicate_names': 'The Guide can contain the same API name for different requirement sets. The global missing-name list counts such a name only once.',
        'aliases_and_macros': 'No alias expansion or instruction-based matching is used. Every Guide name remains a separate API name.',
        'prototype_reconstruction': 'Join the XML return type, intrinsic name, and ordered parameter type/varname pairs; use void when there are no parameters.',
        'case_check': 'Statically extract CASE_YAML from all official case-pack files, then verify that every case Intel symbol maps to the catalog name. No model or native function is executed.',
    },
    'summary': {
        **stats(entries),
        'duplicate_entry_count': len(entries) - len(all_names),
        'registered_case_count': len(case_bindings),
        'registered_names_absent_from_guide_count': len(unknown),
        'all_guide_names_registered': not missing,
        'sse_sse2_non_svml': stats(regular_sse),
    },
    'by_cpuid': by_cpuid,
    'without_cpuid': stats([entry for entry in entries if not labels(entry)]),
    'by_technology': [{'technology': tech, **stats([entry for entry in entries if entry.attrib.get('tech', '') == tech])} for tech in technologies],
    'sse_sse2_detail': sse_detail,
    'duplicate_guide_names': [{'name': name, 'entry_count': len(values),
                               'cpuid_requirement_sets': sorted([list(v) for v in {tuple(labels(entry)) for entry in values}])}
                              for name, values in sorted(by_name.items()) if len(values) > 1],
    'registered_names': sorted(registered),
    'missing_names': sorted(missing),
}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report['summary'], indent=2))
print('CPUID rows:',len(by_cpuid),'Missing SSE:',sse_detail['SSE']['missing_name_count'],'Missing SSE2:',sse_detail['SSE2']['missing_name_count'])
