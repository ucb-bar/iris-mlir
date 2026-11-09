# Build an explicit outlined INT8 chain

This opt-in edge builds linkable objects for an authored, closed typed graph. It
uses Merlin's existing outline planner, checked kernel serialization, prepared
kernel contract and chain builder. Iris owns the selected split-provider ABI;
the inherited RVV provider owns RVV arithmetic lowering. It does not launch an
ELF, select an offload policy, quantize a model or admit a full model.

Use a recursive Iris checkout and a Merlin installation containing
`merlin.compile.outlined_program` and `PreparedStagedKernel`. Configure its
ordinary MLIR compiler and pinned hardware-source tools as described in
`GETTING_STARTED.md`. Keep the nested base at the gitlink revision; do not check
out a different RVV branch inside it.

From this repository root, the public example has a signed-I8 GEMM followed by
wrapping-I32 bias addition. Build both routes from the exact same source:

```sh
python tools/campaigns/outlined_i8_program.py \
  --source examples/outlined_i8_chain.mlir --output out/outlined-vme \
  --route vme --dispatch-index 0 --worker-slots 0 1 --unit saturn_vme --config IrisConfig \
  --workspace-budget-bytes 65536 --descriptor-job-budget 64 \
  --prefix example_vme

python tools/campaigns/outlined_i8_program.py \
  --source examples/outlined_i8_chain.mlir --output out/outlined-rvv \
  --route rvv --dispatch-index 0 --worker-slots 0 1 --unit saturn_vme --config IrisConfig \
  --workspace-budget-bytes 65536 --descriptor-job-budget 64 \
  --prefix example_rvv
```

`--worker-slots` requires exactly one slot for every arithmetic dispatch in the
closed graph. Slots are indices 0 and 1 into the pinned compute workers. These
assign ordinary callbacks; the selected split GEMM still runs in parallel on
both prepared worker slots. A one-operation graph needs one entry, and a
three-operation graph needs three. Wrong counts refuse before creating output.
Cached Iris or RVV modules from another checkout also refuse; the command does
not remove or reload caller modules.

Outputs must be fresh directories. The workspace and descriptor budgets are
explicit example limits, not target cache-capacity facts or universal tuning
choices. Source proof derives the selected operation's dimensions and requires
zero-seeded signed-I8 × signed-I8, unflagged wrapping-I32 accumulation and the
supported layouts. Unsupported source, ABI, compiler storage effects, stale
composition or exceeded budgets refuse the build. There is no integrated-packing
fallback.

The command selects `contracts/streaming_support_pins.json` and the nested RVV
base for its own process through the normal `MERLIN_SUPPORT_PINS` and
`MERLIN_TARGET_PATH` interfaces. The existing composition verifier checks the
paired revision and generated contracts. This explicit selection leaves
Merlin's legacy global registry unchanged; it does not establish compatibility
for another provider in that registry.

`build.json` records source identity, composition, actual flags, provider input
hashes, opaque state and workspace cost. `chain/build.json` contains the generated
chain header/driver and its link objects. The RVV route uses the same
controller-packed input layout and output-block ownership as VME; it is a
controlled comparator, not a claim of the best RVV layout or implementation.
Its compiler postcondition rejects allocation, copy, free and new-team calls.
Scalar and vector-storage-only tail limitations remain in each leaf's record.

The runtime role contract requires H2 model initialization, packing, dispatch
and finish, with every arithmetic callback on pinned h0/h1. Controller objects
use the qualified scalar common ISA by default. VLEN is taken from the selected
unit configuration independently of DLEN. No H2 performance counters or tracks
are part of this protocol.

Linking and running still require a target application adapter: persistent pinned
workers, ordinary vector setup followed by Iris-aware context registration before
ARM, launch/join hooks, shared-clock hooks, buffer ownership and a complete
numerical gate. Workers must return and be joined before team/workspace reuse.
The required runtime header and generated chain API document this boundary.
Merely producing these objects is not execution admission or a hardware result.
Worker counters measure synchronous callback intervals; shared-clock ARM through
final ACK can cover controller preparation and finish without reading H2 counters.
Neither interval is physical-unit busy time.

The public source and refusal tests can be run with:

```sh
PYTHONPATH=src:base/rvv-mlir/src python -m pytest \
  tests/lowering/test_outlined_program_cli.py \
  tests/lowering/test_outlined_vme.py tests/lowering/test_outlined_rvv.py
```

The two compiler-backed CLI tests require the explicit compiler and pinned RTL
source environment. Missing prerequisites are reported as skips, not passes.
The script's selected dispatch is explicit and structural: model names, golden
values and benchmark identities do not choose its behavior.
