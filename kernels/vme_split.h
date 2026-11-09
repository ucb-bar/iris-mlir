/* Controller-packed, compute-only signed-I8/I32 VME operator ABI.
 *
 * One descriptor is in flight at a time. Hart 2 owns prepare/finish and all
 * dispatch. Compute harts 0/1 own disjoint output blocks and their private MRF.
 * The caller publishes the immutable descriptor after prepare with release
 * ordering, acquires it on workers, and joins every selected worker BEFORE
 * finish or reusing any workspace. No function below creates a team or thread.
 */
#ifndef IRIS_VME_SPLIT_H
#define IRIS_VME_SPLIT_H
#include <stddef.h>
#include <stdint.h>

enum iris_vme_split_status {
  IRIS_VME_SPLIT_OK = 0,
  IRIS_VME_SPLIT_ROLE = 1,
  IRIS_VME_SPLIT_SHAPE = 2,
  IRIS_VME_SPLIT_CAPACITY = 3,
  IRIS_VME_SPLIT_ALIAS = 4,
  IRIS_VME_SPLIT_ALIGNMENT = 5,
  IRIS_VME_SPLIT_CONFIG = 6
};

/* Dense A[M,K], B[K,N], canonical output C[M,N]. Capacities are actual owned
 * buffer sizes in bytes. At[K,M], Bp[K,N], and Cp[M,N] are DISTINCT workspaces;
 * no workspace may overlap inputs, canonical output, or either metadata object.
 * tile_edge/alignment_bytes come from the selected derived unit configuration.
 */
typedef struct {
  const int8_t *a;
  const int8_t *b;
  int32_t *output;
  int8_t *at;
  int8_t *bp;
  int32_t *cp;
  size_t a_bytes, b_bytes, output_bytes, at_bytes, bp_bytes, cp_bytes;
  size_t m, n, k, tile_edge, alignment_bytes;
  uint64_t sequence;
  uint32_t worker_mask; /* bit 0 -> hart 0, bit 1 -> hart 1; no other bits */
} iris_vme_split_buffers;

typedef struct {
  const int8_t *at;
  const int8_t *bp;
  int32_t *cp;
  size_t m, n, k, tile_edge, alignment_bytes;
  size_t block_columns, block_count;
  uint64_t sequence;
  uint32_t worker_mask;
} iris_vme_split_work;

/* Synchronous. Validation precedes ALL writes. On success only hart 2 has
 * packed either operand; Cp is initialized, and the work descriptor is ready
 * for publication. No VME, BF16, FP8 or Zbb instruction is required by prepare.
 */
int iris_vme_split_prepare(const iris_vme_split_buffers *buffers,
                           iris_vme_split_work *work);

/* Explicit immutable-B lease. Bp MUST equal B exactly with equal capacities;
 * every other writable/metadata span remains disjoint. The caller proves B is
 * a sealed dense readonly object retained without writers through ALL worker
 * joins and finish. This is not inferred from pointer identity. Prepare packs
 * A and clears Cp, but never writes B/Bp. No Bp allocation is required. The
 * ordinary prepare/finish continue to refuse B==Bp. Setup/load/prepacking and
 * validation costs remain caller-owned, separate from repeated inference.
 */
int iris_vme_split_prepare_readonly_b(const iris_vme_split_buffers *buffers,
                                     iris_vme_split_work *work);
int iris_vme_split_finish_readonly_b(const iris_vme_split_buffers *buffers,
                                    const iris_vme_split_work *work);

/* Hart 0/1 only. No packing, dispatch, OpenMP, allocator or shared pack scratch.
 * Block ownership is cyclic among the selected physical harts in ascending
 * order. completed_blocks counts source-level completed blocks, NOT unit busy.
 * The caller must enable/own the thread's VME context before this entry.
 */
int iris_vme_split_compute(const iris_vme_split_work *work,
                           size_t *completed_blocks);

/* Hart 2 only, AFTER all selected workers have completed. Copies Cp to the
 * canonical output. This CPU/RVV copy is not a standalone DMA operation.
 */
int iris_vme_split_finish(const iris_vme_split_buffers *buffers,
                          const iris_vme_split_work *work);

/* Shared validation has no vector or matrix instructions and does not write
 * buffers. A valid descriptor alone does not prove readiness or completion;
 * those are caller-owned sequence/release/acquire obligations.
 */
int iris_vme_split_validate_work(const iris_vme_split_work *work);

static inline unsigned iris_vme_split_hart(void) {
#if defined(__riscv)
  unsigned long hart;
  __asm__ volatile("csrr %0,mhartid" : "=r"(hart));
  return (unsigned)hart;
#elif defined(IRIS_VME_SPLIT_HOST_TEST)
  extern unsigned iris_vme_split_test_hart;
  return iris_vme_split_test_hart;
#else
  return (unsigned)-1;
#endif
}
#endif
