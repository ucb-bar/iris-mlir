/* Finite E4M3FN byte operands, ordered FP32 accumulation, explicit split roles.
 * H2 prepares immutable At/Bp and scatters raw FP32 output bits after all selected
 * workers join. H0/H1 own disjoint output tiles and their private matrix state.
 * No callback creates a team. Caller owns release/acquire, sequence, quiescence
 * and pinned Iris-aware VME thread context. RNE is checked on compute harts.
 */
#ifndef IRIS_VME_FP8_SPLIT_H
#define IRIS_VME_FP8_SPLIT_H
#include <stddef.h>
#include <stdint.h>

enum iris_vme_fp8_split_status {
  IRIS_VME_FP8_SPLIT_OK = 0,
  IRIS_VME_FP8_SPLIT_ROLE = 1,
  IRIS_VME_FP8_SPLIT_SHAPE = 2,
  IRIS_VME_FP8_SPLIT_CAPACITY = 3,
  IRIS_VME_FP8_SPLIT_ALIAS = 4,
  IRIS_VME_FP8_SPLIT_ALIGNMENT = 5,
  IRIS_VME_FP8_SPLIT_CONFIG = 6,
  IRIS_VME_FP8_SPLIT_NONFINITE = 7,
  IRIS_VME_FP8_SPLIT_ROUNDING = 8
};

/* A[M,K]/B[K,N] contain actual E4M3FN encodings, not signed integer values.
 * No numeric quantizer or scale/bias epilogue is hidden in this primitive.
 * Capacities describe independently owned buffers; input A/B may alias because
 * they are read-only. Metadata, canonical output and all workspaces are distinct.
 */
typedef struct {
  const uint8_t *a, *b;
  float *output;
  uint8_t *at, *bp;
  float *cp;
  size_t a_bytes, b_bytes, output_bytes, at_bytes, bp_bytes, cp_bytes;
  size_t m, n, k, tile_edge, alignment_bytes;
  uint64_t sequence;
  uint32_t worker_mask;
} iris_vme_fp8_split_buffers;

typedef struct {
  const uint8_t *at, *bp;
  float *cp;
  size_t m, n, k, tile_edge, alignment_bytes;
  size_t block_columns, block_count;
  uint64_t sequence;
  uint32_t worker_mask;
  /* Produced only after prepare scans both inputs and rejects NaN encodings.
   * This is a caller-owned immutable lease, not an authentication/readiness token.
   */
  uint32_t finite_inputs_verified;
} iris_vme_fp8_split_work;

/* Hart2 only, common scalar ISA. ALL buffer/metadata/domain checks precede
 * workspace writes. Source FP8->FP32 product is exact for every admitted value;
 * target accumulation follows increasing K under RNE. Unknown/nonfinite domain
 * is refused rather than a float/integer fallback.
 */
int iris_vme_fp8_split_prepare(const iris_vme_fp8_split_buffers *,
                              iris_vme_fp8_split_work *);
int iris_vme_fp8_split_validate_work(const iris_vme_fp8_split_work *);
int iris_vme_fp8_split_compute(const iris_vme_fp8_split_work *, size_t *completed_tiles);
/* After every selected worker completes. Byte copies only: no typed int32_t
 * dereference of float objects, no scale/bias/model arithmetic on the controller.
 */
int iris_vme_fp8_split_finish(const iris_vme_fp8_split_buffers *,
                             const iris_vme_fp8_split_work *);

static inline unsigned iris_vme_fp8_split_hart(void) {
#if defined(__riscv)
  unsigned long hart;
  __asm__ volatile("csrr %0,mhartid" : "=r"(hart));
  return (unsigned)hart;
#elif defined(IRIS_VME_FP8_SPLIT_HOST_TEST)
  extern unsigned iris_vme_fp8_split_test_hart;
  return iris_vme_fp8_split_test_hart;
#else
  return (unsigned)-1;
#endif
}
#endif
