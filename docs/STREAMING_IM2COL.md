# Opt-in INT8 streaming convolution

The Iris provider can lower a static signed `i8 × i8 → i32` window contraction
to sequential row panels and VME calls. This replaces the full im2col tensor.
The inherited RVV component builds the panels; Iris supplies the matrix-call
consumer. The ordinary route is unchanged unless the selector below is used.

Select both providers explicitly, after initializing the pinned submodules:

```sh
export MERLIN_TARGET_PATH="$PWD:$PWD/base/rvv-mlir"
export MERLIN_SUPPORT_PINS="$PWD/contracts/streaming_support_pins.json"
```

This feature pins RVV commit `250e93e726f373810776bd5113939e51c4162cad`.
The explicit development manifest satisfies Merlin's single-base check while
leaving its global support pins unchanged. It requires the exact local feature
checkout; it does not claim this unpublished branch can already be fetched.

Use the public matrix-lowering entry point with Merlin's existing preparation
and lowering APIs. `source` is an existing typed MLIR file, and `work` is a new
build directory:

```python
from pathlib import Path
from merlin.targetgen.plugins import load_declared
from merlin.runtime.backends.zephyr_model import MatrixRouting, prepare_for_lowering
from merlin.llvmlower.lower import lower_model_file
from merlin.llvmlower.codegen import compile_ll

provider = load_declared("iris", "matrix_lowering")
selection = provider.streaming_selector(
    unit="saturn_vme", config="IrisConfig", vlen_bits=256,
    workspace_bytes=64 * 1024, panel_rows=64,
)
route = MatrixRouting(
    support_target="iris", unit="saturn_vme", config="IrisConfig",
    select=selection,
)
work = Path("new-streaming-build")
features = frozenset(filter(None, [route.route_feature_name()]))
prepared, features = prepare_for_lowering(
    source, work, backend="rvv", harts=1, vlen=256, features=features,
    matrix=route, int8_compute=False,
)
lowered = lower_model_file(
    prepared, work / "lower", targets=(), textual=True, vectorize=True,
    codegen_target="iris", features=features, ir_audit=True,
)
compile_ll(lowered.ll_path, work / "model.o", target="riscv",
           extra_flags=("-march=rv64gcv_zvl256b",))
```

The normal matrix-object build must consume `load_matrix_signatures(work,
route)` and this same route, as for other Iris builds. The sidecars
`vme_signatures.json` and `streaming_im2col.json` describe the exact static call
signatures, source window, panel widths, logical invocation count and workspace
bound. Logical routed counts include repeated loop invocations; static call
sites are reported separately. Every original output row is covered once.

The supported form has one left window with explicit NCHW or NHWC input,
unshared weights, static positive stride/dilation, and a zero-initialized signed
integer MAC. Batch, row tails and explicit upstream padding are retained. The
reduction-dimension order and modular `i32` result are preserved. FP8 conversion,
floating-point reassociation and quantization are outside this transformation;
existing quantization and scale computation remain outside its scope. Unsigned
window arithmetic and priced swapped-orientation policies are refused.

`workspace_bytes` is an explicit software budget for the full gather panel,
separate row-tail gather panel, and the descriptor shim's reusable packed A.
It is not a cache-size claim. Output storage, B layout normalization, runtime
metadata, allocator alignment and compiler-generated output-tile temporaries are additional and
must be included in an actual image's memory audit. B normalization stays
outside the loops. The existing descriptor shim still copies each A panel to
its K-major buffer and preserves its fences; this first compiler schedule does
not eliminate that extra copy. Parallel panel execution is unsupported.
Object construction refuses `parallel_tiles=True` for this schedule and binds
the schedule SHA256, exact signatures and independently rechecked unit facts
into its provenance.

VLEN is checked against the selected unit. DLEN is read independently from its
RTL-derived parameters; it is never inferred from VLEN. The tile edge is taken
from the existing unit contract. The panel width is bounded by the workspace,
the requested upper bound and the spatial output-row width, and rounded to a
tile multiple where possible. At Iris's current edge 32, width 64 can reach the
existing 2×2 M fast path; width 32 cannot. Width 128 does not span two width-64
spatial rows in this implementation. No measured flat-M T128 result is
transferred to it, and no other-VLEN speed or physical utilization is claimed.

Check the actual lowered LLVM and final image for allocations/copies and run a
complete numerical gate before evaluating performance. Compare against the
best legal RVV panel configuration on the same inputs, not an untuned full
im2col control. Functional Spike instruction counts are not hardware cycles.
