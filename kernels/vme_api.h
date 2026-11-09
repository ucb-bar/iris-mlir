/* vme_api.h -- the vme.h / vme_gemm.h API of the saturn `vme` benchmarks (Miles Rusch), with every word
 * taken from the GENERATED saturn_vme.h instead of typed in.
 *
 * The names and arguments are vme.h's, so a kernel written against it ports by changing its #include:
 *
 *   VME_V(n) VME_MT(t) VME_MTB(t)           .insn operand spellings
 *   VME_TSS_ROW, vme_tss(tile, pattern, i)  tile subset specifier
 *   vme_msetmtype_raw, vme_vsettnt_e8w4, vme_vsettnt_e8w4_alt, vme_vsettnt_e32w1,
 *   vme_vsettn, vme_vsettm, vme_vsettk      configuration
 *   VME_MM_U_U / S_U / U_S / S_S(t, vs2, vs1)   int8 sf.mm
 *   VME_VTZERO(t), VME_VTDISCARD(), VME_VTMV_V_T(vd, tss), VME_VTMV_T_V(tss, vs2)
 *   VME_LOAD_ROW(vreg, ptr)                  (vme_gemm.h)
 *
 * plus vme_enable(), which sets mstatus.MS: saturn's crt.S does that for a benchmark, Merlin's runtime does
 * not, so a kernel calls it once before its first matrix instruction (on every hart that runs it).
 *
 * The sf.mm word, the configuration words, the tile rd fields, the widen codes and the vtype bits all come
 * from saturn_vme.h, which Merlin writes from the unit's RTL (cross-checked against vme.h itself) when it
 * builds or kernel-tests a kernel. This file holds only names.
 */
#ifndef VME_API_H
#define VME_API_H

#include "saturn_vme.h"

/* Register operand spellings for .insn (vector register vn is written as x<n>). */
#define VME_V(n) VME_REG(n)
#define VME_MT(t) "x" VME_STR(VME_MT_RD_##t)   /* tile t */
#define VME_MTB(t) "x" VME_STR(VME_MTB_RD_##t) /* tile t, A operand signed */

#define VME_TSS_ROW VME_TSS_ROW_PATTERN
static inline size_t vme_tss(size_t tile, size_t pattern, size_t index)
{
  return ((tile * VME_TSS_TILE_SCALE) << VME_TSS_TILE_LSB) | (pattern << VME_TSS_PATTERN_LSB) | index;
}

static inline void vme_enable(void)
{
  asm volatile("csrs mstatus, %0" ::"r"((size_t)VME_MSTATUS_MS) : "memory");
}

/* ---- configuration ---------------------------------------------------------------------------------- */

static inline void vme_msetmtype_raw(size_t mtype_bits, size_t vtype_bits)
{
  size_t unused;
  asm volatile(VME_SF_MSETMTYPE("%0", "%1", "%2") : "=r"(unused) : "r"(mtype_bits), "r"(vtype_bits));
}

/* sf.msetmtype twice (the first makes the new element width current, so the second derives LMUL for it),
 * then sf.msettn: returns tn, which is also vl. */
static inline size_t vme_vsettnt_(size_t mtype_bits, size_t vtype_bits, size_t atn)
{
  vme_msetmtype_raw(mtype_bits, vtype_bits);
  vme_msetmtype_raw(mtype_bits, vtype_bits);
  size_t tn;
  asm volatile(VME_SF_VSETTN("%0", "%1") : "=r"(tn) : "r"(atn));
  return tn;
}
static inline size_t vme_vsettnt_e8w4(size_t atn) { return vme_vsettnt_(VME_OPERAND_MTYPE, VME_OPERAND_VTYPE, atn); }
/* altfmt: B (vs1) signed -- with VME_MM_S_* this is a signed x signed int8 matmul. */
static inline size_t vme_vsettnt_e8w4_alt(size_t atn)
{
  return vme_vsettnt_(VME_OPERAND_MTYPE, VME_OPERAND_VTYPE_BSIGNED, atn);
}
static inline size_t vme_vsettnt_e32w1(size_t atn) { return vme_vsettnt_(VME_READOUT_MTYPE, VME_READOUT_VTYPE, atn); }

static inline size_t vme_vsettn(size_t atn)
{
  size_t r;
  asm volatile(VME_SF_VSETTN("%0", "%1") : "=r"(r) : "r"(atn));
  return r;
}
static inline size_t vme_vsettm(size_t atm)
{
  size_t r;
  asm volatile(VME_SF_VSETTM("%0", "%1") : "=r"(r) : "r"(atm));
  return r;
}
static inline size_t vme_vsettk(size_t atk)
{
  size_t r;
  asm volatile(VME_SF_VSETTK("%0", "%1") : "=r"(r) : "r"(atk));
  return r;
}

/* ---- matrix multiply: C[tm,tn] += A[tk,tm]^T * B[tk,tn]; A rows from vs2, B rows from vs1 ----------- */

/* A's sign is rd's A-signed bit (MT vs MTB); B's is vtype.altfmt (vme_vsettnt_e8w4 vs _alt), so u.s/u.u
 * and s.s/s.u are the same instruction. */
#define VME_MM_U_U(t, vs2, vs1) asm volatile(VME_SF_MM_INT(VME_MT(t), VME_V(vs1), VME_V(vs2)) ::: "memory")
#define VME_MM_S_U(t, vs2, vs1) asm volatile(VME_SF_MM_INT(VME_MTB(t), VME_V(vs1), VME_V(vs2)) ::: "memory")
#define VME_MM_U_S(t, vs2, vs1) VME_MM_U_U(t, vs2, vs1)
#define VME_MM_S_S(t, vs2, vs1) VME_MM_S_U(t, vs2, vs1)

/* FP8 uses the independently RTL-derived SF_MM_FP8 word. The same rd[0] bit
 * now selects A=E5M2; vtype.altfmt selects B=E5M2. Clear means OCP E4M3. */
#define VME_MM_E4_E4(t, vs2, vs1) asm volatile(VME_SF_MM_FP8(VME_MT(t), VME_V(vs1), VME_V(vs2)) ::: "memory")
#define VME_MM_E5_E4(t, vs2, vs1) asm volatile(VME_SF_MM_FP8(VME_MTB(t), VME_V(vs1), VME_V(vs2)) ::: "memory")
#define VME_MM_E4_E5(t, vs2, vs1) VME_MM_E4_E4(t, vs2, vs1)
#define VME_MM_E5_E5(t, vs2, vs1) VME_MM_E5_E4(t, vs2, vs1)

/* Packed FP8 is a distinct ABI from the signed-byte/I32 kernels. These are
 * encoded byte operands and raw FP32 accumulation; scale/bias stay outside. */
#ifndef VME_FP8_KERNEL
#define VME_FP8_KERNEL(name) void name(float *c, const uint8_t *at, const uint8_t *b, size_t m, size_t n, size_t k)
#endif

/* ---- tile state --------------------------------------------------------------------------------------- */

#define VME_VTZERO(t) asm volatile(VME_SF_VTZERO_T(VME_MT(t)) ::: "memory")
#define VME_VTDISCARD() asm volatile(VME_SF_VTDISCARD() ::: "memory")
/* tile row/column -> vector group (e32w1) */
#define VME_VTMV_V_T(vd, tss) asm volatile(VME_SF_VTMV_V_T(VME_V(vd), "%0") ::"r"(tss) : "memory")
/* vector group -> tile row/column */
#define VME_VTMV_T_V(tss, vs2) asm volatile(VME_SF_VTMV_T_V("%0", VME_V(vs2)) ::"r"(tss) : "memory")

/* vme_gemm.h */
#define VME_LOAD_ROW(vreg, ptr) asm volatile("vle8.v " vreg ", (%0)" ::"r"(ptr) : "memory")

#endif /* VME_API_H */
