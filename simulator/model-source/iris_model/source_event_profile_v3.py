"""Receipt and clock-denominator admission around unchanged v2 accounting.

This checks consistency and byte identity, not authenticity of an arbitrary
receipt. Existing v2 journals, receipts and accounting remain immutable.
"""
import argparse
import datetime
import json
from pathlib import Path

from . import source_event_profile_v2 as accounting


def load(path):
    def pairs(items):
        value = {}
        for key, item in items:
            accounting.need(key not in value, "Duplicate receipt key: " + key)
            value[key] = item
        return value

    def invalid(value):
        raise ValueError("Non-finite receipt value: " + value)

    result = json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                        parse_constant=invalid)
    accounting.need(isinstance(result, dict), "Receipt object required")
    return result


def admit(receipt, events, features, console, elf, fixture=None, summary=None):
    """Validate actual process outcome separately from measured-only capture."""
    record = load(receipt)
    accounting.need(record.get("pass") is True, "Passed execution receipt required")
    accounting.need(record.get("timed_out") is False,
                    "Timed-out or unspecified execution cannot qualify profiling")
    accounting.need(record.get("error") is None, "Execution error contradicts success")
    if "unchanged_inputs_after_execution" in record:
        accounting.need(record["unchanged_inputs_after_execution"] is True,
                        "Changed execution inputs cannot qualify profiling")
    stopped = record.get("intentional_postmeasurement_debugger_stop", False)
    accounting.need(type(stopped) is bool, "Typed intentional-stop flag required")
    normal = record.get("normal_guest_exit_verified", not stopped)
    accounting.need(type(normal) is bool, "Typed normal-exit flag required")
    if normal:
        accounting.need(not stopped, "Normal and intentional-stop scopes conflict")
        code = record.get("exit_status")
        accounting.need(type(code) is int and code == 0,
                        "Normal profiling requires actual process exit status zero")
        if "debugger_exit_status" in record:
            accounting.need(type(record["debugger_exit_status"]) is int and
                            record["debugger_exit_status"] == 0,
                            "Debugger failure contradicts normal completion")
    else:
        accounting.need(stopped is True,
                        "Incomplete execution requires explicit intentional debugger stop")
        code = record.get("debugger_exit_status")
        accounting.need(type(code) is int and code == 0,
                        "Measured-only profiling requires successful debugger completion")
        accounting.need(record.get("exit_status") is None,
                        "Measured-only capture must not also claim a guest process exit")
        accounting.need(record.get("whole_run_timing_or_speed_claim") is False and
                        record.get("done_and_count_stable_before_and_after_readback") is True,
                        "Measured-only scope and stable completed-region proof required")
    # The inherited checks bind every journal/prefix/console and ELF/fixture.
    inherited = accounting.bound_execution(receipt, events, features, console, elf, fixture)
    accounting.need(inherited == record, "Receipt changed during admission")
    if summary is not None:
        path = Path(summary).resolve()
        accounting.need(record["output_sha256"].get(str(path)) == accounting.sha(path),
                        "Native summary denominator unbound/changed: " + str(path))
    return record, normal


def profile(events, features, console, summary, elf, rom, output, nm,
            region_map=None, region_format="iris-json", execution_complete=None,
            fixture=None, execution_receipt=None,
            capacity_report=accounting.ROOT / "build/throughput-limits-source-v3/report.json"):
    accounting.need(execution_receipt is not None, "Execution receipt required")
    output = Path(output).resolve()
    accounting.need(not output.exists(), "Fresh v3 output directory required")
    record, normal = admit(execution_receipt, events, features, console, elf,
                           fixture, summary)
    if execution_complete is not None:
        accounting.need(type(execution_complete) is bool and execution_complete == normal,
                        "Requested completion scope contradicts admitted process outcome")
    paths = [Path(__file__).resolve(), Path(accounting.__file__).resolve(),
             Path(execution_receipt).resolve(), Path(events).resolve(),
             Path(features).resolve(), Path(console).resolve(), Path(elf).resolve(),
             Path(rom).resolve(), Path(nm).resolve(), Path(capacity_report).resolve()]
    paths += [Path(p).resolve() for p in (fixture, summary, region_map) if p is not None]
    identities = {str(p): accounting.sha(p) for p in paths}
    output.mkdir(parents=True)
    result = accounting.profile(events, features, console, summary, elf, rom,
                                output / "accounting-v2", nm, region_map, region_format,
                                normal, fixture, execution_receipt, capacity_report)
    accounting.need(all(accounting.sha(p) == h for p, h in identities.items()),
                    "Admitted input changed during accounting")
    after, after_normal = admit(execution_receipt, events, features, console, elf,
                                fixture, summary)
    accounting.need(after == record and after_normal == normal,
                    "Execution outcome changed during accounting")
    evidence = dict(schema="iris.source_profile_admission.v3", **{"pass": True},
                    created_UTC=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    normal_execution_complete=normal,
                    native_summary_bound=summary is not None,
                    fixture_only_observed_horizon=summary is None,
                    arbitrary_receipt_authenticity_certified=False,
                    timing_accuracy_certified=False,
                    accounting_summary=str(output / "accounting-v2/summary.json"),
                    accounting_summary_sha256=accounting.sha(output / "accounting-v2/summary.json"),
                    input_sha256=identities)
    (output / "admission.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ("events", "features", "console", "elf", "rom", "output", "nm"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--region-map", type=Path)
    parser.add_argument("--region-format", choices=("none", "iris-json", "merlin-kernel-v1"), default="none")
    args = parser.parse_args()
    try:
        result = profile(args.events, args.features, args.console, args.summary,
                         args.elf, args.rom, args.output, args.nm, args.region_map,
                         args.region_format, None, args.fixture, args.execution_receipt)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, "iris-source-profile-v3: " + str(error) + "\n")
    print(json.dumps({"pass": result["pass"], "regions": len(result["regions"]),
                      "journal_complete": result["journal_complete"],
                      "admission": str(args.output.resolve() / "admission.json")}))


if __name__ == "__main__":
    main()
