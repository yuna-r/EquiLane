#define _POSIX_C_SOURCE 200112L
#include "native_suite_runtime.h"
#include <ctype.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#if defined(__x86_64__)
#include <xmmintrin.h>
#endif

#pragma STDC FENV_ACCESS ON

enum { NS_OBJECT=1, NS_ARRAY=2, NS_STRING=3, NS_INTEGER=4 };

static void ns_space(ns_context *c) {
    while (c->position < c->length && (c->json[c->position]==' ' ||
           c->json[c->position]=='\n' || c->json[c->position]=='\r' || c->json[c->position]=='\t')) ++c->position;
}
static int ns_equal(ns_context *c, int a, int b) {
    if (a<0 || b<0) return 0;
    ns_token *x=&c->tokens[a], *y=&c->tokens[b];
    return x->kind==NS_STRING && y->kind==NS_STRING && x->end-x->start==y->end-y->start &&
           memcmp(c->json+x->start,c->json+y->start,(size_t)(x->end-x->start))==0;
}
static int ns_text(ns_context *c, int token, const char *text) {
    if (token<0 || c->tokens[token].kind!=NS_STRING) return 0;
    ns_token *t=&c->tokens[token]; size_t size=strlen(text);
    return size==(size_t)(t->end-t->start) && memcmp(c->json+t->start,text,size)==0;
}
static int ns_parse(ns_context *c, int depth) {
    ns_space(c);
    if (depth>32 || c->position>=c->length || c->used>=NS_MAX_TOKENS) return -1;
    int id=c->used++; ns_token *t=&c->tokens[id];
    t->start=(int)c->position; t->count=0;
    char first=c->json[c->position++];
    if (first=='{' || first=='[') {
        t->kind=first=='{'?NS_OBJECT:NS_ARRAY;
        char end=first=='{'?'}':']'; ns_space(c);
        if (c->position<c->length && c->json[c->position]==end) ++c->position;
        else {
            for (;;) {
                if (first=='{') {
                    int key=ns_parse(c,depth+1);
                    if (key<0 || c->tokens[key].kind!=NS_STRING) return -1;
                    /* Duplicate member names are rejected, even for unused buffers. */
                    for (int old=id+1; old<key;) {
                        if (ns_equal(c,old,key)) return -1;
                        old=c->tokens[c->tokens[old].next].next;
                    }
                    ns_space(c);
                    if (c->position>=c->length || c->json[c->position++]!=':') return -1;
                }
                if (ns_parse(c,depth+1)<0) return -1;
                ++t->count; ns_space(c);
                if (c->position>=c->length) return -1;
                char separator=c->json[c->position++];
                if (separator==end) break;
                if (separator!=',') return -1;
            }
        }
    } else if (first=='"') {
        t->kind=NS_STRING; t->start=(int)c->position;
        while (c->position<c->length && c->json[c->position]!='"') {
            /* The ABI vocabulary consists entirely of unescaped ASCII names/bits. */
            unsigned char ch=(unsigned char)c->json[c->position++];
            if (ch<0x20 || ch>=0x7f || ch=='\\') return -1;
        }
        if (c->position>=c->length) return -1;
        t->end=(int)c->position++; t->next=c->used; return id;
    } else {
        t->kind=NS_INTEGER;
        if (first=='-') {
            if (c->position>=c->length || !isdigit((unsigned char)c->json[c->position])) return -1;
        } else if (!isdigit((unsigned char)first)) return -1;
        size_t digits=first=='-'?c->position:(c->position-1);
        if (c->json[digits]=='0' && digits+1<c->length && isdigit((unsigned char)c->json[digits+1])) return -1;
        while (c->position<c->length && isdigit((unsigned char)c->json[c->position])) ++c->position;
    }
    t->end=(int)c->position; t->next=c->used; return id;
}
static int ns_get(ns_context *c, int object, const char *name) {
    if (object<0 || c->tokens[object].kind!=NS_OBJECT) return -1;
    for (int key=object+1; key<c->tokens[object].next;) {
        int value=c->tokens[key].next;
        if (ns_text(c,key,name)) return value;
        key=c->tokens[value].next;
    }
    return -1;
}
static int ns_object(ns_context *c, int token, int count) {
    return token>=0 && c->tokens[token].kind==NS_OBJECT && c->tokens[token].count==count;
}
static int ns_integer(ns_context *c, int token, int64_t *value) {
    if (token<0 || c->tokens[token].kind!=NS_INTEGER) return 0;
    ns_token *t=&c->tokens[token]; int position=t->start, negative=c->json[position]=='-';
    if (negative) ++position;
    uint64_t parsed=0, limit=negative?UINT64_C(9223372036854775808):INT64_MAX;
    for (;position<t->end;++position) {
        unsigned digit=(unsigned)(c->json[position]-'0');
        if (parsed>(limit-digit)/10U) return 0;
        parsed=parsed*10U+digit;
    }
    *value=negative?(parsed==UINT64_C(9223372036854775808)?INT64_MIN:-(int64_t)parsed):(int64_t)parsed;
    return 1;
}
static int ns_hex_digit(char value) {
    if (value>='0' && value<='9') return value-'0';
    if (value>='a' && value<='f') return value-'a'+10;
    return -1;
}
static int ns_hex(ns_context *c, int token, unsigned char *output, size_t size, int reverse) {
    if (token<0 || c->tokens[token].kind!=NS_STRING) return 0;
    ns_token *t=&c->tokens[token];
    if ((size_t)(t->end-t->start)!=size*2U+2U || c->json[t->start]!='0' || c->json[t->start+1]!='x') return 0;
    for (size_t i=0;i<size;++i) {
        int a=ns_hex_digit(c->json[t->start+2+2*i]), b=ns_hex_digit(c->json[t->start+3+2*i]);
        if (a<0 || b<0) return 0;
        output[reverse?size-1-i:i]=(unsigned char)(a*16+b);
    }
    return 1;
}
static int ns_identifier(ns_context *c, int token) {
    if (token<0 || c->tokens[token].kind!=NS_STRING) return 0;
    ns_token *t=&c->tokens[token];
    if (t->start==t->end || !(isalpha((unsigned char)c->json[t->start]) || c->json[t->start]=='_')) return 0;
    for (int i=t->start+1;i<t->end;++i)
        if (!(isalnum((unsigned char)c->json[i]) || c->json[i]=='_')) return 0;
    return 1;
}
int ns_valid_abi(const ioitf_input *input, ioitf_output *output) {
    return input && output && input->abi_version==IOITF_ABI_VERSION && output->abi_version==IOITF_ABI_VERSION &&
        input->struct_size==sizeof(*input) && output->struct_size==sizeof(*output) &&
        (input->encoded_arguments.size==0 || input->encoded_arguments.data) &&
        (output->return_capacity==0 || output->encoded_return_value) &&
        (output->effects_capacity==0 || output->encoded_memory_effects);
}
int ns_init(ns_context *c, const ioitf_input *input, ioitf_output *output, int operands, int immediates, int pointers) {
    memset(c,0,sizeof(*c));
    output->return_size=0; output->effects_size=0; output->normalized_fp_exceptions=0; output->status=IOITF_OUTPUT_INVALID_INPUT;
    if (input->fp_mode!=IOITF_FP_MODE_IEEE || input->rounding_mode>IOITF_ROUND_TOWARD_NEGATIVE ||
        input->encoded_arguments.size==0 || input->encoded_arguments.size>NS_MAX_JSON) return 0;
    c->json=(const char *)input->encoded_arguments.data; c->length=(size_t)input->encoded_arguments.size;
    c->tokens=calloc(NS_MAX_TOKENS,sizeof(*c->tokens));
    if (!c->tokens) { output->status=IOITF_OUTPUT_RUNTIME_ERROR; return 0; }
    if (ns_parse(c,0)!=0) return 0;
    ns_space(c);
    if (c->position!=c->length || !ns_object(c,0,3)) return 0;
    c->operands=ns_get(c,0,"operands"); c->immediates=ns_get(c,0,"immediates"); c->buffer_object=ns_get(c,0,"buffers");
    if (!ns_object(c,c->operands,operands) || !ns_object(c,c->immediates,immediates) || c->buffer_object<0 ||
        c->tokens[c->buffer_object].kind!=NS_OBJECT || c->tokens[c->buffer_object].count>NS_MAX_BUFFERS ||
        (!pointers && c->tokens[c->buffer_object].count!=0)) return 0;
    for (int key=c->buffer_object+1; key<c->tokens[c->buffer_object].next;) {
        int object=c->tokens[key].next; int64_t alignment;
        int bytes=ns_get(c,object,"bytes");
        if (!ns_identifier(c,key) || !ns_object(c,object,2) || !ns_integer(c,ns_get(c,object,"alignment"),&alignment) ||
            alignment<1 || alignment>NS_MAX_ALIGNMENT || ((uint64_t)alignment&((uint64_t)alignment-1U)) ||
            bytes<0 || c->tokens[bytes].kind!=NS_STRING) return 0;
        int digits=c->tokens[bytes].end-c->tokens[bytes].start-2;
        if (digits<0 || digits%2 || (unsigned)digits>2U*NS_MAX_BUFFER_BYTES) return 0;
        ns_buffer *buffer=&c->buffers[c->buffer_count++]; buffer->key=key; buffer->alignment=(size_t)alignment; buffer->size=(size_t)digits/2U;
        size_t actual_alignment=buffer->alignment<16U?16U:buffer->alignment;
        if (posix_memalign((void **)&buffer->data,actual_alignment,buffer->size?buffer->size:1U)!=0) {
            output->status=IOITF_OUTPUT_RUNTIME_ERROR; return 0;
        }
        if (!ns_hex(c,bytes,buffer->data,buffer->size,0)) return 0;
        key=c->tokens[object].next;
    }
    return 1;
}
static void ns_restore_controls(ns_context *c) {
#if defined(__x86_64__)
    _mm_setcsr((unsigned)c->saved_fp_control);
#elif defined(__aarch64__)
    __asm__ volatile("msr fpcr,%0" :: "r"(c->saved_fp_control));
#elif defined(__powerpc64__)
    double fpscr; memcpy(&fpscr,&c->saved_fp_control,sizeof(fpscr));
    typedef unsigned int ns_vscr __attribute__((vector_size(16)));
    ns_vscr vscr; memcpy(&vscr,c->saved_vector_control,sizeof(vscr));
    __asm__ volatile("mtfsf 255,%0" :: "f"(fpscr));
    __asm__ volatile("mtvscr %0" :: "v"(vscr));
#endif
}
void ns_destroy(ns_context *c) {
    if (c->environment_active) { (void)fesetenv(&c->saved_environment); ns_restore_controls(c); c->environment_active=0; }
    for (int i=0;i<c->buffer_count;++i) free(c->buffers[i].data);
    free(c->tokens); c->tokens=NULL;
}
int ns_value(ns_context *c, const char *name, const char *element, int width, int lanes, int scalar, void *output, size_t size) {
    int object=ns_get(c,c->operands,name);
    if (!ns_object(c,object,2) || !ns_text(c,ns_get(c,object,"element"),element) || width<1 || width>8 ||
        (size_t)(width*lanes)!=size) return 0;
    if (scalar) return lanes==1 && ns_hex(c,ns_get(c,object,"bits"),output,(size_t)width,1);
    int values=ns_get(c,object,"lanes");
    if (values<0 || c->tokens[values].kind!=NS_ARRAY || c->tokens[values].count!=lanes) return 0;
    int token=values+1;
    for (int i=0;i<lanes;++i) {
        if (!ns_hex(c,token,(unsigned char *)output+(size_t)(i*width),(size_t)width,1)) return 0;
        token=c->tokens[token].next;
    }
    return 1;
}
int ns_immediate(ns_context *c, const char *name, int64_t *value) { return ns_integer(c,ns_get(c,c->immediates,name),value); }
int ns_pointer(ns_context *c, const char *name, size_t alignment, size_t extent, void **output) {
    int object=ns_get(c,c->operands,name), buffer_name=ns_get(c,object,"buffer"); int64_t offset;
    if (!ns_object(c,object,2) || !ns_identifier(c,buffer_name) || !ns_integer(c,ns_get(c,object,"offset"),&offset) || offset<0) return 0;
    for (int i=0;i<c->buffer_count;++i) {
        ns_buffer *buffer=&c->buffers[i];
        if (ns_equal(c,buffer->key,buffer_name)) {
            if (alignment==0 || buffer->alignment%alignment || (uint64_t)offset%alignment ||
                (uint64_t)offset>buffer->size || extent>buffer->size-(size_t)offset) return 0;
            *output=buffer->data+(size_t)offset; return 1;
        }
    }
    return 0;
}

typedef struct { unsigned char *data; size_t size; } ns_writer;
static void ns_put(ns_writer *w, const char *text) {
    size_t size=strlen(text); if (w->data) memcpy(w->data+w->size,text,size); w->size+=size;
}
static void ns_hex_byte(ns_writer *w, unsigned value) {
    static const char digits[]="0123456789abcdef";
    char text[3]={digits[value>>4],digits[value&15U],0}; ns_put(w,text);
}
static size_t ns_return_json(unsigned char *destination, const char *element, int width, int lanes, int scalar, const void *data) {
    ns_writer w={destination,0};
    if (!element) { ns_put(&w,"null"); return w.size; }
    if (scalar) ns_put(&w,"{\"bits\":\"0x");
    else { ns_put(&w,"{\"element\":\""); ns_put(&w,element); ns_put(&w,"\",\"lanes\":[\"0x"); }
    for (int i=0;i<lanes;++i) {
        if (i) ns_put(&w,"\",\"0x");
        for (int j=width-1;j>=0;--j) ns_hex_byte(&w,data?((const unsigned char *)data)[i*width+j]:0);
    }
    if (scalar) { ns_put(&w,"\",\"element\":\""); ns_put(&w,element); ns_put(&w,"\"}"); }
    else ns_put(&w,"\"]}");
    return w.size;
}
static size_t ns_effects_json(ns_context *c, unsigned char *destination) {
    ns_writer w={destination,0}; ns_put(&w,"{");
    /* Input JSON is canonical; its object order is retained in the result. */
    for (int i=0;i<c->buffer_count;++i) {
        ns_buffer *buffer=&c->buffers[i]; ns_token *key=&c->tokens[buffer->key];
        if (i) ns_put(&w,","); ns_put(&w,"\"");
        size_t size=(size_t)(key->end-key->start);
        if (w.data) memcpy(w.data+w.size,c->json+key->start,size); w.size+=size;
        ns_put(&w,"\":{\"byte_offset\":0,\"bytes\":\"0x");
        for (size_t j=0;j<buffer->size;++j) ns_hex_byte(&w,buffer->data[j]);
        ns_put(&w,"\"}");
    }
    ns_put(&w,"}"); return w.size;
}
int ns_capacity(ns_context *c, ioitf_output *output, const char *element, int width, int lanes, int scalar) {
    output->return_size=ns_return_json(NULL,element,width,lanes,scalar,NULL);
    output->effects_size=ns_effects_json(c,NULL);
    return output->return_size<=output->return_capacity && output->effects_size<=output->effects_capacity;
}
int ns_begin(ns_context *c, uint32_t rounding) {
    static const int modes[]={FE_TONEAREST,FE_TOWARDZERO,FE_UPWARD,FE_DOWNWARD};
    if (rounding>3 || fegetenv(&c->saved_environment)!=0) return 0;
    c->environment_active=1;
#if defined(__x86_64__)
    c->saved_fp_control=_mm_getcsr();
#elif defined(__aarch64__)
    __asm__ volatile("mrs %0,fpcr" : "=r"(c->saved_fp_control));
#elif defined(__powerpc64__)
    double fpscr; typedef unsigned int ns_vscr __attribute__((vector_size(16))); ns_vscr vscr;
    __asm__ volatile("mffs %0" : "=f"(fpscr)); memcpy(&c->saved_fp_control,&fpscr,sizeof(fpscr));
    __asm__ volatile("mfvscr %0" : "=v"(vscr)); memcpy(c->saved_vector_control,&vscr,sizeof(vscr));
#endif
    if (fesetenv(FE_DFL_ENV)!=0 || fesetround(modes[rounding])!=0 || feclearexcept(FE_ALL_EXCEPT)!=0) return 0;
#if defined(__x86_64__)
    /* Preserve the rounding mode just selected while clearing FTZ and DAZ. */
    _mm_setcsr(_mm_getcsr() & ~((1U<<15)|(1U<<6)));
#elif defined(__aarch64__)
    uint64_t fpcr; __asm__ volatile("mrs %0,fpcr" : "=r"(fpcr));
    fpcr &= ~((UINT64_C(1)<<24)|(UINT64_C(1)<<25)|(UINT64_C(1)<<19)|UINT64_C(0x9f00));
    __asm__ volatile("msr fpcr,%0" :: "r"(fpcr));
#elif defined(__powerpc64__)
    /* The vector non-Java bit is outside glibc fenv_t. */
    typedef unsigned int ns_vscr_reset __attribute__((vector_size(16)));
    ns_vscr_reset default_vscr={0,0,0,0}; __asm__ volatile("mtvscr %0" :: "v"(default_vscr));
#endif
    __asm__ volatile("" ::: "memory"); return 1;
}
int ns_end(ns_context *c, uint32_t *flags) {
    __asm__ volatile("" ::: "memory");
    int observed=fetestexcept(FE_ALL_EXCEPT); uint32_t normalized=0;
    if (observed&FE_INVALID) normalized|=IOITF_FP_INVALID;
    if (observed&FE_DIVBYZERO) normalized|=IOITF_FP_DIVIDE_BY_ZERO;
    if (observed&FE_OVERFLOW) normalized|=IOITF_FP_OVERFLOW;
    if (observed&FE_UNDERFLOW) normalized|=IOITF_FP_UNDERFLOW;
    if (observed&FE_INEXACT) normalized|=IOITF_FP_INEXACT;
    int status=fesetenv(&c->saved_environment); ns_restore_controls(c); c->environment_active=0; *flags=normalized;
    return observed>=0 && status==0;
}
void ns_encode(ns_context *c, ioitf_output *output, const char *element, int width, int lanes, int scalar, const void *data) {
    output->return_size=ns_return_json(output->encoded_return_value,element,width,lanes,scalar,data);
    output->effects_size=ns_effects_json(c,output->encoded_memory_effects);
    output->status=IOITF_OUTPUT_OK;
}
