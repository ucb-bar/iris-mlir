"""Join regions to captured CSR results and intersect actual model event spans.

Counter values may change independently of elapsed model time. This module never
infers a read value from a clock divider or a fixed counter offset.
"""
import bisect
import copy
import json
from .event_lifecycle import cancelled, completed, issued, physical_edge, validate_lifecycle, validate_events, cancelled_payload_unknown

MASK64 = (1 << 64) - 1
CYCLE_CSRS = {0xB00, 0xC00}
COUNTER_CSRS = CYCLE_CSRS | {0xB02, 0xC02} | set(range(0xB03, 0xB20)) | set(range(0xC03, 0xC20))
METHOD = "captured_counter_reads"


def integer(value, label, upper=None):
    if type(value) is not int or value < 0 or (upper is not None and value >= upper):
        raise ValueError("invalid counter observation " + label)
    return value


def observations(graph, instructions=None):
    capture = graph.get("counter_read_observations")
    if (not isinstance(capture, dict) or type(capture.get("schema_version")) is not int or capture["schema_version"] != 1 or
            capture.get("complete") is not True or not isinstance(capture.get("source"), str) or
            not capture["source"].strip() or not isinstance(capture.get("reads"), list)):
        raise ValueError("complete captured counter-read observations with source are required")
    harts = integer(graph["harts"], "hart count")
    cycles = integer(graph["cycles"], "run cycles")
    nodes = validate_events(graph)
    sequence, last_cycle, seen_nodes = {}, {}, set()
    for read in capture["reads"]:
        if not isinstance(read, dict):
            raise ValueError("invalid counter observation record")
        hart = integer(read["hart"], "hart", harts)
        number = integer(read["sequence"], "sequence")
        node = integer(read["node"], "node")
        pc = integer(read["pc"], "PC", 1 << 64)
        raw = integer(read["raw"], "instruction", 1 << 32)
        csr = integer(read["csr"], "CSR", 1 << 12)
        destination = integer(read["destination"], "destination", 32)
        if type(read.get("result_discarded")) is not bool or read["result_discarded"] != (destination == 0):
            raise ValueError("counter read destination/discard state disagrees")
        integer(read["value"], "value", 1 << 64)
        cycle = integer(read["read_cycle"], "read cycle", cycles + 1)
        if not isinstance(read.get("read_phase"), str) or not read["read_phase"].strip():
            raise ValueError("counter read phase must be explicit")
        if number != sequence.get(hart, 0) or cycle < last_cycle.get(hart, 0):
            raise ValueError("counter read sequence/time is not ordered per hart")
        sequence[hart] = number + 1
        last_cycle[hart] = cycle
        if node in seen_nodes or node not in nodes:
            raise ValueError("missing or duplicate counter observation node")
        seen_nodes.add(node)
        event = nodes[node]
        integer(event["hart"], "event hart", harts)
        integer(event["pc"], "event PC", 1 << 64)
        integer(event["submitted"], "event submission", cycles + 1)
        integer(event["start"], "event start", cycles + 1)
        integer(event["end"], "event end", cycles + 1)
        validate_lifecycle(event, cycles, graph.get('partial',False))
        if (not completed(event) or event.get('squashed',False) or event.get('pc_known',True) is False or event["hart"] != hart or event["pc"] != pc or
                not event["submitted"] <= cycle <= event["end"] or
                not event["submitted"] <= event["start"] < event["end"]):
            raise ValueError("counter observation disagrees with completed instruction event")
        if (pc & 1 or raw & 127 != 0x73 or (raw >> 12) & 7 not in (1, 2, 3, 5, 6, 7) or
                (raw >> 7) & 31 != destination or raw >> 20 != csr or csr not in COUNTER_CSRS):
            raise ValueError("counter observation does not encode a returning counter CSR read")
        if instructions is not None and instructions.read(pc) != raw:
            raise ValueError("counter observation instruction disagrees with ELF/ROM")
    return capture["reads"]


def annotate(graph, console, instructions):
    reads = observations(graph, instructions)
    by_value = {}
    for read in reads:
        if read["csr"] in CYCLE_CSRS and not read["result_discarded"]:
            by_value.setdefault((read["hart"], read["value"]), []).append(read)
    boundaries, seen = [], set()
    for line in console.splitlines():
        if not line.startswith("IRIS_REGION "):
            continue
        row = json.loads(line.split(" ", 1)[1])
        hart = integer(row["hart"], "region hart", graph["harts"])
        for name in ("case_id", "region"):
            if not isinstance(row.get(name), str) or not row[name]:
                raise ValueError("region identity must be nonempty")
        name = row["case_id"] + "::" + row["region"]
        key = (hart, name)
        if key in seen:
            raise ValueError("duplicate guest region identity")
        seen.add(key)
        matched = []
        for field in ("start_cycle", "end_cycle"):
            value = integer(row[field], "guest " + field, 1 << 64)
            selected = by_value.get((hart, value), [])
            if len(selected) != 1:
                raise ValueError("missing or ambiguous captured mcycle boundary " + repr((key, field, value)))
            matched.append(selected[0])
        first, last = matched
        if last["sequence"] <= first["sequence"] or last["read_cycle"] <= first["read_cycle"]:
            raise ValueError("reversed or zero-duration observed region")
        boundaries.append({"hart": hart, "region": name, "start": first["read_cycle"], "end": last["read_cycle"],
                           "guest_start_cycle": first["value"], "guest_end_cycle": last["value"],
                           "guest_mcycle_delta": (last["value"] - first["value"]) & MASK64,
                           "start_node": first["node"], "end_node": last["node"],
                           "start_read_sequence": first["sequence"], "end_read_sequence": last["sequence"],
                           "projection_method": METHOD,
                           "source": "guest values matched to captured CSR results and read timestamps; " + graph["counter_read_observations"]["source"]})
    if not boundaries:
        raise ValueError("guest region records missing")
    result = copy.deepcopy(graph)
    result["region_boundaries"] = boundaries
    result["region_projection_method"] = METHOD
    return result


def intersection(start, end, lower, upper):
    return max(lower, start), min(upper, end)


def union(intervals):
    total, frontier = 0, None
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if frontier is None or start >= frontier:
            total += end - start
        elif end > frontier:
            total += end - frontier
        frontier = max(end, frontier if frontier is not None else end)
    return total


def region_metrics(graph, symbol_table=(), peaks=None):
    if graph.get("region_projection_method") != METHOD:
        return []
    reads = {(read["hart"], read["sequence"]): read for read in observations(graph)}
    starts = [symbol[0] for symbol in symbol_table]
    peaks = peaks or {}
    output = []
    for boundary in graph["region_boundaries"]:
        hart, lower, upper = boundary["hart"], boundary["start"], boundary["end"]
        first = reads.get((hart, boundary["start_read_sequence"]))
        last = reads.get((hart, boundary["end_read_sequence"]))
        if (boundary.get("projection_method") != METHOD or first is None or last is None or
                first["csr"] not in CYCLE_CSRS or last["csr"] not in CYCLE_CSRS or
                first["result_discarded"] or last["result_discarded"] or last["sequence"] <= first["sequence"] or
                (first["node"], first["read_cycle"], first["value"], last["node"], last["read_cycle"], last["value"]) !=
                (boundary["start_node"], lower, boundary["guest_start_cycle"], boundary["end_node"], upper, boundary["guest_end_cycle"]) or
                boundary["guest_mcycle_delta"] != (last["value"] - first["value"]) & MASK64):
            raise ValueError("region boundary disagrees with captured read evidence")
        duration = upper - lower
        if duration <= 0:
            raise ValueError("invalid observed region duration")
        selected = [event for event in graph["events"] if event["hart"] == hart and
                    (event["submitted"] < upper and event["end"] >= lower)]
        for event in selected:
            validate_lifecycle(event, graph['cycles'], graph.get('partial',False))
        units, functions = {}, {}
        for resource in sorted({event["resource"] for event in selected}):
            group = [event for event in selected if event["resource"] == resource]
            active = [intersection(event["start"], event["end"], lower, upper) for event in group]
            busy = union(active)
            wait = sum(max(0, min(event["start"], upper) - max(event["submitted"], lower)) for event in group)
            finished = [event for event in group if completed(event) and
                        (lower <= event['end'] < upper if physical_edge(event) else lower < event['end'] < upper)]
            tied = [event for event in group if completed(event) and not physical_edge(event) and event["end"] in (lower, upper)]
            byte_tie, mac_tie = any(event["bytes"] for event in tied), any(event["macs"] for event in tied)
            canceled_bytes = any(cancelled_payload_unknown(event,'bytes',lower,upper) for event in group)
            canceled_macs = any(cancelled_payload_unknown(event,'macs',lower,upper) for event in group)
            byte_count = None if graph.get("traffic_accounting") != "instrumented" or byte_tie or canceled_bytes else sum(event["bytes"] for event in finished)
            macs = None if graph.get("arithmetic_accounting") != "instrumented" or mac_tie or canceled_macs else sum(event["macs"] for event in finished)
            peak = peaks.get(resource, {}).get("macs_per_cycle")
            units[resource] = {"busy_cycles": busy, "busy_fraction": busy / duration,
                               "occupancy_basis": "union of operation spans intersected with captured read timestamps",
                               "operations_started": sum(issued(event) and lower <= event["start"] < upper for event in group),
                               "operations_completed_strictly_inside": sum(lower < event['end'] < upper for event in finished),
                               "incomplete_operations": sum(not completed(event) and not cancelled(event) for event in group),
                               "operation_wait_cycles_sum": wait,
                               "completed_bytes": byte_count, "completed_bytes_per_cycle": byte_count / duration if byte_count is not None else None,
                               "completed_macs": macs, "completed_macs_per_cycle": macs / duration if macs is not None else None,
                               "achieved_fraction_of_peak": macs / duration / peak if macs is not None and peak else None,
                               "boundary_byte_completion_unresolved": byte_tie, "boundary_mac_completion_unresolved": mac_tie}
            if any(physical_edge(event) for event in group):
                units[resource].update(physical_edge_operations_completed=sum(physical_edge(event) for event in finished),
                    operations_completed_in_region=len(finished),
                    physical_edge_boundary_policy='declared physical commit points in [start,end); zero occupancy')
            if any(cancelled(event) or event.get('squashed') for event in group):
                units[resource].update(cancelled_operations=sum(cancelled(event) for event in group),
                    squashed_completed_model_operations=sum(completed(event) and event.get('squashed',False) for event in group),
                    cancelled_byte_payload_unresolved=bool(canceled_bytes),cancelled_mac_payload_unresolved=bool(canceled_macs))
        for event in selected:
            start, end = intersection(event["start"], event["end"], lower, upper)
            if end <= start and not cancelled(event) and not (physical_edge(event) and lower <= event['end'] < upper):
                continue
            index = bisect.bisect_right(starts, event["pc"]) - 1
            symbol = symbol_table[index] if event.get('pc_known',True) and index >= 0 and event["pc"] < symbol_table[index][1] else None
            key = (symbol[2], symbol[0]) if symbol else (None, None)
            item = functions.setdefault(key, {"intervals": [], "nodes": set(), "resources": {}})
            item["intervals"].append((start, end));item["nodes"].add(event["id"])
            item["resources"].setdefault(event["resource"], []).append((start, end))
        output.append({"hart": hart, "region": boundary["region"], "start_cycle": lower, "end_cycle": upper,
                       "cycles": duration, "guest_mcycle_delta": boundary["guest_mcycle_delta"],
                       "region_boundary_source": boundary["source"], "units": units,
                       "functions": [{"function": key[0], "symbol_address": key[1], "model_operations": len(value["nodes"]),
                                      "active_cycles": union(value["intervals"]),
                                      "resources": {name: {"active_cycles": union(spans)} for name, spans in value["resources"].items()}}
                                     for key, value in sorted(functions.items(), key=lambda item: item[0][1] if item[0][1] is not None else -1)],
                       "stall_breakdown": None, "roofline": None,
                       "accounting": {"work": ("completed spans strictly inside read timestamps; their boundary payload ties remain unavailable; explicitly declared physical points use [start,end) with zero occupancy"
                                                if any(physical_edge(e) for e in selected) else
                                                "completed events strictly inside read timestamps; boundary payload ties remain unavailable"),
                                      "wait": "sum of submission-to-start intersections for visible operations; includes dependencies and resource waits, not additive CPU stalls",
                                      "functions": "ELF bounds at originating operation PC; active spans intersect this region",
                                      "nested_or_overlapping_regions_are_additive": False}})
    return output
