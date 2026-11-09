#!/usr/bin/env python3
"""Publish recorded execution observations as HTML and lossless data archives.

The bundle preserves input/result/difference bytes, not native binaries or local
build directories. Its publication manifest authenticates both the gzip files
and the original uncompressed streams. No intrinsic code is executed here.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
ROLES = ('intel', 'arm', 'openpower', 'riscv')
TARGETS = ('arm', 'openpower', 'riscv')
RUNS = {'intel':'intel-batch', 'arm':'arm-batch', 'openpower':'openpower-fork', 'riscv':'riscv-fork'}
COMPARISONS = {'arm':'compare-arm-batch', 'openpower':'compare-openpower-fork', 'riscv':'compare-riscv-fork'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('snapshot paths must stay inside the selected directory')
    path = root / relative
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError('snapshot paths must not be symlinks')
    if not path.resolve().is_relative_to(root):
        raise ValueError('snapshot path escaped its root')
    return path


def read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('snapshot JSON requires an object')
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def authenticate(root, metadata):
    path = safe(root, metadata['file'])
    if not path.is_file() or path.stat().st_size != metadata['byte_length'] or sha(path) != metadata['sha256']:
        raise ValueError('recorded source artifact differs: '+metadata['file'])
    return path


def compress(source, destination, *, records):
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    length = count = 0
    last = b''
    with source.open('rb') as raw, destination.open('wb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output, compresslevel=9, mtime=0) as archive:
            for block in iter(lambda:raw.read(1024*1024), b''):
                digest.update(block); length += len(block); count += block.count(b'\n'); last = block
                archive.write(block)
    if count != records or (length and not last.endswith(b'\n')):
        raise ValueError('stream is incomplete or has an unexpected record count')
    return {'compression':'gzip', 'compressed_byte_length':destination.stat().st_size,
            'compressed_sha256':sha(destination), 'uncompressed_byte_length':length,
            'uncompressed_sha256':digest.hexdigest(), 'record_count':count}


def inventory(directory):
    return [{'file':str(p.relative_to(directory)), 'byte_length':p.stat().st_size, 'sha256':sha(p)}
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'publication.json']


def export_snapshot(source, destination):
    from render_execution_report import render_reports
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('select a new snapshot destination')
    summary_file = safe(source, 'execution-summary.json')
    summary = read(summary_file)
    if (summary.get('artifact_type') != 'equilane.extended-execution-overview' or summary.get('complete') is not True
            or set(summary['roles']) != set(ROLES) or set(summary['comparisons']) != set(TARGETS)):
        raise ValueError('not a completed four-role execution overview')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.'+destination.name+'-', dir=destination.parent))
    streams = {}
    try:
        def copy_original(path, name):
            target = safe(temporary, name); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        def archive(metadata, name, records):
            original = authenticate(source, metadata)
            result = compress(original, safe(temporary, name), records=records)
            if result['uncompressed_sha256'] != metadata['sha256'] or result['uncompressed_byte_length'] != metadata['byte_length']:
                raise ValueError('archive changed the original stream')
            streams[name] = {**result, 'source_file':metadata['file']}
            return {'file':name, **result}
        copy_original(summary_file, 'evidence/execution-summary.original.json')
        copy_original(authenticate(source, summary['input_manifest']), 'evidence/inputs.manifest.json')
        copy_original(authenticate(source, summary['catalog']), 'data/catalog.json')
        inputs = archive(summary['inputs'], 'data/inputs.jsonl.gz', summary['distinct_inputs'])
        roles = {}
        for role in ROLES:
            row = summary['roles'][role]
            if row['directory'] != RUNS[role] or row['record_count'] != summary['distinct_inputs']:
                raise ValueError('unexpected role source or record count')
            manifest_file = authenticate(source, row['manifest'])
            manifest = read(manifest_file)
            if (manifest.get('complete') is not True or manifest.get('runner') != role
                    or manifest['status_counts'] != row['status_counts']):
                raise ValueError('result manifest disagrees with the verified overview')
            copy_original(manifest_file, f'evidence/results-{role}.manifest.json')
            results = archive(row['results'], f'data/results-{role}.jsonl.gz', row['record_count'])
            roles[role] = {key:row[key] for key in ('backend','compiled_candidates','invoked_candidates','executed_candidates','unsupported_candidates','record_count','status_counts','environment')}
            roles[role].update(results=results, original_manifest=f'evidence/results-{role}.manifest.json',
                               recorded_library_sha256=row['library']['sha256'],
                               recorded_generated_source_sha256=row['generated_source']['sha256'])
        comparisons = {}
        for role in TARGETS:
            row = summary['comparisons'][role]
            if row['directory'] != COMPARISONS[role]:
                raise ValueError('unexpected comparison source')
            original = authenticate(source, row['summary'])
            comparison = read(original)
            for key, observed in [('record_count',row['record_count']),('matched_inputs',row['matched_inputs']),
                                  ('mismatched_inputs',row['raw_byte_differences']),('not_comparable_inputs',row['not_comparable_inputs'])]:
                if comparison[key] != observed:
                    raise ValueError('comparison summary disagrees with verified counts')
            copy_original(original, f'evidence/comparison-{role}.original.json')
            differences = archive(row['differences'], f'{role}/differences.jsonl.gz', row['differences']['record_count'])
            comparisons[role] = {key:row[key] for key in ('record_count','comparable_inputs','comparable_input_coverage_percent','matched_inputs','raw_byte_differences','not_comparable_inputs','match_rate_among_comparable_percent','differing_function_count','fp_flag_differences_diagnostic_only','review_candidates')}
            comparisons[role].update(differences=differences, html=f'{role}/index.html',html_en=f'{role}/index.en.html',
                                     original_summary=f'evidence/comparison-{role}.original.json')
            write(safe(temporary, f'{role}/summary.json'), {'schema_version':1,'artifact_type':'equilane.published-extended-comparison',
                  'target':role, 'source_summary_sha256':sha(original), **comparisons[role]})
        layouts = {summary['roles'][role]['layout']['driver_sha256'] for role in ROLES}
        for name in ('run_single_unit.py','run_fork_single_unit.py','fork_worker.py'):
            path = safe(source, name)
            expected = (name == 'fork_worker.py' and summary['roles']['openpower']['layout']['fork_worker_sha256']) or sha(path)
            if sha(path) != expected or (name != 'fork_worker.py' and sha(path) not in layouts):
                raise ValueError('execution driver no longer matches the completed run')
            copy_original(path, 'drivers/'+name)
        public = {'schema_version':1,'artifact_type':'equilane.published-extended-execution','status':summary['status'],
                  'source_execution_summary_sha256':sha(summary_file),'execution_source_commit':summary['source_commit'],
                  'candidate_count':summary['candidate_count'],'distinct_inputs':summary['distinct_inputs'],
                  'result_records':summary['result_records'],'input_distribution':summary['input_distribution'],
                  'comparison_policy':summary['comparison_policy'],'guide_scope':summary['guide_scope'],
                  'physical_hardware_evidence':False,'source_identity':summary['source_identity'],
                  'inputs':inputs,'roles':roles,'comparisons':comparisons,
                  'retention':{'all_input_result_difference_records_included':True,'native_binaries_included':False,
                               'local_build_directories_included':False,'standalone_native_replay_artifact':False,
                               'original_metadata':'evidence/','original_metadata_paths_describe_local_capture':True,
                               'compression':'Lossless gzip; uncompressed digests match the original recorded streams.'}}
        write(temporary/'execution-summary.json', public)
        render_reports(source, temporary, summary)
        publication = {'schema_version':1,'artifact_type':'equilane.execution-publication','source_commit':summary['source_commit'],
                       'source_execution_summary_sha256':sha(summary_file),'streams':streams,'files':inventory(temporary)}
        publication['report_renderer_source_sha256'] = sha(ROOT/'tools/render_execution_report.py')
        write(temporary/'publication.json', publication)
        verify_snapshot(temporary)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return {'status':'verified','files':len(publication['files'])+1,'compressed_streams':len(streams),
            'byte_length':sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())}


def verify_snapshot(directory):
    directory = Path(directory).resolve()
    manifest = read(safe(directory, 'publication.json'))
    if manifest.get('artifact_type') != 'equilane.execution-publication' or manifest.get('schema_version') != 1:
        raise ValueError('unknown publication manifest')
    files = manifest['files']
    names = [row['file'] for row in files]
    if len(names) != len(set(names)) or 'publication.json' in names:
        raise ValueError('duplicate or recursive publication files')
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != set(names) | {'publication.json'}:
        raise ValueError('publication contains missing or unlisted files')
    for row in files:
        authenticate(directory,row)
    for name,metadata in manifest['streams'].items():
        if name not in names or metadata['compression'] != 'gzip':
            raise ValueError('invalid archive reference')
        path = safe(directory,name)
        if sha(path) != metadata['compressed_sha256'] or path.stat().st_size != metadata['compressed_byte_length']:
            raise ValueError('compressed archive hash changed')
        digest = hashlib.sha256(); length = count = 0
        with gzip.open(path,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                digest.update(block); length += len(block); count += block.count(b'\n')
        if (length,count,digest.hexdigest()) != (metadata['uncompressed_byte_length'],metadata['record_count'],metadata['uncompressed_sha256']):
            raise ValueError('decompressed archive differs from recorded data')
    return manifest


def stage_site(directory, destination, *, legacy=None):
    directory, destination = Path(directory).resolve(), Path(destination).resolve()
    manifest = verify_snapshot(directory)
    if destination.exists():
        raise ValueError('site destination must be new')
    destination.mkdir(parents=True)
    report_destination = destination / 'extended' if legacy else destination
    if legacy:
        legacy = Path(legacy).resolve()
        old_names = ['index.html','index.en.html','summary.json','deployment.json','.nojekyll']
        old_names += [f'{target}/{name}' for target in ('arm','openpower','riscv','riscv-vlen256')
                      for name in ('index.html','index.en.html','differences.jsonl')]
        for name in old_names:
            source = safe(legacy,name)
            if source.is_file():
                target = safe(destination,name); target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
        if not (destination/'index.html').is_file():
            raise ValueError('historical site is missing its public index')
    for name in [row['file'] for row in manifest['files']] + ['publication.json']:
        target = safe(report_destination,name); target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(safe(directory,name),target)
    if legacy:
        link_recorded_report(destination)


def link_recorded_report(directory):
    """Keep the established canonical URLs and link the recorded extension."""
    import re
    directory = Path(directory)
    for language in ('ja','en'):
        path = directory / ('index.html' if language=='ja' else 'index.en.html')
        if path.exists():
            html = path.read_text(encoding='utf-8')
            if 'id="recorded-extension"' in html:
                continue
            title = '1,895関数の実行結果を読む' if language=='ja' else 'Read the recorded execution of 1,895 functions'
            banner = ('<aside id="recorded-extension" style="padding:1rem;margin:1rem 0;background:#eee4fa;border-radius:12px">'
                      f'<a href="extended/{"" if language=="ja" else "index.en.html"}">{title}</a> · '
                      '<a href="extended/index.en.html">English</a></aside>')
            if re.search(r'<body(?:\s[^>]*)?>',html):
                html = re.sub(r'(<body(?:\s[^>]*)?>)',lambda match:match[0]+banner,html,count=1)
            else:
                html = html.replace('<h1',banner+'<h1',1)
            path.write_text(html,encoding='utf-8')
        elif language=='en':
            path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>EquiLane reports</title>'
                            '<h1>EquiLane execution reports</h1><p><a href="extended/index.en.html">Read the recorded execution of 1,895 functions</a></p>'
                            '<p><a href="index.html">Earlier canonical report (Japanese)</a></p></html>\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--site',type=Path)
    parser.add_argument('--legacy-site',type=Path)
    args=parser.parse_args()
    if args.verify:
        manifest=verify_snapshot(args.verify)
        if args.site: stage_site(args.verify,args.site,legacy=args.legacy_site)
        print(json.dumps({'status':'verified','files':len(manifest['files'])+1,'streams':len(manifest['streams'])}))
    elif args.input and args.output:
        print(json.dumps(export_snapshot(args.input,args.output)))
    else:
        parser.error('provide --input/--output or --verify')

if __name__=='__main__': main()
