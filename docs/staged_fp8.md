# Explicit typed FP8 prepared kernels

`iris.backend.staged_fp8_kernel_api()` prepares an explicitly selected,
destination-passing E4M3FN contraction for the staged runtime. The source must
already use standard `f8E4M3FN` tensor operands and FP32 accumulation. This API
does not quantize ordinary FP32 operations, select model sites, or install a
default routing policy. Generic encoded-byte storage and staged orchestration
belong to Merlin; format, instruction, register, and tile facts belong here.

The admitted scalar body is exactly two `arith.extf` operations, an unflagged
FP32 multiply, an unflagged FP32 accumulator addition, and the yielded result.
The canonical `[M,K] × [K,N]` reduction starts with a newly filled **positive**
FP32 zero. The provider checks the actual fill body, source effects, indexing,
tensor encoding, attributes, and live operations. It refuses reassociation,
contract flags, negative-zero seeds, unsupported formats, or unknown effects.

Every finite E4M3FN product is exactly representable in FP32: each operand has
at most four significand bits, the smallest nonzero product is `2^-18`, and
the largest is `448²`. Under RNE, ordered separate multiplication/addition
therefore agrees with the pinned unit's ordered FP32 FMA, including signed-zero
behavior and sticky inexact results. Preparation scans both encoded inputs and
transactionally refuses E4M3FN NaNs (`0x7f` and `0xff`) before writing work or
output. Compute refuses a non-RNE `frm` before issuing matrix arithmetic.
This is a finite-input foundation; no NaN, exception-mode, or other-format
execution equivalence is granted. E5M2 instruction spelling alone does not
establish a prepared E5M2 capability.

The caller provides `StagedRoles` with hart 2 owning initialization,
preparation, dispatch and finish; compute/profile harts are an explicit subset
of harts 0 and 1. `worker_slots` are logical invocation slots. The adapter maps
them to physical hart bits through the same checked `worker_binding` used by
the signed-I8 provider.

Preparation on hart 2 uses ordinary scalar common-ISA byte copies to produce
`At[K,M]` and `Bp[K,N]`, verifies the finite domain, and clears `Cp[M,N]` to
positive zero. Compute workers own private matrix state and disjoint TE×TE
tiles. They perform no packing, OpenMP dispatch, allocation, scale, or bias.
Finish on hart 2 copies FP32 representations through character accesses;
it does not dereference float objects through integer aliases. Any scale or
bias must remain an explicit ordinary FP32 arithmetic dispatch on a compute
worker, with the source operation order preserved.

The staged runtime's caller-owned lease must keep inputs, work descriptors,
and scratch immutable until all workers finish and the controller has finished
and observed a quiescent team. A checked finite flag does not permit modifying
the encoded operands after preparation. The caller initializes pinned worker
VME context before ARM; arbitrary thread migration or generic RVV context
support is not sufficient. There is one synchronous team and no nested dispatch.

The API has the same explicit build parameters as `staged_kernel_api()`:

```python
prepared = backend.staged_fp8_kernel_api().prepare_staged_kernel(
    closed_typed_module, fresh_build_directory,
    unit=unit, config=config, roles=roles, worker_slots=worker_slots,
    cc=compiler, controller_cflags=scalar_common_isa_flags,
    compute_cflags=source_derived_compute_flags,
    runtime_include=runtime_headers, prefix=unique_c_prefix,
)
```

The returned `PreparedStagedKernel` binds the exact source key, header,
state layout observed from the selected compiler, callbacks, objects, source
digests, and worker selection. FP8 additionally checks the pinned matrix-cell,
sequencer and conversion sources and refuses mismatched source revisions.
The board configuration must enable the source's FP8 OPU parameter; metadata
in a nearby checkout does not prove that an FPGA image matches those sources.

No timing accuracy, physical unit busy fraction, DRAM completion acknowledgement,
full-model quality, capture lineage, or automatic offload qualification follows
from this API. Architectural fences establish its ordering boundary. Numeric
execution gates, image association and performance measurements remain separate.
