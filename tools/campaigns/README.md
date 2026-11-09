# Common-source operator experiments

`operator_suite.py` emits one signed INT8-to-INT32 MLIR graph per declared
geometry. Every selected backend refers to those same authored bytes. Public
input and output layouts are explicit, including NCHW/NHWC convolutions and
transposed-RHS matrix stages. Backend normalization and generated objects must
be retained as separate artifacts by the compiler workflow.

Keep experiment specifications, model tensors, measurements and plots in a
private artifact directory. This directory contains reusable tools only.

```sh
python tools/campaigns/operator_suite.py --spec /private/spec.json --output /private/new-sources
```

The specification schema is `iris.compiled_operator_suite.v1`, with:

- `target`: explicit `vlen_bits`, independently declared `dlen_bits` (or null),
  `compute_harts` and optional configuration name. Declarations are not target
  capability verification. Iris compute harts are 0 and 1.
- `variants`: distinct selections from `rvv`, `vme_full`, `vme_prepacked`.
- `max_logical_bytes_per_case`: a generation limit for inputs plus output. It
  does not bound compiler scratch allocations or admit a target memory layout.
- `cases`: unique safe IDs, `split` (`smoke`, `fit` or `holdout`), `kind`, geometry
  and optional source-declared `model`/`part` labels. Labels do not establish
  checkpoint provenance.

Matrix kinds `gemm`, `attention_qk` and `attention_av` take positive `m,n,k` and
optional `rhs_layout` (`k_n` or `n_k`). QK defaults to `n_k`; the others to `k_n`.
These are raw dot stages: they do not implement complete attention, scaling,
masking, softmax, quantization, bias or an output epilogue.

`conv2d` takes `batch,ci,co,ih,iw,kh,kw`; optional `sh,sw,dh,dw` default to one,
and `pt,pb,pl,pr` default to zero. `layout` is `NCHW` (default) or `NHWC`.
Padding is explicit in the graph. Stride, dilation and asymmetric padding affect
the independent reference. Work metadata separates dense MACs from nonpadding
MACs; padded zero work must not be credited as useful work.

All arithmetic sign-extends I8 operands, multiplies and accumulates modulo
32 bits. This is a synthetic integer experiment contract, separate from
checkpoint-backed TorchAO capture and its quantization/scaling semantics.

The generated `sources.json` preserves original recipes, normalized geometry,
logical shapes/work, source hashes and variant references. It declares that
execution and memory admission are still required. Freeze fitting/holdout
rosters before measurements, and keep held-out cases out of policy selection.

## Independent full-output checks

`operator_reference.py` provides deterministic full-range I8 inputs and a
separate NumPy reference. `operator_harness.generate(row, target, fresh_dir)`
creates one hart-0 bare-metal driver, a binary golden and a strict postgate. It
checks every output word, unchanged inputs, buffer guards and actual `vlenb`.
The driver parks the other harts; it is not a multicore scheduler.

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, "tools/campaigns")
from operator_harness import generate

sources = json.loads(Path("/private/new-sources/sources.json").read_text())
receipt = generate(sources["cases"][0], sources["target"], "/private/new-driver")
```

The generated `main.c` expects `htif.h` and the Merlin console/exit ABI. Startup,
console, allocator, MLIR runtime and linker sources must be supplied explicitly
and digest-bound in the execution plan. Verify that the actual emitted LLVM
function is a void-returning `forward` with flattened input memrefs followed by
the preallocated output memref before using the row-major wrapper. Never infer
an ABI solely from a tensor shape.

The generated `check.py` accepts `--console`, `--output` and
`--counter-semantics`, and creates a fresh `report.json`. Bind its transitive
Python sources, manifest, golden and every runtime header as execution inputs.
The checker independently compares the complete raw output, verifies CSR
endpoints, checksums, guards, inputs, completion and successful guest exit.

Initialization and checking/UART output are outside the operator bracket; a
completion fence is inside. This fence does not establish an independently
qualified device-specific drain. A functional simulator's CSR values remain
functional counts. Modeled or FPGA cycles require their actual declared counter
domain. Physical unit busy time and memory traffic are unavailable unless
separately measured; logical work and payload bytes cannot substitute for them.

These tools never launch hardware. Hardware execution belongs to the managed
campaign runner and its ownership, cleanup and evidence requirements.
