> Historical documentation snapshot from the external `iris-model` workspace.
> Its relative artifact/source links resolve in that original workspace.
> For this branch's commands and limitations, use [README.md](README.md).

# Guarded source profiling

Use v3 for new profiles. It adds receipt and native-summary admission around the
unchanged [v2 accounting](SOURCE_EVENT_PROFILING_V2.md), preserving old evidence.
The model, its parameters and all default selections are unchanged.

```sh
python3 -m iris_model.source_profile_run_v3 \
  --model build/C12-source-VADD35a-optional-package-v1/source_compute/manifest.json \
  --elf /absolute/unstripped-program.elf \
  --output build/my-new-profile --timeout 600
```

The top-level `run.json` must pass. Execution evidence stays in `execution-v1`.
`source-profile-v3/admission.json` records the new checks; the JSON metrics,
Perfetto trace and command table are in `source-profile-v3/accounting-v2`.
Source events must actually be enabled by the selected model. This command does
not enable an unsupported actor or change the model's IRQ/context restrictions.

For offline use, the arguments match the v2 consumer:

```sh
python3 -m iris_model.source_event_profile_v3 \
  --events /absolute/source-events.jsonl \
  --features /absolute/features.jsonl --console /absolute/stdout \
  --summary /absolute/native-summary.json \
  --execution-receipt /absolute/run.json \
  --elf /absolute/program.elf --rom board/evidence/bootrom.rv64.img \
  --fixture /absolute/runtime.fixture \
  --nm /scratch/eygao/iris-cy/.conda-env/riscv-tools/bin/riscv64-unknown-elf-nm \
  --output build/my-new-offline-profile
```

The native summary supplies whole-program clock denominators. Its exact path and
SHA256 must occur in the execution receipt's output map, alongside the journal,
prefixes and console. The ELF and fixture must match the input map. A copied or
modified unbound summary is refused even when its clock fields look plausible.
For the existing deliberate measured-only debugger capture, omit `--summary`
and use its bound fixture. The observed journal horizon is reported without
claiming normal program completion or whole-program speed.

Normal execution requires typed exit status zero, an explicit false timeout
flag and no contradictory intentional-stop or error claim. Measured-only scope
requires its explicit successful debugger exit, stable completed-region proof
and false whole-run timing/speed claim. Inputs are rechecked after accounting.
These checks establish consistency and byte identity; they do not authenticate
an arbitrary receipt authored elsewhere.

The [independent v2 review](../build/source-event-profiler-v2-independent-review-closure-v1/report.json)
recomputed all demonstrated work/regions/functions correctly, but found two
offline admission gaps: unbound summary denominators and contradictory process
outcomes. Both exact counterexamples now refuse, with 15 independent admission
checks including the actual 1,320-region controlled-stop receipt.
[V3 review](../build/source-event-profiler-v3-independent-admission-v1/report.json).

The live v3 frontend passed the frozen 35a three-hart smoke, all 136 output-word
checks, three actual CSR regions and normal exit. Raw stdout, stderr, source
journal, CSR prefixes and native summary are byte-identical to the prior v2
execution. The initial v3 reporting-directory failure remains preserved.
[Delivery and byte parity](../build/source-event-profiler-v3-delivery-root-v1/report.json).

All v2 work definitions and limitations still apply: source pipe-valid occupancy,
controller busy and command age are separate. OPU pipe-valid occupancy, physical
memory traffic/bandwidth and hardware roofline remain unavailable. Process exit
and a successful profile do not certify numerical correctness of another ELF,
physical utilization, source/image equivalence or ±2% timing accuracy.
