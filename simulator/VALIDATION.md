# Validation of this integration

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
