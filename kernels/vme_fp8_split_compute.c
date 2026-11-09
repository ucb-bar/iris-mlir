/* Compute harts own the MRF and disjoint output tiles. No packing/team/heap. */
#ifdef _OPENMP
#error "controller-dispatched FP8 compute must be built without OpenMP"
#endif
#include "vme_fp8_split.h"
#include "vme_gemm_fp8.c"

int iris_vme_fp8_split_compute(const iris_vme_fp8_split_work *w,
                               size_t *completed_tiles) {
  unsigned hart = iris_vme_fp8_split_hart();
  if (hart > 1) return IRIS_VME_FP8_SPLIT_ROLE;
  if (!completed_tiles) return IRIS_VME_FP8_SPLIT_SHAPE;
  int status = iris_vme_fp8_split_validate_work(w);
  if (status) return status;
  if (!(w->worker_mask & (1U << hart))) return IRIS_VME_FP8_SPLIT_ROLE;
  if (w->tile_edge != VME_TILE_EDGE || w->alignment_bytes != VME_ALIGN_BYTES)
    return IRIS_VME_FP8_SPLIT_CONFIG;
  uintptr_t result = (uintptr_t)completed_tiles;
  if (result % _Alignof(size_t) || sizeof(*completed_tiles) > UINTPTR_MAX - result)
    return IRIS_VME_FP8_SPLIT_ALIGNMENT;
  const void *bases[] = {w->at, w->bp, w->cp, w};
  size_t bytes[] = {w->m * w->k, w->k * w->n,
                    w->m * w->n * sizeof(float), sizeof(*w)};
  for (unsigned i = 0; i < 4; ++i)
    if (result < (uintptr_t)bases[i] + bytes[i] &&
        (uintptr_t)bases[i] < result + sizeof(*completed_tiles))
      return IRIS_VME_FP8_SPLIT_ALIAS;
  unsigned long frm;
  __asm__ volatile("csrr %0,frm" : "=r"(frm));
  if (frm != 0) return IRIS_VME_FP8_SPLIT_ROUNDING;
  vme_enable();
  size_t te = vme_vsettnt_e8w4((size_t)-1);
  if (te != w->tile_edge) return IRIS_VME_FP8_SPLIT_CONFIG;
  unsigned workers = (w->worker_mask & 1U) + ((w->worker_mask >> 1) & 1U);
  unsigned rank = hart == 0 ? 0 : (w->worker_mask & 1U);
  *completed_tiles = 0;
  for (size_t tile = rank; tile < w->block_count; tile += workers) {
    size_t i = (tile / w->block_columns) * te;
    size_t j = (tile % w->block_columns) * te;
    size_t tm = w->m - i < te ? w->m - i : te;
    size_t tn = w->n - j < te ? w->n - j : te;
    fp8_gemm_tile(w->cp, w->at, w->bp, w->m, w->n, w->k,
                  i, j, tm, tn, 0, 0);
    ++*completed_tiles;
  }
#if defined(__riscv)
  /* Architectural ordering before completion publication; not a DRAM D-ack
   * claim or physical store/accelerator busy measurement. */
  __asm__ volatile("fence rw,rw" ::: "memory");
#endif
  return IRIS_VME_FP8_SPLIT_OK;
}
