#ifndef EQUILANE_NATIVE_SUITE_RUNTIME_H
#define EQUILANE_NATIVE_SUITE_RUNTIME_H

#include "framework/case_abi.h"
#include <fenv.h>
#include <stddef.h>
#include <stdint.h>

/* Deliberate resource limits are part of the native runner contract. */
#define NS_MAX_JSON (1024U * 1024U)
#define NS_MAX_TOKENS 16384
#define NS_MAX_BUFFERS 16
#define NS_MAX_BUFFER_BYTES 65536U
#define NS_MAX_ALIGNMENT 4096U

typedef struct { int kind, start, end, next, count; } ns_token;
typedef struct {
    int key;
    unsigned char *data;
    size_t size, alignment;
} ns_buffer;
typedef struct {
    const char *json;
    size_t length, position;
    ns_token *tokens;
    int used, operands, immediates, buffer_object, buffer_count;
    ns_buffer buffers[NS_MAX_BUFFERS];
    fenv_t saved_environment;
    uint64_t saved_fp_control;
    uint32_t saved_vector_control[4];
    int environment_active;
} ns_context;

int ns_valid_abi(const ioitf_input *, ioitf_output *);
int ns_init(ns_context *, const ioitf_input *, ioitf_output *, int, int, int);
void ns_destroy(ns_context *);
int ns_value(ns_context *, const char *, const char *, int, int, int, void *, size_t);
int ns_immediate(ns_context *, const char *, int64_t *);
int ns_pointer(ns_context *, const char *, size_t, size_t, void **);
int ns_capacity(ns_context *, ioitf_output *, const char *, int, int, int);
int ns_begin(ns_context *, uint32_t);
int ns_end(ns_context *, uint32_t *);
void ns_encode(ns_context *, ioitf_output *, const char *, int, int, int, const void *);

#endif
