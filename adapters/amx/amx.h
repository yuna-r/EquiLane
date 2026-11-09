#ifndef EQUILANE_AMX_H
#define EQUILANE_AMX_H
#include <stddef.h>
#include <stdint.h>

enum { AMX_TILES = 8, AMX_ROWS = 16, AMX_ROW_BYTES = 64, AMX_TILE_BYTES = 1024 };
enum { AMX_OK = 0, AMX_INVALID_INPUT = 1, AMX_UNSUPPORTED = 2 };
enum { AMX_LOADCONFIG, AMX_STORECONFIG, AMX_RELEASE, AMX_LOAD, AMX_STORE,
       AMX_ZERO, AMX_DPBSSD, AMX_DPBSUD, AMX_DPBUSD, AMX_DPBUUD,
       AMX_DPBF16PS, AMX_DPFP16PS, AMX_CMMRLFP16PS, AMX_CMMIMFP16PS };
typedef struct {
    uint8_t config[64];
    uint8_t tiles[AMX_TILES][AMX_TILE_BYTES];
} equilane_amx_state;

/* The portable implementation below never executes an Intel AMX instruction. */
int equilane_amx_portable(uint32_t operation, const equilane_amx_state *input,
    equilane_amx_state *output, uint8_t *memory, uint64_t memory_size,
    uint64_t offset, uint64_t stride, uint32_t dst, uint32_t a, uint32_t b);
int equilane_amx_intel_available(void);
#endif
