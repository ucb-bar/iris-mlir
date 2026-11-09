"""Iris-owned descriptor entry for a producer that already emitted K-major A.

The primitive ABI remains At[K,M], B[K,N], C[M,N]. No quantization, transpose,
scratch packing or arithmetic reassociation occurs in this entry. Arbitrary
legal strides retain a modular scalar fallback. Parallel panels are not enabled.
"""

from __future__ import annotations

import re


def emit(
    kernel_source,
    kernel_function,
    signatures,
    *,
    prefix,
    alignment_bytes,
    ordinary=None,
):
    from merlin.kernels.matrix_shim import ShimUnit

    def identifier(value):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
            raise ValueError("invalid prepacked shim C identifier")
        return value

    prefix, kernel_function = identifier(prefix), identifier(kernel_function)
    if alignment_bytes < 1:
        raise ValueError("explicit positive alignment required")
    if ordinary is None:
        text = f"""#include <stdint.h>
#include <stddef.h>
{kernel_source}
typedef struct {{ int32_t *allocated, *aligned; intptr_t offset, sizes[2], strides[2]; }} {prefix}_memref_2d_i32;
#ifdef __riscv
#define {prefix.upper()}_FENCE() __asm__ volatile("fence rw, rw" ::: "memory")
#else
#define {prefix.upper()}_FENCE() __asm__ volatile("" ::: "memory")
#endif
static unsigned long long g_{prefix}_calls, g_{prefix}_fallbacks;
unsigned long long {prefix}_calls(void) {{ return g_{prefix}_calls; }}
unsigned long long {prefix}_fallbacks(void) {{ return g_{prefix}_fallbacks; }}
"""
    else:
        text = str(ordinary)
    for symbol, shape in sorted(signatures.items()):
        identifier(symbol)
        if len(shape) != 3 or any(type(v) is not int or v <= 0 for v in shape):
            raise ValueError("prepacked A requires a positive static (M,N,K) signature")
        m, n, k = shape
        text += f"""
/* {symbol}: A is ALREADY At[K,M]. No A-to-At copy or pack scratch. */
{prefix}_memref_2d_i32 {symbol}(
 int8_t *a_alloc, int8_t *a_aligned, intptr_t a_off, intptr_t a_s0, intptr_t a_s1, intptr_t a_st0, intptr_t a_st1,
 int8_t *b_alloc, int8_t *b_aligned, intptr_t b_off, intptr_t b_s0, intptr_t b_s1, intptr_t b_st0, intptr_t b_st1,
 int32_t *c_alloc, int32_t *c_aligned, intptr_t c_off, intptr_t c_s0, intptr_t c_s1, intptr_t c_st0, intptr_t c_st1)
{{
 (void)a_alloc; (void)b_alloc;
 if (a_s0 != {k} || a_s1 != {m} || b_s0 != {k} || b_s1 != {n} || c_s0 != {m} || c_s1 != {n}) __builtin_trap();
 const int8_t *A=a_aligned+a_off, *B=b_aligned+b_off;
 int32_t *C=c_aligned+c_off;
 {prefix}_memref_2d_i32 result={{c_alloc,c_aligned,c_off,{{c_s0,c_s1}},{{c_st0,c_st1}}}};
 ++g_{prefix}_calls;
 {prefix.upper()}_FENCE();
 const int dense=(a_st0=={m} && a_st1==1 && b_st0=={n} && b_st1==1 && c_st0=={n} && c_st1==1);
 const int aligned=((uintptr_t)A % (uintptr_t){alignment_bytes})==0 && ((uintptr_t)B % (uintptr_t){alignment_bytes})==0 && ((uintptr_t)C % (uintptr_t){alignment_bytes})==0;
 if(dense && aligned) {{
  {kernel_function}(C,A,B,(const int32_t*)0,{m},{n},{k});
 }} else {{
  ++g_{prefix}_fallbacks;
  for(size_t i=0;i<{m};++i) for(size_t j=0;j<{n};++j) {{
   uint32_t sum=0;
   for(size_t q=0;q<{k};++q) sum+=(uint32_t)((int32_t)A[(intptr_t)q*a_st0+(intptr_t)i*a_st1]*(int32_t)B[(intptr_t)q*b_st0+(intptr_t)j*b_st1]);
   C[(intptr_t)i*c_st0+(intptr_t)j*c_st1]=(int32_t)sum;
  }}
 }}
 return result;
}}
"""
    return ShimUnit(
        text,
        scratch_bytes=0 if ordinary is None else ordinary.scratch_bytes,
        symbols=tuple(
            sorted(
                set(signatures)
                | (set(ordinary.symbols) if ordinary is not None else set())
            )
        ),
    )
