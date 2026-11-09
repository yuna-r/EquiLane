"""FP16 packed arithmetic ports using integer-based IEEE SoftFloat.

All 117 API names correspond to real Intel AVX-512 FP16 intrinsics. Native
Intel reference code is guarded; software arithmetic is never its substitute.
Only non-immediate packed arithmetic/FMA forms are implemented here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

from .simde_codegen import fp_wrapper_source, read_guide
from .softfloat import validate_softfloat, VENDOR

OPS = ('add','sub','mul','div','sqrt','fmadd','fnmadd','fmsub','fnmsub','fmaddsub','fmsubadd')
PATTERN = re.compile(r'_mm(256|512)?_(?:(mask3|maskz|mask)_)?('+'|'.join(OPS)+r')_ph\Z')
ARTIFACT = 'equilane.softfloat-fp16-candidates'


def _record(name, ordinal):
    match = PATTERN.fullmatch(name)
    if not match: raise ValueError('unknown FP16 arithmetic API')
    width, mode, operation = int(match[1] or 128), match[2] or 'none', match[3]
    fused = operation not in ('add','sub','mul','div','sqrt')
    if mode=='mask3' and not fused: raise ValueError('mask3 requires FMA')
    lanes=width//16; size=width//8; vector='__m'+str(width)+'h'; mask='__mmask'+str(lanes)
    if operation=='sqrt': names=['a']
    elif fused: names=['a','b','c']
    else: names=['a','b']
    if mode=='mask': names=(['a','k','b','c'] if fused else ['src','k',*names])
    elif mode=='maskz': names=['k',*names]
    elif mode=='mask3': names=[*names,'k']
    arguments=[]; offset=0
    for parameter in names:
        is_mask=parameter=='k'; ctype=mask if is_mask else vector
        length=lanes//8 if is_mask else size
        arguments.append({'name':parameter,'guide_type':ctype,'intel_type':ctype,
            'portable_type':ctype,'byte_length':length,'kind':'mask' if is_mask else 'vector',
            'element_type':'MASK' if is_mask else 'FP16','offset':offset})
        offset+=length
    returned={'guide_type':vector,'intel_type':vector,'portable_type':vector,'byte_length':size,'kind':'vector'}
    return {'name':name,'ordinal':ordinal,'operation':operation,'mask_mode':mode,'lane_count':lanes,
            'technology':'AVX-512','cpuid':['AVX512_FP16']+(['AVX512VL'] if width<512 else []),
            'intel_target_features':['avx512fp16']+(['avx512vl'] if width<512 else []),
            'categories':['Arithmetic'],'arguments':arguments,'return':returned,
            'input_byte_length':offset,'output_byte_length':size,
            'status':'source_implementation_not_execution_evidence'}


def build_catalog(archive):
    nodes,guide=read_guide(archive)
    names=sorted({node.get('name') for node in nodes if PATTERN.fullmatch(node.get('name',''))})
    records=[_record(name,index) for index,name in enumerate(names)]
    source=validate_softfloat()
    return {'schema_version':1,'artifact_type':ARTIFACT,'language':'en','abi_version':1,
        'scope':'117 non-immediate packed FP16 arithmetic and FMA APIs, with merge, zero and mask3 forms. Half values cross the ABI as raw bits. This is an implementation catalog, not conformance evidence.',
        'byte_order':'little','input_layout':'Arguments concatenated in signature order without padding.',
        'intel_reference':'Actual AVX-512 FP16 intrinsic behind a CPU guard; never SoftFloat.',
        'portable_backend':'Berkeley SoftFloat integer IEEE FP16 with 8086-SSE NaN specialization.',
        'statuses':{'ok':0,'unknown_operation':1,'invalid_buffer':2,'unsupported_cpu':3,'fp_environment_error':4},
        'guide':guide,'softfloat_commit':source['commit'],
        'softfloat_manifest_sha256':hashlib.sha256((VENDOR/'provenance.json').read_bytes()).hexdigest(),
        'candidate_count':len(records),'intrinsics':records}


def validate_catalog(catalog):
    if (catalog.get('artifact_type')!=ARTIFACT or type(catalog.get('schema_version')) is not int
        or catalog.get('schema_version')!=1 or type(catalog.get('abi_version')) is not int or catalog.get('abi_version')!=1):
        raise ValueError('invalid FP16 catalog type')
    expected_keys={'schema_version','artifact_type','language','abi_version','scope','byte_order','input_layout',
                   'intel_reference','portable_backend','statuses','guide','softfloat_commit',
                   'softfloat_manifest_sha256','candidate_count','intrinsics'}
    if set(catalog)!=expected_keys or catalog['byte_order']!='little':
        raise ValueError('invalid FP16 catalog fields')
    pinned=json.loads((Path(__file__).parent/'data/intel-intrinsics.json').read_text())['provenance']
    if catalog['guide']!={key:pinned[key] for key in ('version','date','offline_archive_sha256','data_js_sha256','decoded_xml_sha256')}:
        raise ValueError('FP16 catalog Guide provenance changed')
    source=validate_softfloat()
    if source['commit']!=catalog.get('softfloat_commit') or hashlib.sha256((VENDOR/'provenance.json').read_bytes()).hexdigest()!=catalog.get('softfloat_manifest_sha256'):
        raise ValueError('FP16 catalog source changed')
    records=catalog.get('intrinsics')
    if not isinstance(records,list) or type(catalog.get('candidate_count')) is not int or catalog.get('candidate_count')!=len(records) or not records:
        raise ValueError('invalid FP16 catalog size')
    names=[record.get('name') for record in records]
    if names!=sorted(set(names)): raise ValueError('FP16 catalog names are not ordered and unique')
    for index,record in enumerate(records):
        if json.dumps(record,sort_keys=True)!=json.dumps(_record(record['name'],index),sort_keys=True):
            raise ValueError('FP16 API contract changed')
    return {'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'softfloat_manifest_sha256':catalog['softfloat_manifest_sha256']}


def generate_source(catalog,names,*,role):
    validate_catalog(catalog)
    indexed={record['name']:record for record in catalog['intrinsics']}
    if not names or len(names)!=len(set(names)) or any(name not in indexed for name in names):
        raise ValueError('invalid FP16 selection')
    if role=='intel': return _native_source([indexed[name] for name in names])
    if role!='portable': raise ValueError('invalid FP16 role')
    records=[indexed[name] for name in names]
    lines=['/* FP16 ports: integer IEEE arithmetic, not Intel execution evidence. */',
           '#include <stdint.h>','#include <string.h>','#include "softfloat.h"',
           'static uint32_t flags_out(uint_fast8_t f) {return ((f&16)?1:0)|((f&8)?2:0)|((f&4)?4:0)|((f&2)?8:0)|((f&1)?16:0);}',
           'static uint16_t read16(const uint8_t *p) {uint16_t v; memcpy(&v,p,2); return v;}',
           'static void write16(uint8_t *p,uint16_t v) {memcpy(p,&v,2);}']
    for record in records:
        op,mode,n=record['operation'],record['mask_mode'],record['lane_count']
        offsets={argument['name']:argument['offset'] for argument in record['arguments']}
        lines.append(f"static void port_{record['ordinal']}(const uint8_t *input,uint8_t *output) {{")
        if mode=='none': lines.append('uint64_t mask=UINT64_MAX;')
        else: lines.append(f"uint64_t mask=0; memcpy(&mask,input+{offsets['k']},{n//8});")
        merge='src' if 'src' in offsets else 'c' if mode=='mask3' else 'a'
        if mode in ('mask','mask3'): lines.append(f"memcpy(output,input+{offsets[merge]},{record['output_byte_length']});")
        else: lines.append(f"memset(output,0,{record['output_byte_length']});")
        lines.append(f'for (unsigned lane=0;lane<{n};++lane) {{if (!((mask>>lane)&1U)) continue;')
        for parameter in ('a','b','c'):
            if parameter in offsets: lines.append(f"float16_t {parameter}={{read16(input+{offsets[parameter]}+2*lane)}};")
        lines.append('float16_t result;')
        if op in ('add','sub','mul','div'): lines.append(f'result=f16_{op}(a,b);')
        elif op=='sqrt': lines.append('result=f16_sqrt(a);')
        else:
            if op in ('fnmadd','fnmsub'): lines.append('a.v^=0x8000U;')
            if op in ('fmsub','fnmsub'): lines.append('c.v^=0x8000U;')
            elif op=='fmaddsub': lines.append('if (!(lane&1U)) c.v^=0x8000U;')
            elif op=='fmsubadd': lines.append('if (lane&1U) c.v^=0x8000U;')
            lines.append('result=f16_mulAdd(a,b,c);')
        lines.append('write16(output+2*lane,result.v); }}')
    lines.extend(['int equilane_ext_call_with_flags(uint32_t op,const uint8_t *input,uint64_t input_size,uint8_t *output,uint64_t output_size,uint32_t *flags) {',
                  'if (!flags) return 2;','switch(op) {'])
    for record in records:
        lines.extend([f"case {record['ordinal']}:",
            f"if (input_size!={record['input_byte_length']} || output_size!={record['output_byte_length']} || !input || !output) return 2; break;"])
    lines.extend(['default:return 1;}','uint_fast8_t old_mode=softfloat_roundingMode,old_flags=softfloat_exceptionFlags;',
                  'softfloat_roundingMode=softfloat_round_near_even; softfloat_exceptionFlags=0;','switch(op) {'])
    lines.extend(f"case {record['ordinal']}:port_{record['ordinal']}(input,output);break;" for record in records)
    lines.extend(['}','*flags=flags_out(softfloat_exceptionFlags);softfloat_roundingMode=old_mode;softfloat_exceptionFlags=old_flags;return 0;}',
       'int equilane_ext_call(uint32_t op,const uint8_t *in,uint64_t size,uint8_t *out,uint64_t capacity) {uint32_t f;return equilane_ext_call_with_flags(op,in,size,out,capacity,&f);}', ''])
    return '\n'.join(lines)


def _native_source(records):
    """Keep native FP16 instructions behind a baseline pointer-ABI guard."""
    lines=['/* Actual Intel FP16 intrinsics; never the SoftFloat implementation. */',
           '#include <stdint.h>','#include <stddef.h>','#include <string.h>',
           '#include <fenv.h>','#include <immintrin.h>','#pragma STDC FENV_ACCESS ON']
    for record in records:
        features=','.join(record['intel_target_features'])
        lines.extend([f'__attribute__((target("{features}"),noinline))',
                      f"static int half_native_{record['ordinal']}(const uint8_t *input,uint8_t *output) {{"])
        for index,argument in enumerate(record['arguments']):
            lines.extend([f"{argument['intel_type']} a{index};",
                          f"_Static_assert(sizeof(a{index})=={argument['byte_length']},\"argument width\");",
                          f"memcpy(&a{index},input+{argument['offset']},{argument['byte_length']});"])
        args=','.join(f'a{index}' for index in range(len(record['arguments'])))
        lines.extend([f"{record['return']['intel_type']} result={record['name']}({args});",
                      f"memcpy(output,&result,{record['output_byte_length']});return 0;}}"])
    lines.extend(['static int equilane_ext_invoke(uint32_t operation,const uint8_t *input,uint64_t input_size,uint8_t *output,uint64_t output_size) {','switch(operation) {'])
    for record in records:
        guard=' && '.join(f'__builtin_cpu_supports("{feature}")' for feature in record['intel_target_features'])
        lines.extend([f"case {record['ordinal']}:",
                      f"if(input_size!={record['input_byte_length']} || output_size!={record['output_byte_length']} || !input || !output) return 2;",
                      f'__builtin_cpu_init(); if (!({guard})) return 3;',
                      f"return half_native_{record['ordinal']}(input,output);"])
    lines.extend(['default:return 1;}}',fp_wrapper_source()])
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guide-archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(build_catalog(args.guide_archive),indent=2)+'\n')
