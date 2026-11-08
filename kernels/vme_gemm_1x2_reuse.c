/* Experimental signed-INT8 1x2-N reuse kernel. See docs/VME_1X2_REUSE.md.
 * The original generic kernel remains the fallback and the package default.
 * No VLEN constant is used: TE and readout state come from the target.
 */
/* vme_gemm_2x2_bias.c -- Miles Rusch's vme_gemm_2x2_bias (saturn `vme`,
 * benchmarks/vme-fused-lm2-gemm-transpose/main.c) on Merlin's matrix-kernel ABI.
 *
 * Written against vme_api.h, which is vme.h's API with every word taken from the generated saturn_vme.h
 * (Merlin writes it at build time from the unit's RTL). Copy this file to start a kernel of your own.
 *
 * The ABI (what Merlin calls for every int8 contraction it routes to the VME unit):
 *
 *   C[i][j] = (bias ? bias[j] : 0) + sum_k at[k*m + i] * b[k*n + j]       int32, wrapping; C is WRITTEN
 *
 *   at  the left operand, K-major (k rows of m) -- vme_gemm.h's `at` -- packed by Merlin into aligned scratch
 *   b   the right operand, K-major (k rows of n), the weights in place
 *   c   int32, row-major m x n; Merlin requantizes it to int8 after this returns
 *   bias may be NULL (the model's shim always passes NULL; kernel-test also checks a bias)
 *   m, n, k >= 1, any values; at, b and c are aligned to VME_ALIGN_BYTES. Operands are SIGNED int8.
 *
 * What changed from the benchmark, and why:
 *   * int8_t / int32_t operands (the model's are signed); the matmul is VME_MM_S_S after
 *     vme_vsettnt_e8w4_alt, i.e. signed A (rd bit) x signed B (altfmt), exactly as main.c does;
 *   * M and N need not be multiples of 2*TE: a 2TE x 2TE block the matrix covers runs the benchmark's 2x2
 *     super-tile, anything else runs vme_gemm_tiled's one-tile loop with tm/tn clamped -- loading exactly
 *     tm (A) and tn (B) lanes, so nothing past the end of a panel is read;
 *   * the tile edge is asked of the hardware, not passed in, and vme_enable() sets mstatus.MS (saturn's
 *     crt.S does that for a benchmark; Merlin's runtime does not);
 *   * one tile row is read out at a time (v16, bias in v24), not the benchmark's two interleaved streams.
 */
#include "vme_api.h"

/* The register plan below (vme_gemm.h's) puts consecutive reduction rows two registers apart, which is
 * right for kmax = 4 at e8w4. A unit with another kmax needs another plan; refuse to build rather than
 * load rows into the wrong registers. */
#if VME_KSTEP != 4 || VME_ROW_STRIDE != 2
#error "vme_gemm_2x2_bias's register plan (v0/v2/v4/v6, ...) is for kmax = 4"
#endif

/* vme_gemm.h: A rows (k0..k0+3) of m-block 0 -> v0, v2, v4, v6; of m-block 1 -> v8..v14;
 * B rows of n-block 0 -> v16..v22; of n-block 1 -> v24..v30. */
static inline void vme_load_k4(const int8_t *a0, const int8_t *a1, const int8_t *b0, const int8_t *b1,
                               size_t lda, size_t ldb, size_t kk)
{
  VME_LOAD_ROW("v0", a0); VME_LOAD_ROW("v8", a1); VME_LOAD_ROW("v16", b0); VME_LOAD_ROW("v24", b1);
  if (kk > 1) {
    VME_LOAD_ROW("v2", a0 + lda); VME_LOAD_ROW("v10", a1 + lda);
    VME_LOAD_ROW("v18", b0 + ldb); VME_LOAD_ROW("v26", b1 + ldb);
  }
  if (kk > 2) {
    VME_LOAD_ROW("v4", a0 + 2 * lda); VME_LOAD_ROW("v12", a1 + 2 * lda);
    VME_LOAD_ROW("v20", b0 + 2 * ldb); VME_LOAD_ROW("v28", b1 + 2 * ldb);
  }
  if (kk > 3) {
    VME_LOAD_ROW("v6", a0 + 3 * lda); VME_LOAD_ROW("v14", a1 + 3 * lda);
    VME_LOAD_ROW("v22", b0 + 3 * ldb); VME_LOAD_ROW("v30", b1 + 3 * ldb);
  }
}

static inline void vme_load_rows(int a_side, const int8_t *p, size_t ld, size_t kk)
{
  if (a_side) {
    VME_LOAD_ROW("v0", p);
    if (kk > 1) VME_LOAD_ROW("v2", p + ld);
    if (kk > 2) VME_LOAD_ROW("v4", p + 2 * ld);
    if (kk > 3) VME_LOAD_ROW("v6", p + 3 * ld);
  } else {
    VME_LOAD_ROW("v16", p);
    if (kk > 1) VME_LOAD_ROW("v18", p + ld);
    if (kk > 2) VME_LOAD_ROW("v20", p + 2 * ld);
    if (kk > 3) VME_LOAD_ROW("v22", p + 3 * ld);
  }
}

/* Tile `t`'s row `r` -> v16 (+ the bias slice in v24) -> dst. Runs in the e32w1 readout configuration. */
static inline void store_row(int32_t *dst, const int32_t *bias, size_t t, size_t r)
{
  VME_VTMV_V_T(16, vme_tss(t, VME_TSS_ROW, r));
  if (bias) {
    asm volatile("vle32.v v24, (%0)" ::"r"(bias) : "memory");
    asm volatile("vadd.vv v16, v16, v24");
  }
  asm volatile("vse32.v v16, (%0)" ::"r"(dst) : "memory");
}

/* Original benchmark's independent v16/v20 streams. Readout LMUL<=4
 * is checked by the native driver; NULL bias is the certified corpus. */
#ifndef _OPENMP
static inline void store_two_rows(int32_t*d0,int32_t*d1,const int32_t*b0,const int32_t*b1,size_t t0,size_t t1,size_t r){
 VME_VTMV_V_T(16,vme_tss(t0,VME_TSS_ROW,r));
 VME_VTMV_V_T(20,vme_tss(t1,VME_TSS_ROW,r));
 if(b0){asm volatile("vle32.v v24,(%0)"::"r"(b0):"memory");asm volatile("vle32.v v28,(%0)"::"r"(b1):"memory");asm volatile("vadd.vv v16,v16,v24");asm volatile("vadd.vv v20,v20,v28");}
 asm volatile("vse32.v v16,(%0)"::"r"(d0):"memory");
 asm volatile("vse32.v v20,(%0)"::"r"(d1):"memory");
}

#endif

/* The benchmark's vme_gemm_2x2_bias body: one 2TE x 2TE block at (m0, n0), four tiles sharing every load. */
static void gemm_2x2_bias_block(int32_t *c, const int8_t *at, const int8_t *b, const int32_t *bias,
                                size_t M, size_t N, size_t K, size_t te, size_t m0, size_t n0)
{
  vme_enable();
  vme_vsettnt_e8w4_alt(te);
  vme_vsettm(te);
  VME_VTZERO(0); VME_VTZERO(1); VME_VTZERO(2); VME_VTZERO(3);
  size_t k0 = 0;
  const int8_t *a0 = at + m0;
  const int8_t *bb = b + n0;
  vme_vsettk(4);
  while (K - k0 > 4) {
    vme_load_k4(a0, a0 + te, bb, bb + te, M, N, 4);
    VME_MM_S_S(0, 0, 16); VME_MM_S_S(1, 0, 24);
    VME_MM_S_S(2, 8, 16); VME_MM_S_S(3, 8, 24);
    a0 += 4 * M;
    bb += 4 * N;
    k0 += 4;
  }
  {
    /* final chunk: 1..4 rows, with its own sf.msettk */
    const size_t kk = K - k0;
    vme_load_k4(a0, a0 + te, bb, bb + te, M, N, kk);
    vme_vsettk(kk);
    VME_MM_S_S(0, 0, 16); VME_MM_S_S(1, 0, 24);
    VME_MM_S_S(2, 8, 16); VME_MM_S_S(3, 8, 24);
  }
  vme_vsettnt_e32w1(te);
  for (size_t r = 0; r < te; r++)
    for (size_t t = 0; t < 4; t++) {
      const size_t col0 = n0 + (t & 1) * te;
      store_row(c + (m0 + (t >> 1) * te + r) * N + col0, bias ? bias + col0 : (const int32_t *)0, t, r);
    }
  VME_VTDISCARD();
}

/* vme_gemm_tiled's body for one tm x tn tile (tm, tn <= TE), loading exactly tm and tn lanes. */
static void gemm_tile_bias(int32_t *c, const int8_t *at, const int8_t *b, const int32_t *bias,
                           size_t M, size_t N, size_t K, size_t m0, size_t n0, size_t tm, size_t tn)
{
  vme_enable();
  vme_vsettnt_e8w4_alt(tn);
  vme_vsettm(tm);
  VME_VTZERO(0);
  size_t k0 = 0;
  const int8_t *a0 = at + m0;
  const int8_t *bb = b + n0;
  while (k0 < K) {
    const size_t kk = (K - k0) < 4 ? (K - k0) : 4;
    vme_vsettn(tm);                /* vl = tm: A's rows */
    vme_load_rows(1, a0, M, kk);
    vme_vsettn(tn);                /* vl = tn: B's rows, and the tile's N for sf.mm */
    vme_load_rows(0, bb, N, kk);
    vme_vsettk(kk);
    VME_MM_S_S(0, 0, 16);
    a0 += kk * M;
    bb += kk * N;
    k0 += kk;
  }
  vme_vsettnt_e32w1(tn);
  for (size_t r = 0; r < tm; r++)
    store_row(c + (m0 + r) * N + n0, bias ? bias + n0 : (const int32_t *)0, 0, r);
  VME_VTDISCARD();
}

#ifndef _OPENMP
/* Partial-M 1x2-N block: one A panel feeds two B panels/tiles.
 * Existing generated API/register plan only; tail fallback remains unchanged. */
static void gemm_1x2_k4(int32_t*c,const int8_t*a,const int8_t*b,const int32_t*bias,size_t M,size_t N,size_t K,size_t te,size_t n0){
 vme_enable();vme_vsettnt_e8w4_alt(te);vme_vsettm(M);VME_VTZERO(0);VME_VTZERO(1);
 for(size_t k0=0;k0<K;){size_t kk=K-k0<4?K-k0:4;
  vme_vsettn(M);vme_load_rows(1,a+k0*M,M,kk);
  vme_vsettn(te);const int8_t*bb=b+k0*N+n0;vme_load_rows(0,bb,N,kk);
  VME_LOAD_ROW("v24",bb+te);
  if(kk>1)VME_LOAD_ROW("v26",bb+te+N);
  if(kk>2)VME_LOAD_ROW("v28",bb+te+2*N);
  if(kk>3)VME_LOAD_ROW("v30",bb+te+3*N);
  vme_vsettk(kk);VME_MM_S_S(0,0,16);VME_MM_S_S(1,0,24);k0+=kk;
 }
 vme_vsettnt_e32w1(te);
 for(size_t r=0;r<M;r++)store_two_rows(c+r*N+n0,c+r*N+n0+te,bias?bias+n0:0,bias?bias+n0+te:0,0,1,r);
 VME_VTDISCARD();
}

#endif

#ifndef _OPENMP
/* The paired destinations v16/v20 occupy disjoint aligned groups at LMUL4.
 * Inspect the selected target's actual readout state; infer no VLEN or DLEN.
 * A different register geometry retains the original baseline path. */
static inline int reuse_readout_legal(size_t te)
{
  const size_t vl = vme_vsettnt_e32w1(te);
  uintptr_t type;
  asm volatile("csrr %0, vtype" : "=r"(type) :: "memory");
  const uintptr_t vill = (uintptr_t)1 << (8 * sizeof(uintptr_t) - 1);
  return vl == te && !(type & vill) && (type & 7) == 2 && ((type >> 3) & 7) == 2;
}
#endif

VME_KERNEL(vme_gemm_1x2_reuse)
{
  vme_enable();
  const size_t te = vme_vsettnt_e8w4_alt((size_t)-1); /* the tile edge: VLMAX in the operand configuration */

#ifndef _OPENMP
  /* Explicit opt-in kernel, single compute hart and NULL bias only. */
  if(!bias && m<=te && n>=2*te && reuse_readout_legal(te)){
   size_t n0=0;for(;n-n0>=2*te;n0+=2*te)gemm_1x2_k4(c,at,b,bias,m,n,k,te,n0);
   for(;n0<n;n0+=te)gemm_tile_bias(c,at,b,bias,m,n,k,0,n0,m,n-n0<te?n-n0:te);
   return;
  }
#endif

  /* On a multi-hart image (compiled with -fopenmp) each hart takes whole 2TE x 2TE blocks: they write
   * disjoint parts of C, and each hart has its own unit and tiles. */
#ifdef _OPENMP
#pragma omp parallel for collapse(2) schedule(static)
#endif
  for (size_t m0 = 0; m0 < m; m0 += 2 * te)
    for (size_t n0 = 0; n0 < n; n0 += 2 * te) {
      if (m - m0 >= 2 * te && n - n0 >= 2 * te) {
        gemm_2x2_bias_block(c, at, b, bias, m, n, k, te, m0, n0);
        continue;
      }
      for (size_t i = m0; i < m && i < m0 + 2 * te; i += te)
        for (size_t j = n0; j < n && j < n0 + 2 * te; j += te)
          gemm_tile_bias(c, at, b, bias, m, n, k, i, j, (m - i) < te ? (m - i) : te, (n - j) < te ? (n - j) : te);
    }
}
