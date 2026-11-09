# Controller-packed VME operator ABI

This opt-in operator interface separates the Iris control and compute roles.
It does not change the default matrix provider or integrate a whole model's
graph dispatch. The initial numerical contract is dense signed I8 × signed I8
with wrapping I32 accumulation; quantization, scaling, bias, FP8, and arbitrary
strided input views are outside this primitive.

`iris.lower.split_vme.from_config` reads tile geometry and alignment from the
existing selected unit contract. Its workspace plan requires a nonempty subset
of compute harts 0 and 1. Hart 2 owns model initialization, operand preparation,
dispatch, completion joins, and output finalization. There are no hart-2 unit or
core profiling tracks.

The portable declarations are in `kernels/vme_split.h`:

1. On hart 2, call `iris_vme_split_prepare(buffers, work)`. It validates the
   complete buffer plan before writing, transposes dense A[M,K] to At[K,M],
   copies B[K,N] into separate Bp[K,N], and initializes packed Cp[M,N]. The
   controller implementation uses ordinary RVV with the actual returned VL;
   compile it for the common RV64GCV ISA. It requires no matrix, FP8, BF16, or
   Zbb instructions. Buffer sizes are bytes, and the supplied tile edge and
   alignment must come from the selected configuration.
2. Publish the immutable work descriptor and prepared data with release
   ordering after preparation. Selected workers acquire that generation.
   Each pinned compute worker calls `iris_vme_split_compute(work, blocks)`.
   The compute TU must be compiled without OpenMP. It reuses the registered
   `vme_gemm_2x2_bias.c` block and exact-tail bodies and the existing
   RTL-derived `saturn_vme.h`; it adds no instruction encodings. It performs
   no packing, allocation, thread creation, or nested dispatch. Whole 2TE×2TE
   blocks are assigned cyclically among the selected physical harts, with
   disjoint packed output ownership. The tile edge is checked on the worker.
3. Join all selected workers before calling `iris_vme_split_finish` on hart 2.
   It copies packed Cp into the separate canonical output. An architectural
   memory fence orders vector output writes before worker completion and
   controller final acknowledgement. This does not assert a physical DRAM
   acknowledgement count. Ordinary CPU/RVV copies are not standalone DMA.
4. Reuse any workspace or descriptor only after the final acknowledgement.

The ABI admits one synchronous descriptor at a time. The dispatcher owns
sequence publication, release/acquire ordering, worker liveness, completion
joins, and descriptor lifetime. Descriptor validity alone proves neither
readiness nor completion. A nonempty selected worker can receive no block for
a very small shape; record this explicitly rather than inventing useful work.
The completion-count location must be worker-private and must not overlap the
descriptor or operands. There is no shared pack scratch in the compute path.

The surrounding runtime must enable the pinned worker thread's Iris VME
context using the Iris-aware Zephyr interface. This ABI does not establish
arbitrary thread migration or generic vector-only context safety for matrix
state. Role/configuration/capacity/alias failures return an explicit status;
there is no implicit scalar fallback.

Arm workers before controller preparation. A worker-only global timer interval
from ARM to final controller acknowledgement can include preparation,
dispatch, waiting, compute, and output finalization without reading hart-2
core counters. Keep the worker's compute-only `mcycle`/`minstret` window
separate. An inclusive `mcycle` interval is not elapsed wall time if WFI can
gate that clock; retain raw global timer ticks and qualify their frequency.
Instruction-count-only simulator counters remain instruction counts. Neither
these software intervals, static block counts, nor lane geometry measure
physical vector, matrix, or DMA busy time.
