> Historical documentation snapshot from the external `iris-model` workspace.
> Its relative artifact/source links resolve in that original workspace.
> For this branch's commands and limitations, use [README.md](README.md).

# Compute-hart source model 35a: run and measured scope

Detailed timing work targets RVV/VME on compute harts 0 and 1. Hart 2 remains
functional for boot, program control, ordinary RVV, synchronization, timers/IPIs
and Zephyr. Its shared-memory traffic still affects the compute harts; detailed
hart-2 cycle accuracy is deferred. All three harts remain instantiated.

The optional frozen native is
`35a804964d7882f36a8892d7672dcfbbc7becc89c87e2d830c149820c1291388`,
with UART `1723a1679f429b57d6a1aacf4a5f33e1f12c5cb35ccb8c4730975302ed60c6c0`.
[Reconstructible source capsule](../build/C12-VADD-EG-frozen-35a80496-v2/manifest.json)
binds 2,142 native sources and 80 external compiled inputs. This epoch adds a
one-stage integer VADD source actor to the existing four-stage FADD actor and
source OPU control. Original TRAIN-only numerical coefficients remain unchanged.
These new actors are enabled by explicit profile, with no default selection change.

## Run a program

```sh
python3 spike/run_source_VADD35a_v1.py \
  --package build/C12-source-VADD35a-optional-package-v1 \
  --profile source_compute --elf /absolute/unstripped-program.elf \
  --output build/my-new-compute-run --timeout 600
```

`source_compute` enables finite source VADD/FADD/OPU timing on harts 0/1.
Integer VADD supports known full-VL, unmasked VV operations at SEW 8/16/32/64,
LMUL 1/2/4/8, with dirty VS and defined operands. FADD supports finite RNE
H/S/D operations in its qualified full-VL, unmasked, already-dirty FS context
without newly raised flags. Active source-vector IRQ contexts and unsupported
geometry refuse explicitly. Source COM+1 is an unqualified boundary; cross-unit
and memory consumers may drain conservatively. Arbitrary RVV, memory overlap
and interruptible source-actor execution are not certified.

Use `--profile source_os` for the separately tested Zephyr/control profile. It
uses the same native, disables source VADD/OPU/specialized OPU FENCE and retains
source FADD/retry/wake behavior. Its five normal-exit OS groups cover scalar,
mixed, RVV and VME-aware context images. They do not certify active per-EG
VADD/FADD preemption or transfer compute timing accuracy to Zephyr.

Both profiles passed their own three-hart normal smoke, modeled-time sensitivity,
seven fatal/launcher gates, digest/profile admission and actual Merlin API route.
The native passed 32 functional guests, directed source/refusal controls and the
full ASM numerical corpus. Eight preserved shipped/twin GEMM/conv2d images at
one/two workers passed 32,768 INT32 and 32,768 W8A8 goldens. Those ELFs execute
272 source OPU commands and zero finite VADD commands; VADD numerical coverage
comes from the separate directed and ASM tests.
[Delivery](../build/C12-source-VADD35a-optional-delivery-v2/report.json),
[root verification](../build/C12-source-VADD35a-delivery-root-verification-v1/report.json).

## Latest reserved FPGA comparison

The new ELF SHA256 is
`e4a4f194d78e5a02191dc99d07a03d47e9e69bf1b263d0b420870954b6c0fa07`.
It retains all 1,320 regions, three repetitions, 184,128 independent output-word
checks and 3,960 actual CSR/ELF joins. Harts 0 and 1 run individually with the
other harts parked. Data shapes include LMUL 1/2/4/8 and memory payloads
16/32/64/128/256 bytes. New repetition counts 96/384 and row repetitions
3/12/48/192 distinguish this reference from earlier runs; repetition counts are
not data sizes. Extra functional witnesses prevent modular integer wrap from
making omitted arithmetic appear correct.

Predictions froze at 12:12:13 UTC on 2026-11-07, before the protected timing
labels were released. No refit or model selection used these labels. All total,
issue and drain retired-instruction counts match. These results are now opened
and cannot be reused as a fresh blind test for adaptive future changes.
Signed error is 100 × (model − FPGA) / FPGA; no overhead/outlier subtraction.
P90 uses nearest-rank ceil. The previous 02b4 reference uses different repetition
counts, so cross-reference medians are not a like-for-like improvement claim.

| Operation | Regions | Median signed | Median absolute | P90 absolute | Maximum absolute | Within 2% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| integer | 576 | +0.032% | 0.129% | 0.508% | 2.370% | 573/576 |
| fp | 432 | +0.049% | 0.104% | 0.605% | 1.144% | 432/432 |
| matrix_int | 48 | +0.193% | 0.193% | 0.752% | 11.111% | 47/48 |
| matrix_fp8 | 48 | +0.057% | 0.097% | 0.383% | 0.843% | 48/48 |
| rowmove | 24 | +2.485% | 2.629% | 8.696% | 44.186% | 4/24 |
| load | 60 | -6.015% | 7.525% | 27.386% | 42.796% | 3/60 |
| store | 60 | -5.506% | 6.053% | 16.339% | 29.503% | 3/60 |
| load_use | 24 | +85.668% | 85.668% | 149.243% | 150.587% | 0/24 |
| load_independent | 24 | +32.632% | 32.632% | 52.666% | 52.666% | 0/24 |
| store_compute | 24 | +21.688% | 21.688% | 35.058% | 35.079% | 0/24 |

Across all regions, median absolute total error is 0.155%, P90 is 6.522%, maximum
150.587%; 1,110/1,320 meet 2%. Issue-window median absolute error is 0.207%,
P90 6.552%; drain-window median is 4.688%, P90 86.154%. **The full ±2% gate
fails.** Load/use and memory/compute overlap remain major structural gaps.
FP8 timing rows here cover E4M3×E4M3 only. Separate archived finite functional
checks cover all four E4M3/E5M2 operand pairs with positive-one operands; that
is not special-value or all-MRF certification.

[All signed rows](../build/rvv-compute-heldout2-C12-source-VADD-root-validation-v1/regions.jsonl),
[validation](../build/rvv-compute-heldout2-C12-source-VADD-root-validation-v1/report.json),
[prediction freeze](../build/rvv-compute-heldout2-C12-source-VADD-selected-v1/freeze.json),
[hardware archive](../runs/fpga-rvv-compute-heldout2-asm-v1-001.capture.tar.gz).
The hardware completed kill → infrasetup → runworkload → kill, normal exit and
owned cleanup/capture. The model replay deliberately stopped after measuring
all regions; it does not prove normal whole-program completion or execution speed.
The [earlier 02b4 scope](SOURCE_COMPUTE_VALIDATION.md) remains historical evidence.

## JSON and Perfetto profiling

```sh
python3 -m iris_model.source_profile_run_v3 \
  --model build/C12-source-VADD35a-optional-package-v1/source_compute/manifest.json \
  --elf /absolute/unstripped-program.elf \
  --output build/my-new-source-profile --timeout 600
```

The [completed ASM demonstration](../build/source-profile-35a-fullASM-v7/summary.json)
includes 1,320 explicit regions, ELF function attribution and a
[Perfetto trace](../build/source-profile-35a-fullASM-v7/trace.json). It accounts for
4,358,400 integer adds, 2,016,000 floating adds and 14,794,752 logical MACs from
checked command/write joins. Source pipe-valid occupancy, OPU controller busy
and command age are distinct metrics. OPU pipe-valid occupancy, physical memory
traffic/bandwidth and hardware roofline remain unavailable/null. A preserved
156-byte final metadata fragment gets no work credit. See
[profiling semantics and commands](../iris_model/SOURCE_EVENT_PROFILING_V3.md).

## Merlin integration

The new manifests directly provide the hook's required ISA metadata. This
corrected recipe uses the literal `MERLIN_TARGET_PATH` variable. The frozen
historical delivery RUN.txt has a documentation substitution error in that one
export; the actual API tests and simulator/package bytes are unaffected.

```sh
export PYTHONPATH=/scratch/copparihollmann/iris-model/integration/merlin/src
export MERLIN_TARGET_PATH=/scratch/copparihollmann/iris-model/integration/iris-mlir
export MERLIN_IRIS_TIMING=empirical
export MERLIN_IRIS_ALLOW_EXPERIMENTAL_TIMING=1
export MERLIN_IRIS_MODEL_MANIFEST=/scratch/copparihollmann/iris-model/build/C12-source-VADD35a-optional-package-v1/source_compute/manifest.json
build/merlin-venv/bin/python - <<'PYCODE'
from merlin.runtime.backends.zephyr_model import MatrixRouting
from merlin.runtime.backends import device_simulator
route = MatrixRouting(support_target='iris', unit='saturn_vme', config='IrisConfig')
result = device_simulator.run(route, '/absolute/unstripped-program.elf', harts=3,
    active_hart_ids=(0,1), isa='rv64gcv_zfh_zvfh_zvl256b_zvbb',
    memory=(0x80000000,0x100000000), timeout=600)
print(result['stdout'].decode())
assert result['returncode'] == 0
PYCODE
```

Select `active_hart_ids=(0,)` for one matrix worker. Hart 2 supports ordinary RVV
and control, but selecting it for VME work is refused. Full source pins and dirty
changes are bound in the capsule and hardware manifest. Ethan's exact existing
image and original driver are preserved, but complete source/image association
remains unverified. Concurrent-hart, end-to-end Zephyr and large-LLM timing are
not qualified by isolated compute regions.

## Actual memory configuration and next work

A separately reviewed read-only observer ran after normal driver exit and before
owned final cleanup on the new FPGA job. Complete idle/device/lock checks passed
before and after. Actual FASED registers read writeLatency=30, readLatency=30,
writeMaxReqs=16, readMaxReqs=16 and relaxFunctionalModel=0. These are memory-model
target-clock units, not CPU cycles or a measured request round trip. They were
not substituted into this frozen model's inherited numerical parameters.
[Sealed hardware/config evidence](../build/rvv-compute-heldout2-asm-HARDWARE-sealed-receipt-v1/report.json).

Current structural work connects the source VMU request/response identities,
queues, complete read/write backing and FASED clock domains before fitting new
memory parameters or testing contention. Harts 0/1 remain the timing priority;
hart 2 retains functional behavior and contributes real modeled shared traffic.
No TracerV, AutoCounter, SynthPrint or useful HPM instrumentation is available
in this image; no physical utilization measurements are inferred from that absence.

The compute-hart vector DRAM path bypasses the scalar L1 data cache. Pinned Saturn
`536774bd4c5e3bcd7813a4a0dc72fc87e5818532`,
[Shuttle integration](../third_party/saturn/src/main/scala/shuttle/Integration.scala),
connects VMU dmem directly to TLSplitInterface, TLWidthWidget and TLBuffer before
ATL; the separate SG path handles its configured regions. It does not instantiate
a separate Saturn data cache there. The new source transport model preserves
those exact instances and upstream SIFQ/ROB ownership. This source fact is not a
new accuracy certificate for the inherited effective memory timing model.
