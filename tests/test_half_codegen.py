"""Independent lane/mask expectations for the FP16 C implementation."""
from __future__ import annotations
import ctypes
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from equilane.half_codegen import generate_source, validate_catalog
from equilane.softfloat import build_softfloat, VENDOR

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='linux' and shutil.which('clang') and shutil.which('make'), 'requires Linux C toolchain')
class HalfCodegenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        root=Path(cls.temp.name)
        cls.catalog=json.loads((ROOT/'contracts/softfloat-fp16.json').read_text())
        validate_catalog(cls.catalog)
        cls.records=cls.catalog['intrinsics']
        dependency=build_softfloat(root/'dependency')
        source=root/'ports.c';source.write_text(generate_source(cls.catalog,[r['name'] for r in cls.records],role='portable'))
        library=root/'ports.so'
        subprocess.run(['clang','-std=c11','-O2','-fPIC','-shared','-DTHREAD_LOCAL=_Thread_local',
                        '-DSOFTFLOAT_FAST_INT64','-I'+str(VENDOR/'source/include'),str(source),str(dependency),
                        '-Wl,-rpath,'+str(dependency.parent),'-o',str(library)],check=True,capture_output=True)
        cls.lib=ctypes.CDLL(str(library))
        cls.call=cls.lib.equilane_ext_call_with_flags
        cls.call.argtypes=[ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint32)]

    def execute(self,record,values):
        raw=b''.join(values[a['name']] for a in record['arguments'])
        source=ctypes.create_string_buffer(raw); out=ctypes.create_string_buffer(record['output_byte_length']); flags=ctypes.c_uint32()
        self.assertEqual(self.call(record['ordinal'],source,len(raw),out,len(out),ctypes.byref(flags)),0)
        return list(struct.unpack('<'+'H'*record['lane_count'],out.raw)),flags.value

    def test_every_implemented_signature_and_mask_has_independent_lane_answers(self):
        answers={'add':0x4200,'sub':0xbc00,'mul':0x4000,'div':0x3800,'sqrt':0x3c00,
                 'fmadd':0x4500,'fnmadd':0x3c00,'fmsub':0xbc00,'fnmsub':0xc500}
        self.assertEqual(len(self.records),117)
        for record in self.records:
            with self.subTest(name=record['name']):
                n=record['lane_count'];mode=record['mask_mode'];op=record['operation']
                mask=(1<<n)-1 if mode=='none' else sum(1<<lane for lane in range(0,n,2))
                operands={'a':[0x3c00]*n,'b':[0x4000]*n,'c':[0x4200]*n,'src':[0x7c21]*n}
                # Signaling NaNs in inactive lanes must not be evaluated.
                if mode!='none':
                    for lane in range(1,n,2): operands['a'][lane]=0x7c01
                values={k:struct.pack('<'+'H'*n,*v) for k,v in operands.items()}
                values['k']=mask.to_bytes(n//8,'little')
                actual,flags=self.execute(record,values)
                expected=[]
                for lane in range(n):
                    if not ((mask>>lane)&1):
                        expected.append(0 if mode=='maskz' else operands['src'][lane] if 'src' in [a['name'] for a in record['arguments']] else operands['c'][lane] if mode=='mask3' else operands['a'][lane])
                    elif op=='fmaddsub': expected.append(0xbc00 if not lane%2 else 0x4500)
                    elif op=='fmsubadd': expected.append(0x4500 if not lane%2 else 0xbc00)
                    else: expected.append(answers[op])
                self.assertEqual(actual,expected)
                self.assertEqual(flags,0)

    def test_invalid_capacity_and_unknown_operation_leave_output_untouched(self):
        record=self.records[0];source=ctypes.create_string_buffer(record['input_byte_length']);out=ctypes.create_string_buffer(b'X'*record['output_byte_length']);flags=ctypes.c_uint32(0x11223344)
        before=out.raw
        self.assertEqual(self.call(record['ordinal'],source,len(source)-1,out,len(out)-2,ctypes.byref(flags)),2)
        self.assertEqual(out.raw,before);self.assertEqual(flags.value,0x11223344)
        self.assertEqual(self.call(999999,source,len(source),out,len(out),ctypes.byref(flags)),1)
        self.assertEqual(out.raw,before)

if __name__=='__main__':unittest.main()
