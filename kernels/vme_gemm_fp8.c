/* Native packed FP8 GEMM: K-major byte operands, row-major FP32 result.
 * Each cell performs sequential FP32 FMA in increasing K, as OuterProductCell
 * and OuterProductSequencer at Saturn 536774bd4c5e3bcd7813a4a0dc72fc87e5818532.
 * Scale and bias are deliberately outside this primitive. No FP32 fake quant.
 * Encodings/configuration words are generated from the pinned RTL and checked
 * against benchmarks/common/vme.h. A format is rd[0], B is vtype.altfmt.
 */
#include "vme_api.h"

#if VME_KSTEP != 4 || VME_ROW_STRIDE != 2
#error "FP8 row register plan requires the derived kmax=4, row_stride=2"
#endif

static inline void fp8_load_a(const uint8_t *p, size_t ld, size_t kk)
{
  VME_LOAD_ROW("v0", p);
  if (kk > 1) VME_LOAD_ROW("v2", p + ld);
  if (kk > 2) VME_LOAD_ROW("v4", p + 2 * ld);
  if (kk > 3) VME_LOAD_ROW("v6", p + 3 * ld);
}
static inline void fp8_load_b(const uint8_t *p, size_t ld, size_t kk)
{
  VME_LOAD_ROW("v16", p);
  if (kk > 1) VME_LOAD_ROW("v18", p + ld);
  if (kk > 2) VME_LOAD_ROW("v20", p + 2 * ld);
  if (kk > 3) VME_LOAD_ROW("v22", p + 3 * ld);
}

/* Same proven per-tile instruction order, with global strides/explicit owned
 * coordinates so an external controller can assign disjoint tiles. */
static void fp8_gemm_tile(float *c, const uint8_t *at, const uint8_t *b,
                          size_t m, size_t n, size_t k,
                          size_t i, size_t j, size_t tm, size_t tn,
                          int a_e5m2, int b_e5m2)
{
  vme_enable();
  if (b_e5m2) vme_vsettnt_e8w4_alt(tn); else vme_vsettnt_e8w4(tn);
  vme_vsettm(tm);
  VME_VTZERO(0);
  for (size_t p = 0; p < k; p += VME_KSTEP) {
    const size_t kk = k - p < VME_KSTEP ? k - p : VME_KSTEP;
    vme_vsettn(tm);
    fp8_load_a(at + p * m + i, m, kk);
    vme_vsettn(tn);
    fp8_load_b(b + p * n + j, n, kk);
    vme_vsettk(kk);
    if (a_e5m2) { VME_MM_E5_E4(0, 0, 16); }
    else { VME_MM_E4_E4(0, 0, 16); }
  }
  vme_vsettnt_e32w1(tn);
  for (size_t row = 0; row < tm; ++row) {
    VME_VTMV_V_T(16, vme_tss(0, VME_TSS_ROW, row));
    asm volatile("vse32.v v16, (%0)" :: "r"(c + (i + row) * n + j) : "memory");
  }
  VME_VTDISCARD();
}

static void fp8_gemm(float *c, const uint8_t *at, const uint8_t *b,
                     size_t m, size_t n, size_t k, int a_e5m2, int b_e5m2)
{
  if (!m || !n) return;
  vme_enable();
  const size_t te = b_e5m2 ? vme_vsettnt_e8w4_alt((size_t)-1) : vme_vsettnt_e8w4((size_t)-1);
#ifdef _OPENMP
#pragma omp parallel for collapse(2) schedule(static)
#endif
  for (size_t i = 0; i < m; i += te)
    for (size_t j = 0; j < n; j += te) {
      const size_t tm = m - i < te ? m - i : te;
      const size_t tn = n - j < te ? n - j : te;
      fp8_gemm_tile(c, at, b, m, n, k, i, j, tm, tn, a_e5m2, b_e5m2);
    }
}

VME_FP8_KERNEL(merlin_vme_gemm_e4m3_e4m3) { fp8_gemm(c, at, b, m, n, k, 0, 0); }
VME_FP8_KERNEL(merlin_vme_gemm_e4m3_e5m2) { fp8_gemm(c, at, b, m, n, k, 0, 1); }
VME_FP8_KERNEL(merlin_vme_gemm_e5m2_e4m3) { fp8_gemm(c, at, b, m, n, k, 1, 0); }
VME_FP8_KERNEL(merlin_vme_gemm_e5m2_e5m2) { fp8_gemm(c, at, b, m, n, k, 1, 1); }
