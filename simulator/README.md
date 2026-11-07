# Iris Spike development snapshot

This branch connects the existing Iris compiler package to the frozen **35a**
development simulator. It also preserves the native source patch and companion
sources for import into a fork of
[`riscv-software-src/riscv-isa-sim`](https://github.com/riscv-software-src/riscv-isa-sim).
The base is `72958551566a30f19444bd7e354552824e4d0a98` plus our Iris changes.
No separate Spike fork has been published by this change.

## Run now

The tested release currently requires the external `iris-model` workspace and
its complete byte-bound artifact/evidence closure. On amd6 that workspace is
`/scratch/copparihollmann/iris-model`. Generated binaries, component SDK archives,
FPGA images and raw campaign captures are not stored in this repository.

```sh
export IRIS_MODEL_ROOT=/scratch/copparihollmann/iris-model
./simulator/iris-run check
./simulator/iris-run compute /absolute/kernel.elf --output /absolute/new-compute-run
./simulator/iris-run zephyr /absolute/zephyr.elf --output /absolute/new-zephyr-run
./simulator/iris-run profile /absolute/kernel.elf --output /absolute/new-profile-run
```

`--model-root /path/to/iris-model` can precede the command instead of setting the
environment variable. Outputs must be fresh directories; keep ELF symbols.
`--timeout` defaults to 600 seconds and `--seed` to zero. Profiling additionally
accepts `--region-map` and `--region-format` (`none`, `iris-json`, `merlin-kernel-v1`).
Program `mcycle`, `minstret` and `mtime` use the selected model's clock behavior.
Fatal-handler detection, bounded execution and owned guest cleanup are delegated
to the unchanged tested release.

The adapter checks the exact descriptor and provider identities before importing
the provider, which verifies its full input closure. Changed or missing inputs
refuse execution. Existing descriptors contain absolute paths and pin the host
launcher/interpreter; copying this Git repository alone does not install or
relocate that release. A portable clean rebuild/repackaging remains future work.
No successful local check should be interpreted as that missing portability gate.

Useful existing examples on amd6:

```sh
./simulator/iris-run compute "$IRIS_MODEL_ROOT/benchmarks/build/smoke.elf" --output /tmp/iris-compute-new
./simulator/iris-run zephyr "$IRIS_MODEL_ROOT/build/zephyr-mixed9-firstuse-normal-v2/zephyr/zephyr.elf" --output /tmp/iris-zephyr-new
./simulator/iris-run profile "$IRIS_MODEL_ROOT/build/source-profile-smoke-guest-v1/guest.elf" --output /tmp/iris-profile-new
```

## Three heterogeneous harts

| Hart | Core | VLEN | DLEN | Role and instructions |
| --- | --- | ---: | ---: | --- |
| 0, 1 | Shuttle + Saturn | 256 | 128 | Compute; RVV integer and IEEE FP16/32/64, configured FP8/BF16 subset, VME INT8 and FP8 matrix |
| 2 | Rocket + Saturn | 256 | 256 | Control and ordinary RVV integer/IEEE FP; no VME or MX FP8/BF16 arithmetic |

All three physical harts remain present. Matrix worker IDs are `(0,)` or `(0,1)`.
Hart 2 rejects matrix instructions. Ordinary vector INT8 on hart 2 is supported;
absence of its matrix unit does not remove vector integer arithmetic.

`compute` selects **source_compute**, with finite source VADD/FADD/OPU actors on
harts 0/1 and inherited effective costs elsewhere. Unsupported source contexts
refuse, including active source-vector IRQ contexts. `zephyr` selects the separate
**source_os** profile: new source VADD/OPU/specialized FENCE actors are disabled;
source FADD/retry/wake and effective timing remain. Compute timing qualification
does not transfer to OS execution. Detailed hart-2 timing is not certified.

The memory map, per-hart ISAs and exact profile identities are returned by the
verified provider. It includes DRAM at `0x80000000` (4 GiB), two 128-KiB TCMs,
128-KiB system scratchpad at `0x580000000`, and modeled shared-resource effects.
Memory latency/overlap accuracy remains limited; see the validation below.

## Merlin hook

The matrix plugin's `device_simulator(unit=..., config=...)` dispatches to this
release when `MERLIN_IRIS_MODEL_MANIFEST` is set. The root `simulator.py` also
exports the hook for a direct provider loader. A refused explicit selection
never falls back to generic Spike. With no descriptor selected, the preexisting
functional Zvt route remains available under its original configuration.

```sh
export MERLIN_TARGET_PATH=/path/to/iris-mlir
export MERLIN_IRIS_TIMING=empirical
export MERLIN_IRIS_ALLOW_EXPERIMENTAL_TIMING=1
export MERLIN_IRIS_MODEL_MANIFEST="$IRIS_MODEL_ROOT/build/C12-source-VADD35a-optional-package-v1/source_compute/manifest.json"
# Select source_os/manifest.json instead for the tested OS profile.
```

The hook returns `available`, `binary`, `binary_sha256`, `isa_extensions`,
`per_hart_isa`, `matrix_hart_ids`, launch arguments, memory regions and full
execution-input provenance. The caller must honor the per-hart overrides, all
three physical harts, launch arguments and fatal-aware launcher. The original
generic Merlin backend does not necessarily implement that heterogeneous ABI;
the tested companion integration is pinned in `release.json`. Selecting this
hook does not by itself qualify an arbitrary newer Merlin runner.
`merlin/heterogeneous-runner.patch` preserves the historically tested generic
runner delta and its exact runtime file identities are in `release.json`.
This integration does not apply that patch to any live Merlin checkout.

## Observed validation and remaining limits

`release.json` records exact source/artifact pins and `validation.json` records
the evidence identities. These measurements describe the frozen release:

| Reference | Regions | Median absolute total error | P90 absolute | Within 2% |
| --- | ---: | ---: | ---: | ---: |
| Isolated compute, harts 0 then 1 | 1,320 | 0.155% | 6.522% | 1,110/1,320 |
| Three-hart Zephyr batch, all regions | 648 | 4.706% | 27.751% | 159/648 |
| Reserved Zephyr validation subset | 192 | 24.615% | 28.947% | 32/192 |

The isolated arithmetic/matrix subset is stronger: FP add 432/432, integer add
573/576, E4M3×E4M3 matrix 48/48 and INT8 matrix 47/48 within 2%. Loads, stores,
row movement and memory/compute overlap have much larger errors. **The overall
±2% target is unmet.** Held-out predictions froze before label release; opened
data cannot be reused as a fresh blind test after adaptive model changes.

The broad FPGA run independently passed 648 regions and 181,440 active values
plus guards: RVV SEW8/16/32, LMUL1/2/4/8, matrix shapes8/16/32, INT8, all four
E4M3/E5M2 operand-format pairs, one/two compute workers and three trials. Hart 2
was the scalar coordinator. The nine-checksum INT8 Zephyr example above also
completed normally on FPGA and the OS profile. These gates do not establish
arbitrary vector-owner migration, special-value FP8 behavior, whole-ISA coverage,
whole-language-model correctness or loaded-image/source equivalence. The broad
model comparison used a controlled postmeasurement stop, not normal end-to-end
completion or a simulated-speed qualification. Hart-2 cycle accuracy was not scored.

Compute and Zephyr executions retain raw stdout/stderr, native summary, counter
prefixes and `run.json`. `profile` additionally emits admitted source accounting
JSON and a Perfetto/Chrome timeline, with region/function attribution. Source
pipe-valid occupancy, controller busy and command age are distinct measures.
Physical FPGA utilization, useful hardware HPM/TracerV counters, physical memory
traffic and hardware rooflines are unavailable. See
[`SOURCE_EVENT_PROFILING_V3.md`](SOURCE_EVENT_PROFILING_V3.md).

## Source handoff and build inputs

`spike/source-overlay.patch` is the exact frozen native diff. The matching
`native-inventory.json` binds 2,142 source files. `model-source/` preserves the
80 external compiled source/header inputs, runtime/provider/profiler dependency
sources and the historical native build recipe. `calibration/` holds the two
unchanged fixtures. `snapshot.json` inventories these archived bytes. Original
build/capsule receipts are historical evidence, with their original absolute
paths and limitations preserved.

```sh
python3 simulator/prepare-source.py --output /absolute/new-source-workspace
# With a local upstream clone instead of downloading:
python3 simulator/prepare-source.py --upstream /path/to/riscv-isa-sim --output /absolute/another-source-workspace
```

Preparation checks all archived inputs, checks out the exact upstream base,
applies the patch and verifies every recorded native source file. It lays out
the companion sources at the relative locations the native source expects.
It does **not** compile a standalone simulator. The source model also links
generated component SDK archives; their identities and original commands are
recorded in `spike/original-build.json`. Those external SDK archives and their
complete generation pipeline must be packaged before offering a portable clean
build. A new binary digest also requires fresh functional and timing checks;
it cannot inherit the frozen binary's qualification merely from a source pin.

The source snapshots retain upstream license notices; copies of their licenses
are in `LICENSES/`. The root MIT license does not replace upstream licenses.
Never commit bitstreams, private access material or large raw campaign archives.
No command here accesses the FPGA. Any future hardware campaign must use the
operator's occupancy/authorization/lock checks and the full managed
kill → infrasetup → runworkload → owned kill lifecycle.

The [integration validation record](VALIDATION.md) describes the checks performed
for this branch and the separate preexisting compiler test dependency limitation.
