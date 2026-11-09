"""Pinned integer-based IEEE arithmetic for FP16/BF16 port implementations.

This is a portable implementation dependency, never an Intel execution oracle.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = next((path.resolve() for path in (
    Path(os.environ.get('EQUILANE_PROJECT_ROOT', '.')), Path(__file__).resolve().parents[2],
    Path('/opt/equilane')) if (path/'vendor/softfloat/provenance.json').is_file()),
    Path(__file__).resolve().parents[2])
VENDOR = ROOT / 'vendor/softfloat'


def validate_softfloat(vendor=VENDOR):
    vendor = Path(vendor)
    manifest = json.loads((vendor / 'provenance.json').read_text())
    expected = {row['file']: row['sha256'] for row in manifest['files']}
    actual = {str(p.relative_to(vendor)) for p in vendor.rglob('*')
              if p.is_file() and p.name != 'provenance.json'}
    if actual != set(expected):
        raise ValueError('SoftFloat source inventory changed')
    for name, digest in expected.items():
        path = vendor / name
        if path.is_symlink() or not path.resolve().is_relative_to(vendor.resolve()):
            raise ValueError('SoftFloat source path escaped vendor root')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('SoftFloat source digest changed: ' + name)
    return manifest


_WRAPPER = r'''
#include <stdint.h>
#include "softfloat.h"
/* Encoded integers cross this ABI; native C float argument conversions do not. */
int equilane_half(uint32_t operation, uint16_t a, uint16_t b, uint16_t c,
                  uint32_t rounding, uint16_t *output, uint32_t *flags) {
    if (!output || !flags || operation > 5 || rounding > 3) return 1;
    uint_fast8_t old_rounding=softfloat_roundingMode, old_flags=softfloat_exceptionFlags;
    static const uint_fast8_t modes[]={softfloat_round_near_even,softfloat_round_minMag,
                                      softfloat_round_max,softfloat_round_min};
    softfloat_roundingMode=modes[rounding]; softfloat_exceptionFlags=0;
    float16_t x={a},y={b},z={c},result;
    switch(operation) {
        case 0: result=f16_add(x,y); break;
        case 1: result=f16_sub(x,y); break;
        case 2: result=f16_mul(x,y); break;
        case 3: result=f16_div(x,y); break;
        case 4: result=f16_sqrt(x); break;
        default: result=f16_mulAdd(x,y,z); break;
    }
    uint_fast8_t f=softfloat_exceptionFlags;
    *output=result.v;
    *flags=((f&softfloat_flag_invalid)?1:0)|((f&softfloat_flag_infinite)?2:0)|
           ((f&softfloat_flag_overflow)?4:0)|((f&softfloat_flag_underflow)?8:0)|
           ((f&softfloat_flag_inexact)?16:0);
    softfloat_roundingMode=old_rounding; softfloat_exceptionFlags=old_flags;
    return 0;
}
uint32_t equilane_half_to_single(uint16_t a) {float16_t x={a}; return f16_to_f32(x).v;}
uint16_t equilane_single_to_half(uint32_t a) {float32_t x={a}; return f32_to_f16(x).v;}
/* Storage-format expansion preserves every payload bit and performs no arithmetic. */
uint32_t equilane_bfloat_to_single(uint16_t a) {return (uint32_t)a << 16;}
'''


def build_softfloat(output, *, compiler='clang', jobs=2, vendor=VENDOR):
    """Build an auditable portable shared library with the x86 NaN policy."""
    vendor = Path(vendor).resolve()
    manifest = validate_softfloat(vendor)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    library = output / 'libequilane-softfloat.so'
    if library.exists():
        raise ValueError('SoftFloat output already exists; select a fresh directory')
    if type(jobs) is not int or jobs < 1:
        raise ValueError('SoftFloat jobs must be positive')
    shutil.copy2(vendor / 'build/Linux-x86_64-GCC/platform.h', output / 'platform.h')
    compile_flags = ['-fPIC','-O2','-std=c11','-DSOFTFLOAT_FAST_INT64','-DSOFTFLOAT_ROUND_ODD',
                     '-DINLINE_LEVEL=5','-DSOFTFLOAT_FAST_DIV32TO16','-DSOFTFLOAT_FAST_DIV64TO32',
                     '-DTHREAD_LOCAL=_Thread_local','-I'+str(output),
                     '-I'+str(vendor/'source/8086-SSE'),'-I'+str(vendor/'source/include')]
    commands = [['make','-f',str(vendor/'build/Linux-x86_64-GCC/Makefile'),'-j',str(jobs),
                 'SOURCE_DIR='+str(vendor/'source'),
                 'COMPILE_C='+compiler+' -c '+' '.join(compile_flags)+' -o $@']]
    wrapper = output / 'half-abi.c'
    wrapper.write_text(_WRAPPER)
    commands.append([compiler,*compile_flags,'-shared',str(wrapper),str(output/'softfloat.a'),'-o',str(library)])
    with (output/'build.log').open('w') as log:
        for command in commands:
            log.write(json.dumps(command)+'\n'); log.flush()
            result = subprocess.run(command,cwd=output,stdout=log,stderr=subprocess.STDOUT,timeout=300)
            if result.returncode:
                raise RuntimeError('SoftFloat build failed; see '+str(output/'build.log'))
    evidence={'schema_version':1,'artifact_type':'equilane.softfloat-build',
              'implementation':'portable-integer-IEEE','not_intel_execution_evidence':True,
              'source_commit':manifest['commit'],'commands':commands,
              'source_manifest_sha256':hashlib.sha256((vendor/'provenance.json').read_bytes()).hexdigest(),
              'wrapper_sha256':hashlib.sha256(wrapper.read_bytes()).hexdigest(),
              'library_sha256':hashlib.sha256(library.read_bytes()).hexdigest()}
    (output/'build-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return library
