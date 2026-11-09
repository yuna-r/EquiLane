"""Portable half arithmetic is checked independently of Intel execution."""
from __future__ import annotations
import ctypes
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from equilane.softfloat import build_softfloat, validate_softfloat


@unittest.skipUnless(sys.platform=='linux' and shutil.which('clang') and shutil.which('make'), 'requires Linux Clang and make')
class SoftFloatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        cls.lib=ctypes.CDLL(str(build_softfloat(Path(cls.temp.name))))
        cls.lib.equilane_half.argtypes=[ctypes.c_uint32,ctypes.c_uint16,ctypes.c_uint16,ctypes.c_uint16,
                                      ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(ctypes.c_uint32)]
        cls.lib.equilane_half_to_single.argtypes=[ctypes.c_uint16]; cls.lib.equilane_half_to_single.restype=ctypes.c_uint32
        cls.lib.equilane_single_to_half.argtypes=[ctypes.c_uint32]; cls.lib.equilane_single_to_half.restype=ctypes.c_uint16
        cls.lib.equilane_bfloat_to_single.argtypes=[ctypes.c_uint16]; cls.lib.equilane_bfloat_to_single.restype=ctypes.c_uint32

    def call(self,op,a,b=0,c=0,rounding=0):
        output=ctypes.c_uint16(); flags=ctypes.c_uint32()
        self.assertEqual(self.lib.equilane_half(op,a,b,c,rounding,ctypes.byref(output),ctypes.byref(flags)),0)
        return output.value,flags.value

    def test_every_half_bit_pattern_survives_widening_and_narrowing(self):
        for value in range(65536):
            actual=self.lib.equilane_single_to_half(self.lib.equilane_half_to_single(value))
            expected=value | 0x0200 if (value & 0x7c00)==0x7c00 and value & 0x03ff else value
            self.assertEqual(actual,expected,hex(value))

    def test_every_bfloat_bit_pattern_has_exact_widened_bits(self):
        for value in range(65536):
            actual=self.lib.equilane_bfloat_to_single(value)
            expected=value<<16
            self.assertEqual(actual,expected,hex(value))

    def test_arithmetic_and_flags_have_fixed_independent_answers(self):
        self.assertEqual(self.call(0,0x3c00,0x3800),(0x3e00,0))
        self.assertEqual(self.call(1,0x3c00,0x3800),(0x3800,0))
        self.assertEqual(self.call(2,0x4000,0x4200),(0x4600,0))
        self.assertEqual(self.call(3,0x3c00,0x0000),(0x7c00,2))
        self.assertEqual(self.call(3,0x0000,0x0000),(0xfe00,1))
        self.assertEqual(self.call(4,0x4400),(0x4000,0))
        self.assertEqual(self.call(5,0x4000,0x4200,0x3c00),(0x4700,0))

    def test_all_four_rounding_modes_at_halfway(self):
        self.assertEqual(self.call(0,0x3c00,0x1000,rounding=0),(0x3c00,16))
        self.assertEqual(self.call(0,0x3c00,0x1000,rounding=1),(0x3c00,16))
        self.assertEqual(self.call(0,0x3c00,0x1000,rounding=2),(0x3c01,16))
        self.assertEqual(self.call(0,0x3c00,0x1000,rounding=3),(0x3c00,16))


class SoftFloatProvenanceTests(unittest.TestCase):
    def test_vendor_source_matches_its_manifest(self):
        self.assertEqual(len(validate_softfloat()['commit']),40)

if __name__=='__main__': unittest.main()
