# Validation of this integration

## Pinned personal fork

The native model is now pinned from
`copparihollmann/riscv-isa-sim-gemmini`, branch `iris-model`, commit
`4f69efc8ff9ae3282c84b696c781b0e4faac909b`. Its README starts with the
capability and single-hart/multicore timing tables. Seven focused admission tests
cover both the archived frozen route and rejection of a development provider
that differs from the pinned fork.

The latest native build is
`12e51b98964125d6b18e7172efc92f7bdc0ad95ee4259f33aed98856a5764cad`.
It fixes a speculative lookahead into unreachable bytes after a compressed
return, while actual execution of undefined instruction bytes still refuses.
It passed all 45 Merlin quick VME kernel shapes, the original 32 functional
guests, the normal three-hart Zephyr smoke with nine checked region checksums,
and eight directed regression outcomes. Exact receipts are bound in
[`speculative-fetch-validation.json`](spike-model/iris/provenance/speculative-fetch-validation.json).
These functional tests do not qualify new timing accuracy.

The initial clean fork build produced native SHA256
`cc9767615437f72ce7200d75df2aea39d6769a77b9a953c9c5c9bfc0d097c9ea`.
It passed all 32 directed functional cases and the baremetal, Zephyr and
profiling smokes with byte-identical frozen-reference stdout. Profiling admitted
three regions and emitted JSON/Perfetto outputs. The exact source and receipt
digests, including preserved earlier harness/packaging failures, are in
[`fork-rebuild-validation.json`](spike-model/iris/provenance/fork-rebuild-validation.json).
This migration does not establish new held-out timing accuracy.

## Original frozen package integration

Performed on amd6 on 2026-11-07, without FPGA access or changes to the frozen model:

- Six hardware-free release admission tests pass. They check explicit opt-ins,
  configuration refusal, refusal before importing an unknown provider, no
  fallback to legacy Spike after an explicit failure, snapshot/fixture identity,
  and the requirement for an explicit external artifact location.
- Source preparation checks out upstream Spike
  `72958551566a30f19444bd7e354552824e4d0a98`, applies the archived patch and
  matches all 2,142 recorded native source hashes.
- `simulator/iris-run check` admits both complete frozen profile closures.
- The matrix shim admits both real profile descriptors, returns all three
  heterogeneous hart ISAs and limits matrix workers to harts 0/1. Its returned
  execution closure also binds this adapter and the release index.
- Baremetal smoke: normal exit 0; stdout is byte-identical to the previously
  independently validated 12-region, three-hart smoke.
- Zephyr example: normal exit 0; stdout is byte-identical to the previously
  independently validated nine-checksum example with compute harts 0/1 and
  scalar control hart 2.
- Source profiling: normal exit 0, complete admitted journal, three regions,
  JSON accounting and Perfetto trace. Stdout is byte-identical to the previously
  validated 136-word example.

The external rerun receipts and raw outputs are retained under
`iris-model/build/iris-mlir-branch-validation-v1/`; source preparation receipts
are under `iris-model/build/iris-mlir-source-import-test-v*/`. Snapshot identity
is recorded by each preparation receipt. The integration records its adapter,
launcher and release SHA256 values in the unchanged runner's `IRIS_*` environment
receipt. These finite reruns establish this wrapper's execution behavior; they
do not improve timing accuracy or broaden the frozen model's qualification.

Run the admission tests with standard Python, without Merlin:

```sh
python3 -m unittest discover -s tests/conformance -p test_simulator_release.py -v
```

The full preexisting compiler suite was also attempted with companion Merlin
baseline `d56133b168bbee3d73cb8b4304d3622d4191fc6a` and the published
`chip-packages/handwritten-implementation` checkout
`582b90ff63bc17291733d0b66b518129214e4a8d`. Collection cannot proceed because the
branch's existing `tests/conformance/test_backend_composition.py` imports
`merlin.targetgen.component_composition`, which those checkouts do not provide.
This dependency already exists in the unchanged compiler baseline
`46524a94372ac4d63bb78f3586170b0901db4f8e`; no passing full-suite claim is made.
The focused admission tests and actual simulator runs above do pass. The
historically tested heterogeneous Merlin runner additionally requires the
recorded local patch and file identities in `release.json`.
