# Opt-in direct K-major VME preparation

The Iris provider can produce a signed INT8 activation panel directly in the
K-major layout the selected VME primitive consumes. This removes the additional
A→At copy and its pack scratch. It uses the inherited RVV panel builder and the
existing VME primitive ABI. It does not change quantization or reduction order.

Use the existing `MatrixRouting.select` seam with the public Iris provider:

```python
from merlin.targetgen.plugins import load_declared
from merlin.runtime.backends.zephyr_model import MatrixRouting

provider = load_declared("iris", "matrix_lowering")
selection = provider.streaming_selector(
    unit="saturn_vme", config="IrisConfig", vlen_bits=256,
    workspace_bytes=64 * 1024,
    prepacked_a=True,
    admission="workspace_pressure",
)
route = MatrixRouting(
    support_target="iris", unit="saturn_vme", config="IrisConfig",
    select=selection,
)
```

Pass this route to the ordinary preparation, lowering and matrix-object build
APIs. Both new options remain off unless selected; prior emission is preserved.
The explicit VLEN must match the selected unit. DLEN and the tile edge are read
independently from its existing RTL-derived contract. The producer preserves
static batch, layout, explicit padding, stride, dilation and row tails.

The direct descriptor entry verifies exact positive-integer shape witnesses.
It retains the original fence and modular I32 arithmetic, passes NULL bias,
and has a correct strided scalar fallback. It currently requires one compute
hart; `parallel_tiles=True` is refused. FP8 is outside this transformation.

`workspace_pressure` is a static cost proxy, not an empirical timing estimate.
It retains the full route when full gather-plus-pack fits the preferred budget,
when row panels add abstract tile work, or when a panel is smaller than one
RTL-derived tile. Otherwise the largest legal row-bounded panel is selected
from the explicit payload budget. There is no universal measured tile size.

The workspace is a **preference**, not a hard RAM ceiling: retaining the full
route may exceed that preferred budget. The actual image memory planner must
still admit all allocations, alignment, metadata and output storage. This proxy
does not model cache misses, per-call setup, partial-super-tile behavior, or an
exact kernel's measured crossover. Per-build sidecars record every decision.

Bounded C-tile copies and output restoration remain. This option does not claim
to remove those copies, predict hardware cycles, or measure physical unit busy
time. Functional checks cover complete outputs and guards, descriptor strides,
padding, tails, malformed metadata and unchanged default emission. Keep run
results and profiling artifacts in the owner's private evidence store.
