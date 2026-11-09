"""Execute unchanged registered C implementations using one compilation unit.

This only changes compilation grouping. The committed generator, input IDs,
C dispatch, result validation, CPU guards and per-case workers are unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
from equilane import extended

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--role',required=True)
args=parser.parse_args()
compile_original=extended._compile
cache={}

def compile_single_unit(catalog,names,directory,role,compiler,attempts,extra_flags=()):
    if not cache:
        # Let the original bisection policy handle any genuine compile failure.
        extended._compile=compile_original
        try:
            cache.update(compile_original(catalog,[r['name'] for r in catalog['intrinsics']],
                         directory,role,compiler,attempts,extra_flags))
        finally:
            extended._compile=compile_single_unit
    return {name:cache[name] for name in names}

extended._compile=compile_single_unit
result=extended.run_extended_native(args.input,args.output,role=args.role)
Path(args.output,'execution-layout.json').write_text(json.dumps({
    'schema_version':1,'purpose':'Compile the complete selected catalog in one unit; unchanged generator and case execution.',
    'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'source_commit':'e3092c451238d77efe241d91a719df0c51ed993a'},indent=2)+'\n')
print(json.dumps(result))
raise SystemExit(result['exit_code'])
