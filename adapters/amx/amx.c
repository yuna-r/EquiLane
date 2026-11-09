#include "amx.h"
#include "softfloat.h"
#include <string.h>

_Static_assert(sizeof(equilane_amx_state) == 8256, "AMX state byte layout");

static unsigned read16(const uint8_t *p) { return p[0] | ((unsigned)p[1] << 8); }
static uint32_t read32(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void write32(uint8_t *p, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (8 * i));
}
static unsigned rows(const equilane_amx_state *s, unsigned tile) { return s->config[48 + tile]; }
static unsigned cols(const equilane_amx_state *s, unsigned tile) { return read16(s->config + 16 + 2 * tile); }

static int valid_config(const uint8_t *p, int strict_init) {
    if (p[0] > 1) return 0;
    if (!p[0]) {
        if (strict_init) for (unsigned i = 1; i < 64; ++i) if (p[i]) return 0;
        return 1;
    }
    for (unsigned i = 2; i < 16; ++i) if (p[i]) return 0;
    for (unsigned i = 32; i < 48; ++i) if (p[i]) return 0;
    for (unsigned i = 56; i < 64; ++i) if (p[i]) return 0;
    for (unsigned i = 0; i < AMX_TILES; ++i) {
        unsigned c = read16(p + 16 + 2 * i), r = p[48 + i];
        if (c > AMX_ROW_BYTES || r > AMX_ROWS || ((c == 0) != (r == 0))) return 0;
    }
    return 1;
}
static int valid_tile(const equilane_amx_state *s, unsigned tile) {
    return s->config[0] == 1 && tile < AMX_TILES && rows(s, tile) && cols(s, tile);
}
static int bounds(uint64_t size, uint64_t offset, uint64_t width) {
    return offset <= size && width <= size - offset;
}
static uint32_t flush32(uint32_t value) {
    return (value & UINT32_C(0x7f800000)) ? value : value & UINT32_C(0x80000000);
}
static uint32_t add32(uint32_t a, uint32_t b) {
    float32_t x = {flush32(a)}, y = {flush32(b)};
    return flush32(f32_add(x, y).v);
}
static uint32_t fma32(uint32_t a, uint32_t b, uint32_t c) {
    float32_t x = {flush32(a)}, y = {flush32(b)}, z = {flush32(c)};
    return flush32(f32_mulAdd(x, y, z).v);
}
static uint32_t widen16(unsigned bits, int bf16) {
    if (bf16) return (uint32_t)bits << 16;
    float16_t value = {(uint16_t)bits};
    return f16_to_f32(value).v;
}

int equilane_amx_intel_available(void) {
    /* No native backend is advertised without real CPU/OS execution support. */
    return 0;
}

int equilane_amx_portable(uint32_t op, const equilane_amx_state *in,
    equilane_amx_state *out, uint8_t *memory, uint64_t memory_size,
    uint64_t offset, uint64_t stride, uint32_t dst, uint32_t a, uint32_t b) {
    if (!in || !out || op > AMX_CMMIMFP16PS || !valid_config(in->config, 1) ||
        (memory_size && !memory)) return AMX_INVALID_INPUT;
    /* Validate every access before changing the output state or memory. */
    if (op == AMX_LOADCONFIG || op == AMX_STORECONFIG) {
        if (!bounds(memory_size, offset, 64)) return AMX_INVALID_INPUT;
        if (op == AMX_LOADCONFIG && !valid_config(memory + offset, 0)) return AMX_INVALID_INPUT;
    } else if (op == AMX_LOAD || op == AMX_STORE || op == AMX_ZERO) {
        if (!valid_tile(in, dst)) return AMX_INVALID_INPUT;
        if (op != AMX_ZERO) {
            unsigned r = rows(in, dst), c = cols(in, dst);
            if (c % 4 || in->config[1] >= r || !bounds(memory_size, offset, c) ||
                (stride && (r - 1) > (memory_size - offset - c) / stride)) return AMX_INVALID_INPUT;
        }
    } else if (op >= AMX_DPBSSD) {
        if (dst == a || dst == b || a == b || !valid_tile(in, dst) ||
            !valid_tile(in, a) || !valid_tile(in, b) || cols(in, dst) % 4 ||
            cols(in, a) % 4 || cols(in, b) % 4 || cols(in, dst) != cols(in, b) ||
            rows(in, dst) != rows(in, a) || cols(in, a) / 4 != rows(in, b)) return AMX_INVALID_INPUT;
    }
    *out = *in;
    if (op == AMX_RELEASE || (op == AMX_LOADCONFIG && !memory[offset])) {
        memset(out, 0, sizeof(*out));
    } else if (op == AMX_LOADCONFIG) {
        memcpy(out->config, memory + offset, 64);
        memset(out->tiles, 0, sizeof(out->tiles));
    } else if (op == AMX_STORECONFIG) {
        memcpy(memory + offset, in->config, 64);
    } else if (op == AMX_ZERO) {
        memset(out->tiles[dst], 0, AMX_TILE_BYTES);
        out->config[1] = 0;
    } else if (op == AMX_LOAD) {
        unsigned start = in->config[1];
        memset(out->tiles[dst] + start * AMX_ROW_BYTES, 0, AMX_TILE_BYTES - start * AMX_ROW_BYTES);
        for (unsigned r = start; r < rows(in, dst); ++r)
            memcpy(out->tiles[dst] + r * AMX_ROW_BYTES, memory + offset + r * stride, cols(in, dst));
        out->config[1] = 0;
    } else if (op == AMX_STORE) {
        for (unsigned r = in->config[1]; r < rows(in, dst); ++r)
            memcpy(memory + offset + r * stride, in->tiles[dst] + r * AMX_ROW_BYTES, cols(in, dst));
        out->config[1] = 0;
    } else if (op >= AMX_DPBSSD && op <= AMX_DPBUUD) {
        uint8_t result[AMX_TILE_BYTES] = {0};
        int signed_a = op == AMX_DPBSSD || op == AMX_DPBSUD;
        int signed_b = op == AMX_DPBSSD || op == AMX_DPBUSD;
        for (unsigned m = 0; m < rows(in, dst); ++m) {
            for (unsigned n = 0; n < cols(in, dst) / 4; ++n) {
                uint32_t sum = read32(in->tiles[dst] + m * AMX_ROW_BYTES + 4 * n);
                for (unsigned k = 0; k < cols(in, a) / 4; ++k) {
                    for (unsigned j = 0; j < 4; ++j) {
                        unsigned av = in->tiles[a][m * AMX_ROW_BYTES + 4 * k + j];
                        unsigned bv = in->tiles[b][k * AMX_ROW_BYTES + 4 * n + j];
                        int x = signed_a && av >= 128 ? (int)av - 256 : (int)av;
                        int y = signed_b && bv >= 128 ? (int)bv - 256 : (int)bv;
                        sum += (uint32_t)(x * y);
                    }
                }
                write32(result + m * AMX_ROW_BYTES + 4 * n, sum);
            }
        }
        memcpy(out->tiles[dst], result, sizeof(result));
        out->config[1] = 0;
    } else {
        uint8_t result[AMX_TILE_BYTES] = {0};
        uint_fast8_t old_round = softfloat_roundingMode, old_flags = softfloat_exceptionFlags;
        uint_fast8_t old_tininess = softfloat_detectTininess;
        softfloat_roundingMode = softfloat_round_near_even;
        softfloat_exceptionFlags = 0;
        softfloat_detectTininess = softfloat_tininess_afterRounding;
        for (unsigned m = 0; m < rows(in, dst); ++m) {
            for (unsigned n = 0; n < cols(in, dst) / 4; ++n) {
                uint32_t even = 0, odd = 0;
                for (unsigned k = 0; k < cols(in, a) / 4; ++k) {
                    uint32_t ae = widen16(read16(in->tiles[a] + m * AMX_ROW_BYTES + 4 * k), op == AMX_DPBF16PS);
                    uint32_t ao = widen16(read16(in->tiles[a] + m * AMX_ROW_BYTES + 4 * k + 2), op == AMX_DPBF16PS);
                    uint32_t be = widen16(read16(in->tiles[b] + k * AMX_ROW_BYTES + 4 * n), op == AMX_DPBF16PS);
                    uint32_t bo = widen16(read16(in->tiles[b] + k * AMX_ROW_BYTES + 4 * n + 2), op == AMX_DPBF16PS);
                    if (op == AMX_CMMIMFP16PS) {
                        even = fma32(ao, be, even);
                        odd = fma32(ae, bo, odd);
                    } else {
                        if (op == AMX_CMMRLFP16PS) ao ^= UINT32_C(0x80000000);
                        even = fma32(ae, be, even);
                        odd = fma32(ao, bo, odd);
                    }
                }
                uint32_t original = read32(in->tiles[dst] + m * AMX_ROW_BYTES + 4 * n);
                write32(result + m * AMX_ROW_BYTES + 4 * n, add32(original, add32(even, odd)));
            }
        }
        softfloat_roundingMode = old_round;
        softfloat_exceptionFlags = old_flags;
        softfloat_detectTininess = old_tininess;
        memcpy(out->tiles[dst], result, sizeof(result));
        out->config[1] = 0;
    }
    return AMX_OK;
}
