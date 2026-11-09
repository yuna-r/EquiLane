/* Behavioral and register readback probes, executed in an isolated process. */
#define _GNU_SOURCE
#include <fenv.h>
#include <float.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/auxv.h>
#include <unistd.h>
#if defined(__x86_64__)
#include <cpuid.h>
#include <xmmintrin.h>
#elif defined(__powerpc64__)
#include <altivec.h>
#elif defined(__riscv)
#include <sys/syscall.h>
#include <asm/hwprobe.h>
#endif

#pragma STDC FENV_ACCESS ON

static uint64_t bits(double value) { uint64_t u; memcpy(&u,&value,8); return u; }

/* Leave the process in IEEE mode. Workers call this before touching inputs. */
int equilane_ieee_setup(void) {
    if (fesetenv(FE_DFL_ENV) || fesetround(FE_TONEAREST)) return 0;
#if defined(__x86_64__)
    _mm_setcsr(0x1f80);
#elif defined(__aarch64__)
    uint64_t control; __asm__ volatile("mrs %0, fpcr" : "=r"(control));
    control &= ~((UINT64_C(1)<<24)|(UINT64_C(1)<<25)|(UINT64_C(31)<<8));
    __asm__ volatile("msr fpcr, %0" :: "r"(control));
#elif defined(__powerpc64__)
    __asm__ volatile("mtfsb0 29"); /* FPSCR.NI */
    __vector unsigned int vscr=vec_mfvscr();
    vscr=vec_and(vscr,(__vector unsigned int){~0x10000u,~0x10000u,~0x10000u,~0x10000u});
    vec_mtvscr(vscr);
#endif
    return feclearexcept(FE_ALL_EXCEPT)==0;
}

int equilane_preflight(char *out, size_t capacity) {
    const char *isa="[]", *detector="unknown", *controls="{}";
    char control_text[256];
    if (!equilane_ieee_setup()) return 1;
#if defined(__x86_64__)
    unsigned a,b,c,d;
    if (!__get_cpuid(1,&a,&b,&c,&d) || !(d & bit_SSE2)) return 2;
    isa="[\"sse2\"]"; detector="x86.cpuid.sse2";
    unsigned mxcsr=_mm_getcsr(); unsigned short cw;
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    snprintf(control_text,sizeof(control_text),
      "{\"exception_traps_enabled\":%s,\"mxcsr_daz\":%s,\"mxcsr_ftz\":%s}",
      ((mxcsr&0x1f80)!=0x1f80 || (cw&0x3f)!=0x3f)?"true":"false",
      (mxcsr&0x40)?"true":"false",(mxcsr&0x8000)?"true":"false");
    controls=control_text;
#elif defined(__aarch64__)
    if (!(getauxval(AT_HWCAP)&2ul)) return 2;
    isa="[\"neon\"]"; detector="linux.auxv.asimd";
    uint64_t fpcr; __asm__ volatile("mrs %0, fpcr" : "=r"(fpcr));
    snprintf(control_text,sizeof(control_text),
      "{\"exception_traps_enabled\":%s,\"fpcr_dn\":%s,\"fpcr_fz\":%s}",
      (fpcr&(UINT64_C(31)<<8))?"true":"false",
      (fpcr&(UINT64_C(1)<<25))?"true":"false",(fpcr&(UINT64_C(1)<<24))?"true":"false");
    controls=control_text;
#elif defined(__powerpc64__)
    if (!(getauxval(AT_HWCAP)&0x80ul) || !(getauxval(AT_HWCAP2)&0x80000000ul)) return 2;
    isa="[\"power8\",\"vsx\"]"; detector="linux.auxv.power8-vsx";
    double fpscr; uint64_t fpscr_bits;
    __asm__ volatile("mffs %0" : "=f"(fpscr)); memcpy(&fpscr_bits,&fpscr,8);
    __vector unsigned int vscr=vec_mfvscr(); unsigned int lanes[4]; memcpy(lanes,&vscr,16);
    unsigned nj=(lanes[0]|lanes[1]|lanes[2]|lanes[3])&0x10000u;
    snprintf(control_text,sizeof(control_text),
      "{\"exception_traps_enabled\":%s,\"fpscr_ni\":%s,\"vscr_nj\":%s}",
      (fpscr_bits&0xf8)?"true":"false",(fpscr_bits&4)?"true":"false",nj?"true":"false");
    controls=control_text;
#elif defined(__riscv)
    struct riscv_hwprobe probe={RISCV_HWPROBE_KEY_IMA_EXT_0,0};
    long rc=syscall(__NR_riscv_hwprobe,&probe,1,0,NULL,0);
    if (rc==0 && probe.key==RISCV_HWPROBE_KEY_IMA_EXT_0) {
        if (!(probe.value&RISCV_HWPROBE_IMA_V)) return 2;
        detector="linux.hwprobe.rvv";
    } else {
        /* QEMU user mode can lack hwprobe. Execute V rather than infer it. */
        detector="riscv.instruction.rvv.hwprobe-unavailable";
    }
    unsigned long vl; __asm__ volatile("vsetivli %0,1,e8,m1,ta,ma" : "=r"(vl));
    if (vl!=1) return 2;
    isa="[\"rvv\"]"; controls="{\"exception_traps_enabled\":false}";
#else
    return 2;
#endif
    int rounding[]={FE_TONEAREST,FE_TOWARDZERO,FE_UPWARD,FE_DOWNWARD};
    for (int i=0;i<4;i++) {
        if (fesetround(rounding[i]) || fegetround()!=rounding[i]) return 3;
        volatile double one=1.0, half=0x1p-53, sum=one+half;
        uint64_t expected=i==2?UINT64_C(0x3ff0000000000001):UINT64_C(0x3ff0000000000000);
        if (bits(sum)!=expected) return 4;
        volatile double negative=-1.0, result=negative-half;
        expected=i==3?UINT64_C(0xbff0000000000001):UINT64_C(0xbff0000000000000);
        if (bits(result)!=expected) return 4;
    }
    if (fesetround(FE_TONEAREST)) return 3;
    int flags[]={FE_INVALID,FE_DIVBYZERO,FE_OVERFLOW,FE_UNDERFLOW,FE_INEXACT};
    for (int i=0;i<5;i++) {
        if (feclearexcept(FE_ALL_EXCEPT)) return 5;
        volatile double zero=0.0,one=1.0,two=2.0,three=3.0,large=DBL_MAX,small=DBL_MIN;
        volatile double result;
        switch(i) {
          case 0: result=zero/zero; break;
          case 1: result=one/zero; break;
          case 2: result=large*two; break;
          case 3: result=small*small; break;
          default: result=two/three; break;
        }
        (void)result;
        if (!(fetestexcept(FE_ALL_EXCEPT)&flags[i])) return 6;
    }
    if (!equilane_ieee_setup()) return 1;
    int count=snprintf(out,capacity,
      "{\"available_isa\":%s,\"boolean_controls\":%s,\"detector\":\"%s\","
      "\"fp_exception_flags\":[\"invalid\",\"divide-by-zero\",\"overflow\",\"underflow\",\"inexact\"],"
      "\"rounding_modes\":[\"nearest_even\",\"toward_negative\",\"toward_positive\",\"toward_zero\"],"
      "\"status\":\"passed\"}",isa,controls,detector);
    return count<0 || (size_t)count>=capacity ? 7 : 0;
}
