"""Profile online graph events and gate held-out target-cycle measurements.

No parameter fitting or accuracy certification is inferred from a synthetic trace.
"""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from .memory_profile import memory_summary
from .counter_regions import METHOD, observations, region_metrics
from .event_lifecycle import cancelled, completed, issued, physical_edge, unresolved_payload, validate_events


def union_cycles(intervals):
    total, end = 0, None
    for start, stop in sorted(intervals):
        if stop < start:
            raise ValueError("negative event duration")
        total += max(0, stop - max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return total


def symbols(elf, nm):
    """Read actual ELF symbol bounds; never attribute gaps to the preceding function."""
    output = subprocess.run([nm, "-S", "-n", "--defined-only", str(elf)],
                            check=True, text=True, capture_output=True).stdout
    result = []
    for line in output.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4 and fields[2] in ("t", "T", "w", "W"):
            address, size = int(fields[0], 16), int(fields[1], 16)
            if size:
                result.append((address, address + size, fields[3]))
    return sorted(result)


def profile(graph, clock_hz, clock_source, symbol_table=(), peaks=None, roofline_memory_resource=None):
    if graph.get("schema_version") != 1:
        raise ValueError("unsupported graph schema")
    if type(graph.get("harts")) is not int or graph["harts"] <= 0:
        raise ValueError("positive integer graph hart count required")
    if type(graph.get("cycles")) is not int or graph["cycles"] < 0:
        raise ValueError("nonnegative integer graph cycles required")
    if not math.isfinite(clock_hz) or clock_hz <= 0 or not clock_source:
        raise ValueError("explicit positive target clock and provenance required")
    if graph.get("base_clock_hz", clock_hz) != clock_hz:
        raise ValueError("profile clock disagrees with graph base clock")
    if type(graph.get("partial", False)) is not bool or type(graph.get("pending_operations", 0)) is not int or graph.get("pending_operations", 0) < 0:
        raise ValueError("invalid partial graph metadata")
    if not graph.get("partial", False) and graph.get("pending_operations", 0):
        raise ValueError("final graph has unfinished operations")
    ids = validate_events(graph)
    events = graph["events"]
    groups, traces, function_groups = {}, [], {}
    resources = graph.get("resources", {})
    for name, lanes in resources.items():
        if not isinstance(name, str) or not name or type(lanes) is not int or lanes <= 0:
            raise ValueError("invalid configured resource lanes")
    peaks = peaks or {}
    if roofline_memory_resource and (graph.get("traffic_accounting") == "not_instrumented" or
                                    graph.get("arithmetic_accounting") == "not_instrumented"):
        raise ValueError("roofline requires instrumented arithmetic and memory traffic")
    for name, specification in peaks.items():
        if not specification.get("source"):
            raise ValueError("peak parameter provenance missing for " + name)
        for key in ("macs_per_cycle", "bytes_per_cycle"):
            value = specification.get(key)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("peak parameters must be finite and positive")
    starts = [entry[0] for entry in symbol_table]
    boundary_map={}
    for boundary in graph.get("region_boundaries",[]):
        key=(boundary["hart"],boundary["region"])
        if (type(boundary["hart"]) is not int or not 0<=boundary["hart"]<graph["harts"] or
            not isinstance(boundary["region"],str) or not boundary["region"] or
            not isinstance(boundary["source"],str) or not boundary["source"] or key in boundary_map or
            type(boundary["start"]) is not int or type(boundary["end"]) is not int or
            not 0<=boundary["start"]<boundary["end"]<=graph["cycles"]):
            raise ValueError("invalid or duplicate explicit region boundary")
        boundary_map[key]=boundary
    all_boundaries=boundary_map
    captured_regions=graph.get("region_projection_method")==METHOD
    if captured_regions:
        # Original operation/DAG spans stay intact. Captured read phases can cut
        # across an in-flight event, so their metrics use explicit intersections.
        boundary_map={}
    for event in events:
        function = None
        index = bisect.bisect_right(starts, event["pc"]) - 1
        if event.get('pc_known',True) and index >= 0 and event["pc"] < symbol_table[index][1]:
            function = symbol_table[index][2]
        region = event.get("region") or "unmarked"
        groups.setdefault((event["hart"], region), []).append(event)
        function_key=(event["hart"],region,function,symbol_table[index][0] if function else None)
        function_groups.setdefault(function_key,[]).append(event)
        if physical_edge(event):
            traces.append({'name': event['name'], 'cat': 'physical source edge', 'ph': 'i', 's': 't',
                'pid': event['hart'], 'tid': event['resource'], 'ts': event['start'] * 1e6 / clock_hz,
                'args': {'node': event['id'], 'region': region, 'function': function,
                         'pc_known': event['pc_known'], 'pc': hex(event['pc']) if event['pc_known'] else None,
                         'bytes': event['bytes'], 'macs': event['macs'], 'physical_source': event['physical_source'],
                         'completion_policy': 'same-source-edge', 'occupancy_cycles': 0}})
        elif event.get('physical_acceptance') and not completed(event) and event['end']==event['start']:
            traces.append({'name': event['name']+' accepted', 'cat': 'physical source acceptance', 'ph': 'i', 's': 't',
                'pid': event['hart'], 'tid': event['resource'], 'ts': event['start']*1e6/clock_hz,
                'args': {'node': event['id'], 'completed': False, 'request_bytes': event['bytes'],
                         'pc_known': event['pc_known'], 'function': function,
                         'physical_source': event['physical_source'], 'occupancy_cycles': 0}})
        if issued(event) and event['end'] > event['start']:
            traces.append({"name": event["name"], "cat": event["resource"], "ph": "X",
                       "pid": event["hart"], "tid": event["resource"],
                       "ts": event["start"] * 1e6 / clock_hz,
                       "dur": (event["end"] - event["start"]) * 1e6 / clock_hz,
                       "args": {"node": event["id"], "pc": hex(event["pc"]),
                                "function": function, "region": region,
                                "completed": event.get("completed", True)}})
            if event.get('squashed'):
                traces[-1]['args']['squashed'] = True
            if cancelled(event):
                traces[-1]['args'].update(cancelled=True, cancel_cycle=event['cancel_cycle'], cancel_reason=event['cancel_reason'])
        if cancelled(event):
            traces.append({'name': 'cancel '+event['name'], 'cat': 'cancellation', 'ph': 'i', 's': 't',
                'pid': event['hart'], 'tid': event['resource'], 'ts': event['cancel_cycle']*1e6/clock_hz,
                'args': {'node': event['id'], 'pc': hex(event['pc']), 'issued': issued(event),
                         'reason': event['cancel_reason'], 'consumed_cycles': event['end']-event['start']}})
    for event in events:
        for dep in event["deps"]:
            parent = ids[dep]
            if not issued(event):
                continue
            flow_id = str(dep) + ":" + str(event["id"])
            for phase, node, cycle in (("s", parent, parent["end"]), ("f", event, event["start"])):
                traces.append({"name": "dependency", "cat": "dependency", "ph": phase,
                               "id": flow_id, "pid": node["hart"], "tid": node["resource"],
                               "ts": cycle * 1e6 / clock_hz, "bp": "e"})
    summaries = []
    if set(boundary_map)-groups.keys(): raise ValueError("explicit guest region has no graph events")
    for (hart, region), group in sorted(groups.items()):
        start, end = min(e["submitted"] for e in group), max(e["end"] for e in group)
        boundary=boundary_map.get((hart,region))
        if boundary:
            start,end=boundary["start"],boundary["end"]
            if any(e["start"]<start or e["end"]>end for e in group):
                raise ValueError("event outside explicit guest region")
        duration = end - start
        units = {}
        for name in sorted({e["resource"] for e in group}):
            selected = [e for e in group if e["resource"] == name]
            busy = union_cycles((e["start"], e["end"]) for e in selected)
            byte_count, macs = sum(e["bytes"] for e in selected if completed(e)), sum(e["macs"] for e in selected if completed(e))
            peak = peaks.get(name, {}).get("macs_per_cycle")
            if peak is not None and (not math.isfinite(peak) or peak <= 0):
                raise ValueError("peak throughput must be positive and sourced")
            units[name] = {"busy_cycles": busy, "busy_fraction": busy / duration if duration else 0,
                           "occupancy_basis": "union_of_in_flight_operation_spans",
                           "bytes": byte_count, "bytes_per_cycle": byte_count / duration if duration else None,
                           "macs": macs, "macs_per_cycle": macs / duration if duration else None,
                           "achieved_fraction_of_peak": macs / duration / peak if peak and duration else None}
            units[name]["incomplete_operations"] = sum(not completed(event) and not cancelled(event) for event in selected)
            if any(physical_edge(event) for event in selected):
                units[name].update(physical_edge_operations=sum(physical_edge(event) for event in selected),
                                   occupancy_excludes_physical_point_work=True)
            if any(cancelled(event) or event.get('squashed') for event in selected):
                units[name]['cancelled_operations'] = sum(cancelled(event) for event in selected)
                units[name]['squashed_completed_model_operations'] = sum(completed(event) and event.get('squashed',False) for event in selected)
            if graph.get("traffic_accounting") == "not_instrumented" or any(unresolved_payload(event,'bytes') for event in selected):
                units[name]["bytes"] = units[name]["bytes_per_cycle"] = None
            if graph.get("arithmetic_accounting") == "not_instrumented" or any(unresolved_payload(event,'macs') for event in selected):
                units[name]["macs"] = units[name]["macs_per_cycle"] = units[name]["achieved_fraction_of_peak"] = None
        roofline = None
        if roofline_memory_resource is not None:
            if not duration:
                raise ValueError('roofline needs a nonempty measured interval')
            memory = units.get(roofline_memory_resource)
            bandwidth = peaks.get(roofline_memory_resource, {}).get("bytes_per_cycle")
            compute_peak = sum(peaks.get(name, {}).get("macs_per_cycle", 0) for name in units)
            if not memory or not memory["bytes"] or not bandwidth or not compute_peak:
                raise ValueError("roofline needs measured traffic and sourced bandwidth/compute peaks")
            if any(unit["macs"] is None for unit in units.values()):
                raise ValueError("roofline requires completed instrumented operations")
            ops = 2 * sum(unit["macs"] for unit in units.values())
            intensity = ops / memory["bytes"]
            roofline = {"memory_resource": roofline_memory_resource,
                        "operation_convention": "two integer operations per MAC",
                        "arithmetic_intensity_ops_per_byte": intensity,
                        "achieved_ops_per_cycle": ops / duration,
                        "compute_peak_ops_per_cycle": 2 * compute_peak,
                        "memory_peak_bytes_per_cycle": bandwidth,
                        "ceiling_ops_per_cycle": min(2 * compute_peak, bandwidth * intensity)}
        summaries.append({"hart": hart, "region": region, "start_cycle": start,
                          "end_cycle": end, "cycles": duration,
                          "region_boundary_source": boundary["source"] if boundary else "operation submission/completion span",
                          "guest_mcycle_delta": boundary.get("guest_mcycle_delta") if boundary else None,
                          "units": units,
                          "operation_queue_wait_cycles_sum": sum(e["start"] - max(start,e["submitted"]) for e in group),
                          "stall_breakdown": None,
                          "roofline": roofline})
    # Global resource occupancy uses union time, not summed overlapping operation latencies.
    occupancy = {}
    for name in sorted({e["resource"] for e in events}):
        intervals = [(e["start"], e["end"]) for e in events if e["resource"] == name]
        occupancy[name] = {"busy_cycles": union_cycles(intervals),
                           "busy_fraction": union_cycles(intervals) / graph["cycles"] if graph["cycles"] else 0,
                           "occupancy_basis": "union_of_in_flight_operation_spans",
                           "configured_issue_lanes": resources.get(name)}
        if any(physical_edge(event) for event in events if event['resource']==name):
            occupancy[name].update(physical_edge_operations=sum(physical_edge(event) for event in events if event['resource']==name),
                                   occupancy_excludes_physical_point_work=True)
    functions=[]
    for (hart,region,name,address),group in sorted(function_groups.items(),key=lambda item:(item[0][0],item[0][1],item[0][3] or -1)):
        functions.append({"hart":hart,"region":region,"function":name,"symbol_address":address,
            "attribution_basis":"ELF bounds at originating operation PC; model events are not function calls or retired instructions",
            "model_operations":len(group),"completed_model_operations":sum(completed(e) for e in group),
            "active_cycles":union_cycles((e["start"],e["end"]) for e in group),
            "resources":{resource:{"active_cycles":union_cycles((e["start"],e["end"]) for e in group if e["resource"]==resource)}
                         for resource in sorted({e["resource"] for e in group})},
            **({'cancelled_model_operations':sum(cancelled(e) for e in group),
                'squashed_completed_model_operations':sum(completed(e) and e.get('squashed',False) for e in group)}
               if any(cancelled(e) or e.get('squashed') for e in group) else {})})
    captured=observations(graph) if "counter_read_observations" in graph else []
    measured=region_metrics(graph,symbol_table,peaks)
    for read in captured:
        traces.append({"name":"CSR "+hex(read["csr"]),"cat":"captured counter read","ph":"i","s":"t",
            "pid":read["hart"],"tid":"counter reads","ts":read["read_cycle"]*1e6/clock_hz,
            "args":{"node":read["node"],"pc":hex(read["pc"]),"value":hex(read["value"]),
                    "read_phase":read["read_phase"],"sequence":read["sequence"],
                    "destination":read["destination"],"result_discarded":read["result_discarded"]}})
    for boundary in all_boundaries.values():
        traces.append({"name":boundary["region"],"cat":"guest region","ph":"X",
            "pid":boundary["hart"],"tid":"measured regions","ts":boundary["start"]*1e6/clock_hz,
            "dur":(boundary["end"]-boundary["start"])*1e6/clock_hz,
            "args":{"guest_mcycle_delta":boundary.get("guest_mcycle_delta"),"boundary_source":boundary["source"]}})
    return ({"schema_version": 1, "status": graph["status"],
             "partial": graph.get("partial", False),
             "pending_operations": graph.get("pending_operations", 0),
             "clock_hz": clock_hz, "clock_source": clock_source,
             "cycle_unit": "base_clock_ticks",
             "hart_cycle_dividers": graph.get("hart_cycle_dividers"),
             "cycles": graph["cycles"], "regions": summaries, "resources": occupancy,
             "functions":functions,
             "observed_regions":measured,
             "captured_counter_reads":len(captured),
             "modeled_memory": memory_summary(graph,ids),
             "limitations": ["Captured read timestamps define observed_regions; original region groups and dependency events retain their full spans." if captured_regions else
                              "Only explicitly matched regions have guest CSR boundaries; unmarked groups use operation spans." if boundary_map else "Operation spans are not guest CSR-delimited regions.",
                              "No calibrated stall classification or roofline is inferred."],
             **({'lifecycle_accounting':{
                 'cancelled_model_operations':sum(cancelled(e) for e in events),
                 'cancelled_before_issue':sum(cancelled(e) and not issued(e) for e in events),
                 'squashed_completed_model_operations':sum(completed(e) and e.get('squashed',False) for e in events),
                 'occupancy':'Canceled issued spans stop at cancellation; pending cancellations consume no busy time; completed squashed work retains physical occupancy',
                 'work':'Canceled payloads are never credited as completed; partial issued payloads remain unavailable without progress instrumentation',
                 'model_operations_are_retired_instructions':False}}
                if any(cancelled(e) or e.get('squashed') for e in events) else {})},
            {"traceEvents": traces, "displayTimeUnit": "ns",
             "metadata": {"clock_hz": clock_hz, "clock_source": clock_source,
                          "partial": graph.get("partial", False),
                          "pending_operations": graph.get("pending_operations", 0),
                          "status": graph["status"]}})


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("full SHA256 identity required")
    return value


def validate(data, tolerance=0.02):
    """Gate supplied measurements; this never promotes an uncalibrated model."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("invalid accuracy tolerance")
    hardware = digest(data["hardware_sha256"])
    calibration = data["calibration"]
    digest(calibration["sha256"])
    if digest(calibration["hardware_sha256"]) != hardware:
        raise ValueError("calibration hardware identity mismatch")
    simulator = digest(data["simulator_sha256"])
    if digest(calibration["simulator_sha256"]) != simulator:
        raise ValueError("calibration simulator identity mismatch")
    fitted = set(calibration["fitting_case_ids"])
    rows, keys = [], set()
    for record in data["measurements"]:
        key = (record["case_id"], record["region"], record["harts"])
        if key in keys or record["case_id"] in fitted:
            raise ValueError("duplicate measurement or held-out case used for fitting")
        keys.add(key)
        digest(record["elf_sha256"])
        if digest(record["hardware_sha256"]) != hardware:
            raise ValueError("measurement hardware identity mismatch")
        if digest(record["simulator_sha256"]) != simulator:
            raise ValueError("measurement simulator identity mismatch")
        reference, modeled = record["reference_cycles"], record["modeled_cycles"]
        if type(reference) is not int or type(modeled) is not int or reference <= 0 or modeled < 0:
            raise ValueError("integer target-cycle measurements required")
        if record.get("reference_kind") not in ("firesim_target_cycles", "rtl_target_cycles"):
            raise ValueError("ground truth must be RTL target cycles")
        error = (modeled - reference) / reference
        rows.append(dict(record, signed_error_percent=100 * error,
                         passed=abs(error) <= tolerance))
    if not rows:
        raise ValueError("held-out measurements missing")
    return {"schema_version": 1, "hardware_sha256": hardware,
            "simulator_sha256": simulator, "tolerance_percent": 100 * tolerance,
            "status": "passed_supplied_measurements" if all(r["passed"] for r in rows) else "failed",
            "certified": False,
            "certification_note": "Coverage, functional certification and independent holdout provenance require separate review.",
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    trace = sub.add_parser("profile")
    trace.add_argument("graph", type=Path)
    trace.add_argument("--clock-hz", type=float, required=True)
    trace.add_argument("--clock-source", required=True)
    trace.add_argument("--output", type=Path, required=True)
    trace.add_argument("--elf", type=Path)
    trace.add_argument("--nm", default="nm")
    trace.add_argument("--peaks", type=Path, help="JSON resource peaks with mandatory source identities")
    trace.add_argument("--roofline-memory-resource", help="one traffic level; never sum bytes across levels")
    gate = sub.add_parser("validate")
    gate.add_argument("measurements", type=Path)
    gate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "profile":
            table = symbols(args.elf, args.nm) if args.elf else []
            summary, timeline = profile(json.loads(args.graph.read_text()), args.clock_hz,
                                        args.clock_source, table,
                                        json.loads(args.peaks.read_text()) if args.peaks else None,
                                        args.roofline_memory_resource)
            summary["graph_sha256"] = hashlib.sha256(args.graph.read_bytes()).hexdigest()
            summary["tool_source_sha256"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [Path(__file__).resolve(),Path(__file__).with_name("memory_profile.py"),
                          Path(__file__).with_name("counter_regions.py"),
                          Path(__file__).with_name("event_lifecycle.py")]}
            if args.elf:
                summary["elf_sha256"] = hashlib.sha256(args.elf.read_bytes()).hexdigest()
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            (args.output / "timeline.json").write_text(json.dumps(timeline) + "\n")
        else:
            result = validate(json.loads(args.measurements.read_text()))
            args.output.write_text(json.dumps(result, indent=2) + "\n")
            if result["status"] == "failed":
                return 1
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, "iris-report: " + str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
