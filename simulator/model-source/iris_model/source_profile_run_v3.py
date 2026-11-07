"""Guarded ELF execution and v3 receipt admission with unchanged v2 accounting."""
import argparse
import datetime
import json
from pathlib import Path
import signal
import sys

from . import source_profile_run_v1 as execution
from . import source_event_profile_v3 as admission

A = admission.accounting


def run(elf, manifest, output, timeout=600, seed=0, region_map=None,
        region_format="iris-json"):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = dict(schema="iris.source_profile_ELF_execution.v3", status="preflight",
                   **{"pass": False}, profile_pass=False, error=None,
                   hardware_contacted=False, native_model_or_provider_changed=False,
                   independent_numeric_validation=None, timing_accuracy_certified=False,
                   started_at_UTC=datetime.datetime.now(datetime.timezone.utc).isoformat())
    paths = (Path(__file__).resolve(), Path(execution.__file__).resolve(),
             Path(admission.__file__).resolve(), Path(A.__file__).resolve(),
             Path(elf).resolve(), Path(manifest).resolve())
    inputs = {str(p): A.sha(p) for p in paths}

    def save():
        (output / "run.json").write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        # The existing launcher alone owns fatal resolution, ISA/devices,
        # provider admission, timeout and child-process cleanup.
        executed = execution.run(elf, manifest, output / "execution-v1", timeout,
                                 seed, "source-events.jsonl", region_map, region_format)
        receipt.update(execution_receipt=str(output / "execution-v1/run.json"),
                       v1_execution_pass=executed.get("pass"),
                       v1_observer_result_preserved=executed.get("source_profile"))
        A.need(executed.get("pass") is True and
               executed.get("unchanged_inputs_after_execution") is True,
               "Unchanged guarded v1 execution did not pass: " + str(executed.get("error")))
        execution.checked_inputs(executed["input_sha256"])
        fixture = [a.split("=", 1)[1] for a in executed["argv"]
                   if a.startswith("--iris-timing-fixture=")]
        rom = [a.split("=", 1)[1] for a in executed["argv"]
               if a.startswith("--iris-bootrom=")]
        A.need(len(fixture) == len(rom) == 1, "Unique bound fixture and ROM required")
        nm = Path("/scratch/eygao/iris-cy/.conda-env/riscv-tools/bin/riscv64-unknown-elf-nm")
        profile = output / "source-profile-v3"
        result = admission.profile(
            output / "execution-v1/source-events.jsonl", output / "execution-v1/features.jsonl",
            output / "execution-v1/stdout", output / "execution-v1/native-summary.json",
            Path(elf).resolve(), rom[0], profile, nm, region_map, region_format,
            True, fixture[0], output / "execution-v1/run.json")
        A.need(all(A.sha(p) == h for p, h in inputs.items()),
               "Frontend, ELF or model changed during execution/profile")
        receipt.update(status="passed_guarded_execution_and_source_profile", profile_pass=True,
                       **{"pass": True}, source_profile=dict(
                           admission=str(profile / "admission.json"),
                           summary=str(profile / "accounting-v2/summary.json"),
                           trace=str(profile / "accounting-v2/trace.json"),
                           commands=str(profile / "accounting-v2/commands.jsonl"),
                           journal_complete=result["journal_complete"],
                           regions=len(result["regions"])),
                       guest_exit_pass_is_not_independent_numeric_or_timing_certification=True)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        receipt.update(status="refused_or_failed", error=str(error), **{"pass": False})
    except BaseException as error:
        receipt.update(status="interrupted", error=str(error), **{"pass": False})
        raise
    finally:
        receipt["input_sha256"] = inputs
        receipt["finished_at_UTC"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt["output_sha256"] = {str(p): A.sha(p) for p in output.rglob("*")
                                    if p.is_file() and p != output / "run.json"}
        save()
    return receipt


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Interrupted by signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True,
                        help="Explicit guarded provider manifest; no implicit selection")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--region-map", type=Path)
    parser.add_argument("--region-format", choices=("none", "iris-json", "merlin-kernel-v1"), default="iris-json")
    args = parser.parse_args()
    try:
        result = run(args.elf, args.model, args.output, args.timeout, args.seed,
                     args.region_map, args.region_format)
    except (OSError, ValueError) as error:
        parser.exit(1, "iris-source-profile-v3: " + str(error) + "\n")
    print(json.dumps({k: result.get(k) for k in
                      ("pass", "profile_pass", "status", "error", "source_profile")}))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
