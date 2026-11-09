"""AMX state, memory and arithmetic checks with independent bit expectations."""
from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import random
import shutil
import struct
import tempfile
import unittest

from equilane import amx


def _tile(state, index):
    return bytearray.fromhex(state["tiles"][index][2:])


def _put(state, index, raw):
    state["tiles"][index] = "0x" + bytes(raw).hex()


def _word(raw, offset, value, width=4):
    raw[offset:offset + width] = value.to_bytes(width, "little")


def _payload(shapes, *, fill=0xa5, arguments=None, memory=None):
    return {"state": amx.make_state(shapes, fill=fill), "arguments": arguments or {},
            "buffers": {} if memory is None else {"memory": {"bytes": "0x" + memory.hex()}}}


@unittest.skipUnless(shutil.which("clang") and shutil.which("make"), "AMX portable C verification needs Clang and make")
class AMXTests(unittest.TestCase):
    calls = 0

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.library = ctypes.CDLL(str(amx.build_amx(cls.temporary.name)))
        cls.calls = 0

    def both(self, name, payload):
        original = copy.deepcopy(payload)
        python = amx.execute_python(name, payload)
        native = amx.execute_c(name, payload, self.library)
        type(self).calls += 1
        self.assertEqual(native, python, name)
        self.assertEqual(payload, original, "AMX execution modified the caller's record")
        return python

    def dot_payload(self, kind, m=2, k=2, n=2):
        record = _payload({0: (m, 4 * n), 1: (m, 4 * k), 2: (k, 4 * n)},
                          arguments={"dst": 0, "a": 1, "b": 2})
        dst, a, b = (_tile(record["state"], i) for i in range(3))
        floating = kind not in ("dpbssd", "dpbsud", "dpbusd", "dpbuud")
        for row in range(m):
            for col in range(n):
                _word(dst, row * 64 + col * 4, 0x3f800000 if floating else 0)
        for row in range(m):
            for col in range(k):
                _word(a, row * 64 + col * 4, (0x3f803f80 if kind == "dpbf16ps" else 0x3c003c00) if floating else 0x01010101)
        for row in range(k):
            for col in range(n):
                _word(b, row * 64 + col * 4, (0x3f803f80 if kind == "dpbf16ps" else 0x3c003c00) if floating else 0x01010101)
        for i, raw in enumerate((dst, a, b)):
            _put(record["state"], i, raw)
        return record

    def test_exactly_27_guide_apis_have_executable_paths(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / "adapters/amx/contract.json").read_text())
        self.assertEqual(set(amx.OPERATIONS), {entry["name"] for entry in contract["operations"]})
        self.assertEqual(len(amx.OPERATIONS), 27)
        for name, kind in amx.OPERATIONS.items():
            with self.subTest(name=name):
                if kind.startswith(("dp", "cmm")):
                    record = self.dot_payload(kind)
                    if name.startswith("__"):
                        record["arguments"] = {"dst": 0, "src0": 1, "src1": 2}
                    result = self.both(name, record)
                    expected = 0x3f800000 if kind == "cmmrlfp16ps" else 0x40a00000 if kind.startswith(("dpbf", "dpfp", "cmm")) else 8
                    raw = _tile(result["state"], 0)
                    for row in range(2):
                        self.assertEqual([int.from_bytes(raw[row * 64 + col * 4:row * 64 + col * 4 + 4], "little") for col in range(2)], [expected] * 2)
                        self.assertEqual(raw[row * 64 + 8:(row + 1) * 64], bytes(56))
                    self.assertEqual(raw[128:], bytes(896))
                elif kind in ("load", "store"):
                    args = {"dst" if kind == "load" else "src": 0,
                            "base": {"buffer": "memory", "offset": 3}, "stride": 13}
                    self.both(name, _payload({0: (2, 8)}, arguments=args, memory=bytes(range(40))))
                elif kind == "zero":
                    key = "dst" if name.startswith("__") else "tdest"
                    result = self.both(name, _payload({0: (3, 7)}, arguments={key: 0}))
                    self.assertEqual(_tile(result["state"], 0), bytes(1024))
                elif kind == "release":
                    result = self.both(name, _payload({0: (16, 64)}))
                    self.assertEqual(result["state"], amx.make_state())
                else:
                    config = bytes.fromhex(amx.make_state({0: (16, 64)})["config"][2:])
                    self.both(name, _payload({0: (3, 12)}, arguments={"mem_addr": {"buffer": "memory", "offset": 1}}, memory=b"\x7e" + config + b"\xa5"))

    def test_loadconfig_resets_all_tile_data_and_palette_zero_ignores_payload(self):
        for palette in (0, 1):
            config = bytearray.fromhex(amx.make_state({0: (16, 64), 7: (1, 4)})["config"][2:])
            if palette == 0:
                config[:] = bytes([0] + [0xff] * 63)
            record = _payload({0: (4, 12)}, arguments={"mem_addr": {"buffer": "memory", "offset": 7}}, memory=b"\xcc" * 7 + config + b"\xdd")
            result = self.both("_tile_loadconfig", record)
            self.assertEqual(result["state"]["tiles"], ["0x" + "00" * 1024] * 8)
            self.assertEqual(result["state"]["config"], "0x" + (bytes(config) if palette else bytes(64)).hex())
            self.assertEqual(result["buffers"], record["buffers"])

    def test_strided_memory_restart_padding_and_overlap(self):
        for load, shape in ((load, shape) for load in (True, False) for shape in ((1, 4), (1, 64), (3, 12), (16, 64))):
            rows, columns = shape
            for stride in (0, columns, columns + 7):
                for start in sorted({0, rows - 1}):
                    for offset in (0, 1, 15):
                        length = offset + (rows - 1) * stride + columns + 9
                        initial = bytes(i % 256 for i in range(length))
                        args = {"dst" if load else "src": 0, "base": {"buffer": "memory", "offset": offset}, "stride": stride}
                        record = _payload({0: shape}, arguments=args, memory=initial)
                        config = bytearray.fromhex(record["state"]["config"][2:]); config[1] = start
                        record["state"]["config"] = "0x" + config.hex()
                        tile = bytearray((17 + i * 29) % 256 for i in range(1024)); _put(record["state"], 0, tile)
                        expected_tile, expected_memory = tile.copy(), bytearray(initial)
                        if load:
                            expected_tile[start * 64:] = bytes(1024 - start * 64)
                        for row in range(start, rows):
                            pos = offset + row * stride
                            if load:
                                expected_tile[row * 64:row * 64 + columns] = initial[pos:pos + columns]
                            else:
                                expected_memory[pos:pos + columns] = tile[row * 64:row * 64 + columns]
                        with self.subTest(load=load, shape=shape, stride=stride, start=start, offset=offset):
                            result = self.both("_tile_loadd" if load else "_tile_stored", record)
                            self.assertEqual(_tile(result["state"], 0), expected_tile)
                            self.assertEqual(result["buffers"]["memory"]["bytes"], "0x" + expected_memory.hex())
                            self.assertEqual(bytes.fromhex(result["state"]["config"][2:])[1], 0)

    def int8_all_pairs(self, operation):
        signed_a = operation in ("dpbssd", "dpbsud")
        signed_b = operation in ("dpbssd", "dpbusd")
        for x_start in range(0, 256, 16):
            for y_start in range(0, 256, 16):
                record = _payload({0: (16, 64), 1: (16, 4), 2: (1, 64)}, arguments={"dst": 0, "a": 1, "b": 2})
                dst, left, right = (_tile(record["state"], i) for i in range(3))
                for row in range(16):
                    _word(left, row * 64, x_start + row)
                    for col in range(16):
                        _word(dst, row * 64 + col * 4, (0xfffffff0 + row + col) & 0xffffffff)
                for col in range(16):
                    _word(right, 4 * col, y_start + col)
                for i, raw in enumerate((dst, left, right)):
                    _put(record["state"], i, raw)
                with self.subTest(x_start=x_start, y_start=y_start):
                    result = self.both("_tile_" + operation, record)
                    actual = _tile(result["state"], 0)
                    for row in range(16):
                        x = x_start + row
                        x = x - 256 if signed_a and x >= 128 else x
                        for col in range(16):
                            y = y_start + col
                            y = y - 256 if signed_b and y >= 128 else y
                            expected = (0xfffffff0 + row + col + x * y) & 0xffffffff
                            self.assertEqual(int.from_bytes(actual[row * 64 + col * 4:row * 64 + col * 4 + 4], "little"), expected)

    def test_fp16_and_bf16_use_two_partial_sums_before_accumulator(self):
        for operation, big, one in (("dpfp16ps", 0x6c00, 0x3c00), ("dpbf16ps", 0x4580, 0x3f80)):
            record = self.dot_payload(operation, 1, 2, 1)
            a, b, c = (_tile(record["state"], i) for i in (1, 2, 0))
            _word(c, 0, 0)
            for offset, value in ((0, big), (2, big | 0x8000), (4, one), (6, 0)):
                _word(a, offset, value, 2)
            for offset, value in ((0, big), (2, big), (64, one), (66, 0)):
                _word(b, offset, value, 2)
            for i, raw in ((0, c), (1, a), (2, b)):
                _put(record["state"], i, raw)
            result = self.both("_tile_" + operation, record)
            self.assertEqual(_tile(result["state"], 0)[:4], bytes(4), "interleaved scalar accumulation would incorrectly produce 1")

    def test_maximum_geometry_full_dot_groups_and_uint32_wrap(self):
        for operation, x, y in (("dpbssd", 0x80, 0x80), ("dpbsud", 0x80, 0xff),
                                 ("dpbusd", 0xff, 0x80), ("dpbuud", 0xff, 0xff)):
            record = _payload({0: (16, 64), 1: (16, 64), 2: (16, 64)},
                              arguments={"dst": 0, "a": 1, "b": 2})
            _put(record["state"], 0, b"\xff" * 1024)
            _put(record["state"], 1, bytes([x]) * 1024)
            _put(record["state"], 2, bytes([y]) * 1024)
            signed_x = x - 256 if operation in ("dpbssd", "dpbsud") else x
            signed_y = y - 256 if operation in ("dpbssd", "dpbusd") else y
            expected = ((0xffffffff + 64 * signed_x * signed_y) & 0xffffffff).to_bytes(4, "little") * 256
            result = self.both("_tile_" + operation, record)
            self.assertEqual(_tile(result["state"], 0), expected)

    def test_every_fp16_encoding_widens_against_python_binary_formats(self):
        for bits in range(65536):
            if bits & 0x7c00 == 0x7c00 and bits & 0x3ff:
                expected = ((bits & 0x8000) << 16) | 0x7fc00000 | ((bits & 0x3ff) << 13)
            else:
                value = struct.unpack("<e", bits.to_bytes(2, "little"))[0]
                expected = int.from_bytes(struct.pack("<f", value), "little")
            self.assertEqual(amx._widen16(bits, False), expected, f"half={bits:#06x}")

    def test_fp16_subnormals_retained_bf16_subnormals_and_fp32_inputs_flushed(self):
        cases = (("dpfp16ps", 1, 0x3c00, 0x33800000), ("dpbf16ps", 1, 0x3f80, 0),
                 ("dpbf16ps", 0x0080, 0x0080, 0))
        for operation, left, right, expected in cases:
            record = self.dot_payload(operation, 1, 1, 1)
            for i, value in ((0, 1), (1, left), (2, right)):
                raw = _tile(record["state"], i); _word(raw, 0, value); _put(record["state"], i, raw)
            result = self.both("_tile_" + operation, record)
            self.assertEqual(int.from_bytes(_tile(result["state"], 0)[:4], "little"), expected)

    def test_floating_special_patterns_and_random_fma_rounding(self):
        random_bits = random.Random(0x414d5801)
        for operation in ("dpbf16ps", "dpfp16ps", "cmmrlfp16ps", "cmmimfp16ps"):
            specials = (0, 0x8000, 1, 0x8001, 0x7f80, 0xff80, 0x7f81, 0x7fc2, 0x3f80, 0xbf80) if operation == "dpbf16ps" else (0, 0x8000, 1, 0x8001, 0x7c00, 0xfc00, 0x7c01, 0x7e42, 0x3c00, 0xbc00)
            for index in range(160):
                record = self.dot_payload(operation, 1, 2, 1)
                destination = _tile(record["state"], 0)
                accumulator = (0, 0x80000000, 1, 0x7f800000, 0xff800000, 0x7fc00123, 0x7f800045, 0x7f7fffff)[index % 8] if index < 100 else random_bits.randrange(1 << 32)
                _word(destination, 0, accumulator)
                _put(record["state"], 0, destination)
                for tile, count in ((1, 4), (2, 4)):
                    raw = _tile(record["state"], tile)
                    for element in range(count):
                        pair_index = index % 10 if tile == 1 else index // 10
                        value = specials[(pair_index + element * 3) % len(specials)] if index < 100 else random_bits.randrange(65536)
                        position = element * 2 if tile == 1 else (element // 2) * 64 + (element % 2) * 2
                        _word(raw, position, value, 2)
                    _put(record["state"], tile, raw)
                with self.subTest(operation=operation, pattern=index):
                    self.both("_tile_" + operation, record)

    def test_invalid_shapes_aliases_memory_and_config_are_atomic(self):
        cases = []
        base = self.dot_payload("dpbssd")
        for key, value in (("dst", 8), ("a", 0), ("b", 1), ("a", True)):
            record = copy.deepcopy(base); record["arguments"][key] = value; cases.append(("_tile_dpbssd", record))
        for index, value in ((0, 2), (2, 1), (32, 1), (56, 1), (48, 17), (16, 65), (48, 0)):
            record = copy.deepcopy(base); config = bytearray.fromhex(record["state"]["config"][2:]); config[index] = value
            record["state"]["config"] = "0x" + config.hex(); cases.append(("_tile_dpbssd", record))
        for offset, stride in ((0, 20), (39, 0), (2**64 - 1, 1), (0, 2**64 - 1)):
            record = _payload({0: (2, 8)}, arguments={"dst": 0, "base": {"buffer": "memory", "offset": offset}, "stride": stride}, memory=bytes(16))
            cases.append(("_tile_loadd", record))
        for name, record in cases:
            before = copy.deepcopy(record)
            with self.subTest(name=name, arguments=record["arguments"], config=record["state"]["config"]):
                with self.assertRaises(amx.AMXValidationError):
                    amx.execute_python(name, record)
                with self.assertRaises(amx.AMXValidationError):
                    amx.execute_c(name, record, self.library)
                self.assertEqual(record, before)

    def test_c_rejects_before_touching_output_or_memory(self):
        valid = self.dot_payload("dpbssd")
        raw = bytes.fromhex(valid["state"]["config"][2:]) + b"".join(bytes.fromhex(value[2:]) for value in valid["state"]["tiles"])
        source = amx._State.from_buffer_copy(raw)
        for op, dst, a, b in ((6, 0, 0, 2), (6, 8, 1, 2), (14, 0, 1, 2), (3, 0, 1, 2)):
            output = amx._State(); ctypes.memset(ctypes.byref(output), 0x5a, ctypes.sizeof(output))
            memory = (ctypes.c_uint8 * 8)(*([0xa5] * 8))
            result = self.library.equilane_amx_portable(op, ctypes.byref(source), ctypes.byref(output), memory, 8, 0, 8, dst, a, b)
            self.assertEqual(result, 1)
            self.assertEqual(bytes(output), b"\x5a" * ctypes.sizeof(output))
            self.assertEqual(bytes(memory), b"\xa5" * 8)

    def test_intel_native_never_falls_back_to_portable_reference(self):
        self.assertEqual(self.library.equilane_amx_intel_available(), 0)
        for name in amx.OPERATIONS:
            self.assertEqual(amx.execute(name, {}, backend="intel-native")["status"], "unsupported")


for _operation in ("dpbssd", "dpbsud", "dpbusd", "dpbuud"):
    def _make_test(operation):
        def test(self):
            self.int8_all_pairs(operation)
        return test
    setattr(AMXTests, "test_" + _operation + "_all_65536_byte_pairs", _make_test(_operation))


if __name__ == "__main__":
    unittest.main()
