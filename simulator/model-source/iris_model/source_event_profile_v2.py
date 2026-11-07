"""Checked COM -> complete source-write accounting for VADD, FADD and OPU.

This is a consumer of model journals. It does not infer physical FPGA activity,
memory traffic, missing publications, unobserved work, or counter-to-clock maps.
"""
import argparse
import bisect
import collections
import hashlib
import json
import re
from pathlib import Path

from .guest_regions import Instructions
from .report import symbols
from .source_event_profile_v1 import region_bounds, merged

ROOT = Path(__file__).resolve().parents[1]
CAPACITY_SHA = "0b9ddd00e1763067f7540ffaa958ba0b0c8db648309fad8005c5511815bb6a91"


def need(value, message):
    if not value:
        raise ValueError(message)


def integer(value, name, positive=False):
    need(type(value) is int and value >= int(positive), name + ": typed nonnegative integer required")
    return value


def flag(value, name, wire=False):
    need(type(value) is bool or (wire and type(value) is int and value in (0, 1)),
         name + ": boolean required")
    return bool(value)


def load(path):
    def refuse(value):
        raise ValueError("Non-finite JSON: " + value)
    return json.loads(Path(path).read_text(), parse_constant=refuse)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clocks(summary=None, fixture=None):
    """Clock units come from a bound summary/fixture, never mcycle arithmetic."""
    if summary is not None:
        data = load(summary)
        hz = integer(data["base_clock_hz"], "base_clock_hz", True)
        dividers = data["hart_cycle_dividers"]
        end = integer(data["cycles"], "summary cycles")
    else:
        need(fixture is not None, "A source summary or explicit fixture is required")
        hz, by_hart = None, {}
        for line in Path(fixture).read_text().splitlines():
            fields = line.split("#", 1)[0].split()
            if not fields:
                continue
            if fields[0] == "base_hz":
                need(hz is None and len(fields) == 2, "Unique fixture base_hz required")
                hz = integer(int(fields[1], 0), "base_hz", True)
            elif fields[0] == "hart":
                need(len(fields) == 4, "Explicit fixture hart clock required")
                hart, divider = int(fields[1], 0), int(fields[2], 0)
                need(hart not in by_hart, "Duplicate fixture hart clock")
                by_hart[hart] = integer(divider, "divider", True)
        need(set(by_hart) == {0, 1, 2} and hz is not None, "Three actual hart clock declarations required")
        dividers, end = [by_hart[h] for h in range(3)], None
    need(isinstance(dividers, list) and len(dividers) == 3 and
         all(type(d) is int and d > 0 for d in dividers), "Three positive hart dividers required")
    if summary is not None and fixture is not None:
        fhz, fdividers, _ = clocks(None, fixture)
        need((hz, dividers) == (fhz, fdividers), "Summary and fixture clock contracts disagree")
    return hz, dividers, end


def bound_execution(receipt, events, features, console, elf, fixture=None):
    """Both normal execution and deliberate measured-only readers bind bytes."""
    record = load(receipt)
    need(record.get("pass") is True, "Successful byte-bound execution receipt required")
    inputs, outputs = record.get("input_sha256", {}), record.get("output_sha256", {})
    need(isinstance(inputs, dict) and isinstance(outputs, dict), "Execution closure maps required")
    for path in (events, features, console):
        path = Path(path).resolve()
        need(outputs.get(str(path)) == sha(path), "Execution output unbound/changed: " + str(path))
    for path in (elf, fixture):
        if path is not None:
            path = Path(path).resolve()
            need(inputs.get(str(path)) == sha(path), "Execution input unbound/changed: " + str(path))
    if record.get("normal_guest_exit_verified") is False:
        need(record.get("intentional_postmeasurement_debugger_stop") is True,
             "Incomplete execution needs an explicit measured-only scope")
    return record


def capacity_records(path):
    need(sha(path) == CAPACITY_SHA, "Exact independently bound source capacity report required")
    data = load(path)
    need(data.get("pass") is True, "Passed source capacity report required")
    result = {}
    for row in data["capacity_records"]:
        if row["family"] in ("rvv.integer.vadd", "rvv.fp.fadd", "vme.int8", "vme.fp8"):
            result[row["hart"], row["family"], row.get("sew")] = row
    return result


def conditional_fraction(work, span, divider, capacity):
    integer(work, "completed work")
    integer(span, "source window span", True)
    integer(divider, "core divider", True)
    integer(capacity, "source ceiling", True)
    rate = work * divider / span
    ratio = rate / capacity
    return dict(model_completed_work_per_core_edge=rate,
                fraction_of_conditional_source_ceiling=ratio,
                inconsistency_fraction_above_one=ratio > 1 + 1e-12)


def validate_prefixes(path, code):
    last, count = {}, 0
    for line in Path(path).open():
        p = json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite CSR prefix")))
        need(p.get("schema") == "iris.empirical.prefix.v1", "Actual CSR prefix schema required")
        h, seq, value, pc, raw = [integer(p[name], "CSR " + name) for name in
                                   ("hart", "sequence", "value", "pc", "raw")]
        need(h < 3 and seq > last.get(h, -1) and value < 1 << 64 and pc < 1 << 64 and
             raw < 1 << 32 and type(p["destination"]) is int and 0 <= p["destination"] < 32,
             "Typed, ordered actual XLEN CSR fields required")
        integer(p["read_cycle"], "CSR global observation edge")
        need(type(p["result_discarded"]) is bool and p["result_discarded"] == (p["destination"] == 0),
             "Actual CSR returned/discarded result declaration required")
        need(code.read(pc) == raw and raw & 127 == 0x73 and raw >> 20 in (0xb00, 0xc00) and
             p["csr"] == raw >> 20 and p["destination"] == (raw >> 7) & 31,
             "Actual ELF-qualified MCYCLE CSR prefix required")
        last[h] = seq
        count += 1
    return count


def parse_journal(path, code, dividers):
    """Strict owner joins. Missing/duplicate/canceled source work is refused."""
    owners, front, canceled = {}, {}, set()
    reserved, kmicro, kwrites, callbacks, projections = {}, {}, {}, {}, {}
    counts, unknown = collections.Counter(), collections.Counter()
    controller = collections.defaultdict(list)
    reserved_nodes = set()
    max_cycle, tail = 0, b""
    last_queue_edge = {}

    def owner_key(e, unit):
        h = integer(e["hart"], "hart")
        need(h < 2, "Compute source owner must be on hart0/1")
        ident = integer(e["instruction" if unit == "OPU" else "generation"], "owner", unit != "OPU")
        return unit, h, ident

    def unique(table, key, event, name):
        need(key not in table, "Duplicate " + name)
        table[key] = event

    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                tail = line
                break
            e = json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite journal")))
            need(isinstance(e, dict) and isinstance(e.get("event"), str), "Typed source journal event required")
            kind = e["event"]
            counts[kind] += 1
            for field in ("cycle", "read_cycle", "RRD", "rrd", "COM", "com", "available", "write", "seq"):
                if field in e:
                    value = integer(e[field], field)
                    # Planned COM/availability/write deadlines are not
                    # observations that the model clock reached that edge.
                    if field in ("cycle", "read_cycle", "RRD", "rrd", "seq"):
                        max_cycle = max(max_cycle, value)
            unit = "VADD" if kind.startswith("vadd_EG_") else "FADD" if kind.startswith("fadd_EG_") else None
            prefix = unit.lower()+"_EG_" if unit else ""
            if unit and kind in {prefix+suffix for suffix in ("front_reservation", "functional_COM", "wrongpath_cancel", "microissue", "physical_write")}:
                key = owner_key(e, unit)
                if kind.endswith("front_reservation"):
                    unique(front, key, e, unit + " front reservation")
                elif kind.endswith("functional_COM"):
                    unique(owners, key, dict(COM=e, issues={}, writes={}), unit + " actual COM")
                elif kind.endswith("wrongpath_cancel"):
                    need(key not in canceled, "Duplicate source cancellation")
                    need(key in front and e["cycle"] <= front[key]["COM"], "Cancellation must precede successful COM")
                    canceled.add(key)
                else:
                    need(key in owners, "Source issue/write without actual COM")
                    index = integer(e["index"], "EG index")
                    bucket = "issues" if kind.endswith("microissue") else "writes"
                    unique(owners[key][bucket], index, e, unit + " EG " + bucket)
            elif unit and kind == prefix+"counter_read":
                need(flag(e["architecturally_retired"], "counter retired"), "Unretired counter event")
                # Counter metadata can legitimately appear on hart2.
                need(integer(e["hart"], "counter hart") < 3, "Threehart counter metadata")
            elif unit and kind == prefix+"fence_admit":
                h = integer(e["hart"], "fence hart")
                need(h < 2 and integer(e["pending"], "pending source owners") == 0 and flag(e["controller_empty"], "controller empty"),
                     "FENCE admitted pending source work")
                need(not flag(e["last_preedge_busy"], "last preedge busy") or
                     e["cycle"] > integer(e["last_controller_edge"], "last source edge"),
                     "FENCE bypassed final preedge busy")
            elif kind == "opu_front_accept":
                unique(front, owner_key(e, "OPU"), e, "OPU front")
            elif kind == "opu_front_cancel":
                key = owner_key(e, "OPU")
                need(key not in canceled, "Duplicate OPU cancellation")
                need(key in front and e["cycle"] <= front[key]["com"], "OPU cancellation must precede successful COM")
                canceled.add(key)
            elif kind == "opu_functional_COM":
                unique(owners, owner_key(e, "OPU"), dict(COM=e, issues={}, writes={}), "OPU actual COM")
            elif kind == "opu_K_microissue":
                key = owner_key(e, "OPU")
                unique(kmicro, (*key, integer(e["entry"], "entry"), integer(e["k"], "K")), e, "OPU K microissue")
            elif kind == "opu_microissue_reserved":
                key = owner_key(e, "OPU")
                node = integer(e["backend_node"], "OPU callback node", True)
                need(node not in reserved_nodes, "Duplicate OPU callback reservation node")
                reserved_nodes.add(node)
                unique(reserved, (*key, integer(e["entry"], "entry"), integer(e["seq"], "seq")), e, "OPU reservation")
            elif kind == "opu_K_write_owner":
                key = owner_key(e, "OPU")
                unique(kwrites, (*key, integer(e["entry"], "entry"), integer(e["k"], "K")), e, "OPU guarded write")
            elif kind in ("opu_K_completion", "opu_entry_publish"):
                key = owner_key(e, "OPU")
                table = callbacks if kind == "opu_K_completion" else projections
                unique(table, (*key, integer(e["cycle"], "callback cycle")), e, "OPU callback/projection")
            elif kind == "opu_queue_edge":
                h, t = integer(e["hart"], "OPU queue hart"), integer(e["cycle"], "queue edge")
                need(h < 2 and type(e["vdq_before"]) is int and 0 <= e["vdq_before"] <= 4 and
                     type(e["iq_before"]) is int and 0 <= e["iq_before"] <= 3,
                     "Source OPU VDQ4/IQ3 occupancy")
                need(t > last_queue_edge.get(h, -1) and t % dividers[h] == 0,
                     "Repeated/backwards/unaligned OPU source edge")
                last_queue_edge[h] = t
                busy = bool(e["vdq_before"] or e["iq_before"] or flag(e["seq_before"], "SEQ busy", True))
                if busy:
                    controller[h].append((t, t + dividers[h]))
            else:
                unknown[kind] += 1

    need(not (set(owners) & canceled), "Canceled/wrongpath owner received COM/work credit")
    for key in canceled:
        need(key in front, "Source cancellation has no front owner")
    # A canceled command must have no reserved backend events either.
    need(not any(k[:3] in canceled for table in (reserved, kmicro, kwrites) for k in table),
         "Canceled OPU owner has backend work")
    need(set(owners) <= set(front), "Actual COM missing front owner")
    need(set(front) == set(owners) | canceled,
         "Missing actual COM or cancellation for a front reservation")
    writes_at_edge = set()
    result = []
    micro_keys = collections.defaultdict(set)
    for key in kmicro:
        micro_keys[key[:3]].add(key[3:])
    for key, record in owners.items():
        unit, h, ident = key
        c, f, d = record["COM"], front[key], dividers[h]
        pc, raw, com = [integer(c[n], n) for n in ("pc", "raw", "cycle")]
        need(code.read(pc) == raw and c.get("geometry_authority") == "checked_architectural_COM",
             "Actual ELF instruction/checked COM authority mismatch")
        need(pc == f["pc"] and com == f["com" if unit == "OPU" else "COM"],
             "Front -> actual COM identity/phase mismatch")
        row = dict(unit=unit, hart=h, owner=ident, pc=pc, COM=com, RRD=f["rrd" if unit == "OPU" else "RRD"],
                   raw=raw, publications=[], source_pipe_intervals=[])
        need(com == row["RRD"] + 3*d and
             f["backend_available" if unit == "OPU" else "available"] == com+d,
             "Finite CPU COM+1 bridge event contract mismatch")
        if unit != "OPU":
            sew, lm, den, vl = [integer(c[n], n, True) for n in ("SEW", "LMUL_numerator", "LMUL_denominator", "VL")]
            need(sew in ((8, 16, 32, 64) if unit == "VADD" else (16, 32, 64)) and
                 lm in (1, 2, 4, 8) and den == 1 and vl == 256 * lm // sew,
                 "FullVL source add geometry mismatch")
            expected_raw = 0x02000057 if unit == "VADD" else 0x02001057
            need(raw == expected_raw | (c["vs2"] << 20) | (c["vs1"] << 15) | (c["vd"] << 7),
                 "Actual source add opcode/operand mismatch")
            need(flag(c["physical_VRF_restored"], "physical VRF restored"), "Private COM oracle did not restore VRF")
            if unit == "FADD":
                need(type(c.get("new_flags")) is int and c["new_flags"] == 0, "Finite FADD flags0 scope")
            for reg in (c["vs1"], c["vs2"], c["vd"]):
                need(type(reg) is int and reg % lm == 0 and 0 <= reg <= 32 - lm, "Aligned finite register group")
            expected = set(range(2 * lm))
            need(set(record["issues"]) == set(record["writes"]) == expected,
                 "Missing/extra actual source EG issue/publication")
            depth = 1 if unit == "VADD" else 4
            for index in sorted(expected):
                issue, write = record["issues"][index], record["writes"][index]
                t, end = integer(issue["cycle"], "EG issue"), integer(write["cycle"], "EG publication")
                need(issue["pc"] == write["pc"] == pc and issue["destination_EG"] == 2 * c["vd"] + index and
                     t >= com + 4 * d and end == t + depth * d and type(write["bytes"]) is int and write["bytes"] == 16,
                     "Source EG timing, destination or publication bytes mismatch")
                edge = unit, h, end
                need(edge not in writes_at_edge, "Source unit write-port overbooking")
                writes_at_edge.add(edge)
                row["publications"].append((end, 128 // sew, index, 16, False))
                row["source_pipe_intervals"].append((t + d, end + d))
            row.update(family="rvv.integer.vadd" if unit == "VADD" else "rvv.fp.fadd",
                       SEW=sew, retired_work=vl, work_units="element_adds", internal_VRF_bytes=32 * lm)
        else:
            need(c.get("kind") == "matrix_multiply" and c.get("family") in ("vme.int8", "vme.fp8"),
                 "Authoritative OPU MM family required")
            tm, tn, tk, tile = [integer(c[n], n) for n in ("TM", "TN", "TK", "tile")]
            for name in ("SEW", "widen", "vs1", "vs2", "destination_encoding", "tile_accessor_td",
                         "A_operand_register", "B_operand_register", "controller_TM", "controller_TN", "controller_TK"):
                integer(c[name], "OPU COM " + name)
            need(0 < tm <= 32 and 0 < tn <= 32 and tk in (1, 2, 4) and tile < 4 and
                 (tm, tn, tk) == (c["controller_TM"], c["controller_TN"], c["controller_TK"]),
                 "Actual OPU/controller shape mismatch")
            need(raw & 127 == 0x77 and c["destination_encoding"] == (raw >> 7) & 31 and
                 c["vs1"] == (raw >> 15) & 31 and c["vs2"] == (raw >> 20) & 31 and
                 c["A_operand_register"] == c["vs2"] and c["B_operand_register"] == c["vs1"],
                 "Actual OPU operand/destination encoding mismatch")
            need(c["SEW"] == 8 and c["widen"] == 4 and
                 tile == (c["destination_encoding"] >> 3) & 3 and
                 c["tile_accessor_td"] == (c["destination_encoding"] >> 1) & 12,
                 "Source OPU element/tile geometry mismatch")
            alternate = flag(c["altfmt"], "OPU alternate format")
            opcode = raw & 0xfc0070ff
            if c["family"] == "vme.fp8":
                need(opcode in (0xf0001077, 0xf00010f7) and
                     c["format_A"] == ("E5M2" if c["destination_encoding"] & 1 else "E4M3") and
                     c["format_B"] == ("E5M2" if alternate else "E4M3"),
                     "Actual FP8 opcode/format metadata mismatch")
                row["operand_formats"] = {"A": c["format_A"], "B": c["format_B"]}
            else:
                need(opcode in (0xf0000077, 0xf00000f7) and
                     flag(c["signed_A"], "signed A") == (opcode == 0xf00000f7) and
                     flag(c["signed_B"], "signed B") == alternate,
                     "Actual integer MM opcode/signedness mismatch")
                row["operand_formats"] = {"A": "int8" if c["signed_A"] else "uint8",
                                          "B": "int8" if c["signed_B"] else "uint8"}
            expected = {}
            for r in range((tm + 15) // 16):
                for col in range((tn + 15) // 16):
                    entry = tile * 4 + r * 2 + col
                    for k in range(tk):
                        expected[entry, k] = min(16, tm - 16 * r) * min(16, tn - 16 * col)
            got = micro_keys[key]
            need(got == set(expected), "Missing/extra OPU entry/K source microgroups")
            for (entry, k), work in expected.items():
                sk = (*key, entry, k)
                need(sk in kwrites, "Missing guarded OPU entry/K write")
                issue, write = kmicro[sk], kwrites[sk]
                t, end = issue["cycle"], write["cycle"]
                for event, names in ((issue, ("tk", "macs")), (write, ("cells", "macs", "pc"))):
                    for name in names:
                        integer(event[name], "OPU " + name)
                rk = (*key, entry, t)
                need(rk in reserved, "OPU K microissue lacks source reservation")
                reserve = reserved[rk]
                for name in ("pc", "macs", "write"):
                    integer(reserve[name], "reserved OPU " + name)
                last = k + 1 == tk
                need(reserve["pc"] == write["pc"] == pc and t >= com + d and
                     end == reserve["write"] and end >= t and issue["tk"] == tk and
                     flag(issue["last_k"], "source last K", True) == last and
                     flag(write["last_k"], "write last K") == last and
                     flag(write["projected_MRF_published"], "final MRF projection") == last and
                     issue["macs"] == reserve["macs"] == write["macs"] == write["cells"] == work,
                     "OPU K geometry, actual write, work or final-projection mismatch")
                callback_key = (*key, end)
                need(callback_key in callbacks, "Missing actual OPU K completion callback")
                callback = callbacks[callback_key]
                integer(callback["cells"], "callback cells")
                integer(callback["k"], "callback K")
                need(callback["cells"] == work and callback["k"] == k and
                     flag(callback["last_k"], "callback last K", True) == last,
                     "OPU K callback identity mismatch")
                if last:
                    if callback_key in projections:
                        integer(projections[callback_key]["cells"], "MRF projection cells")
                    need(callback_key in projections and projections[callback_key]["cells"] == work,
                         "Missing final per-entry MRF publication")
                else:
                    need(callback_key not in projections, "Intermediate K projected the final MRF")
                need(("OPU", h, end) not in writes_at_edge, "Source OPU write-port overbooking")
                writes_at_edge.add(("OPU", h, end))
                row["publications"].append((end, work, (entry, k), 0, last))
            need(sum(p[1] for p in row["publications"]) == tm * tn * tk, "OPU total logical MAC mismatch")
            row.update(family=c["family"], SEW=None, TM=tm, TN=tn, TK=tk, tile=tile,
                       retired_work=tm * tn * tk, work_units="logical_MACs", internal_VRF_bytes=0)
        row["last_write"] = max(p[0] for p in row["publications"])
        result.append(row)
    need(len(kwrites) == len(kmicro) == len(reserved) and
         all(k[:3] in owners for table in (reserved, kmicro, kwrites) for k in table),
         "Unowned/extra OPU source reservation or completion")
    need(len(callbacks) == len(kwrites), "Extra OPU completion callback")
    need(len(projections) == sum(int(p[4]) for row in result for p in row["publications"]),
         "Extra final MRF projection")
    # A complete newline-less source-owner event cannot be treated as absence.
    # Non-owner trailing diagnostics remain disclosed and uncredited.
    if tail:
        match = re.search(rb'"event"\s*:\s*"([^"\n]+)"', tail)
        allowed = {b"vadd_EG_counter_read", b"fadd_EG_counter_read",
                   b"vadd_EG_fence_admit", b"fadd_EG_fence_admit",
                   b"opu_queue_edge", b"opu_fence_admit"}
        need(match is not None and match[1] in allowed,
             "Unterminated authoritative source-owner/publication or unknown event")
    return dict(commands=result, counts=dict(counts), unknown=dict(unknown), canceled=len(canceled),
                controller=controller, max_cycle=max_cycle, tail=tail)


def profile(events, features, console, summary, elf, rom, output, nm,
            region_map=None, region_format="iris-json", execution_complete=True,
            fixture=None, execution_receipt=None,
            capacity_report=ROOT / "build/throughput-limits-source-v3/report.json"):
    output = Path(output).resolve()
    need(not output.exists(), "Fresh profile output directory required")
    input_paths = [Path(__file__).resolve(), Path(events), Path(features), Path(console), Path(elf), Path(rom), Path(nm), Path(capacity_report)]
    input_paths += [Path(__file__).with_name(name) for name in
                    ("source_event_profile_v1.py", "guest_regions.py", "report.py")]
    input_paths += [Path(p) for p in (summary, fixture, execution_receipt, region_map) if p is not None]
    identities = {str(p.resolve()): sha(p) for p in input_paths}
    need(execution_receipt is not None, "A byte-bound execution receipt is required")
    execution = bound_execution(execution_receipt, events, features, console, elf, fixture)
    normal = execution.get("normal_guest_exit_verified",
                           not execution.get("intentional_postmeasurement_debugger_stop", False))
    need(type(normal) is bool and type(execution_complete) is bool and execution_complete == normal,
         "Normal-completion claim disagrees with execution receipt")
    hz, dividers, known_end = clocks(summary, fixture)
    code = Instructions(elf, rom)
    prefix_count = validate_prefixes(features, code)
    parsed = parse_journal(events, code, dividers)
    need(known_end is None or parsed["max_cycle"] <= known_end,
         "Source event after bound native summary end")
    horizon = known_end if known_end is not None else parsed["max_cycle"]
    need(horizon > 0, "Positive observed model clock horizon required")
    capacities = capacity_records(capacity_report)
    syms = symbols(elf, nm)
    starts = [s[0] for s in syms]

    def function(pc):
        index = bisect.bisect_right(starts, pc) - 1
        return syms[index][2] if index >= 0 and pc < syms[index][1] else None

    commands = parsed["commands"]
    for row in commands:
        row["function"] = function(row["pc"])
    metrics, functions, pub = {}, {}, collections.defaultdict(list)
    commands_by_group = collections.defaultdict(list)
    command_index = {}
    pipe_spans, owner_spans = collections.defaultdict(list), collections.defaultdict(list)

    def new_metric(row):
        return dict(hart=row["hart"], unit=row["unit"], family=row["family"], SEW=row["SEW"],
                    work_units=row["work_units"], checked_COM_commands=0, retired_logical_work=0,
                    complete_publication_groups=0, actual_completed_logical_work=0,
                    actual_internal_VRF_write_bytes=0, final_MRF_entry_publications=0)

    def add(bucket, row):
        bucket["checked_COM_commands"] += 1
        bucket["retired_logical_work"] += row["retired_work"]
        bucket["complete_publication_groups"] += len(row["publications"])
        bucket["actual_completed_logical_work"] += sum(p[1] for p in row["publications"])
        bucket["actual_internal_VRF_write_bytes"] += row["internal_VRF_bytes"]
        bucket["final_MRF_entry_publications"] += sum(p[4] for p in row["publications"])

    for row in commands:
        group = row["hart"], row["family"], row["SEW"]
        commands_by_group[group].append(row)
        command_index[row["unit"], row["hart"], row["owner"]] = row
        add(metrics.setdefault(group, new_metric(row)), row)
        fkey = (*group, row["function"])
        agg = functions.setdefault(fkey, dict(**new_metric(row), function=row["function"],
                                             owner_envelope_begin=row["COM"], owner_envelope_end=0))
        add(agg, row)
        agg["owner_envelope_begin"] = min(agg["owner_envelope_begin"], row["COM"])
        agg["owner_envelope_end"] = max(agg["owner_envelope_end"], row["last_write"] + dividers[row["hart"]])
        for p in row["publications"]:
            pub[group].append((p[0], p[1], row["COM"], p[3], p[4], row["owner"]))
        if row["source_pipe_intervals"]:
            pipe_spans[row["unit"], row["hart"]].extend(row["source_pipe_intervals"])
        owner_spans[row["unit"], row["hart"]].append((row["RRD"], row["last_write"] + dividers[row["hart"]]))
    for items in pub.values():
        items.sort()
    for items in commands_by_group.values():
        items.sort(key=lambda r:r["COM"])
    com_edges = {key:[r["COM"] for r in rows] for key,rows in commands_by_group.items()}

    def fraction(bucket, span):
        cap = capacities.get((bucket["hart"], bucket["family"], bucket["SEW"]))
        need(cap is not None and type(cap["value"]) is int and cap["value"] > 0, "Exact family source ceiling required")
        return dict(**conditional_fraction(bucket["actual_completed_logical_work"], span,
                                           dividers[bucket["hart"]], cap["value"]),
                    conditional_source_capacity_per_core_edge=cap["value"],
                    conditional_source_capacity_units=cap["units"],
                    normalized_comparison_units=bucket["work_units"]+"/core_edge",
                    ceiling_comparison_basis="At most one accepted selected-format microgroup/core edge, subject to the source capacity conditions; no sum across shared/alternate formats",
                    source_ceiling_conditions=cap["conditions"], physical_utilization=False)

    for value in metrics.values():
        value.update(fraction(value, horizon))
    for value in functions.values():
        value.update(fraction(value, value["owner_envelope_end"] - value["owner_envelope_begin"]))
    regions, region_scope = region_bounds(Path(console).read_text(), features, code, region_map, region_format)
    region_rows = []
    for region in regions:
        lo, hi, h = region["global_begin"], region["global_end"], region["hart"]
        groups = []
        for key, values in pub.items():
            if key[0] != h:
                continue
            first = bisect.bisect_left(values, (lo,))
            end = bisect.bisect_left(values, (hi,))
            selected = values[first:end]
            a,b = bisect.bisect_left(com_edges[key],lo),bisect.bisect_left(com_edges[key],hi)
            started = commands_by_group[key][a:b]
            if not selected and not started:
                continue
            contributing = [command_index[metrics[key]["unit"],h,owner]
                            for owner in {p[5] for p in selected}]
            metric = dict(metrics[key])
            for name in ("checked_COM_commands", "retired_logical_work"):
                metric.pop(name)
            metric.update(complete_publication_groups=len(selected),
                          actual_completed_logical_work=sum(p[1] for p in selected),
                          actual_internal_VRF_write_bytes=sum(p[3] for p in selected),
                          final_MRF_entry_publications=sum(p[4] for p in selected),
                          completed_carry_in_groups=sum(p[2] < lo for p in selected),
                          checked_COM_commands_started_in_window=len(started),
                          retired_logical_work_started_in_window=sum(r["retired_work"]for r in started),
                          started_commands_with_final_write_after_window=sum(r["last_write"]>=hi for r in started),
                          contributing_owner_commands=len(contributing),
                          contributing_commands_started_before_window=sum(r["COM"]<lo for r in contributing),
                          all_contributing_commands_final_write_before_end=(all(r["last_write"]<hi for r in contributing) if contributing else None),
                          all_started_commands_final_write_before_end=(all(r["last_write"]<hi for r in started) if started else None),
                          all_started_commands_fully_published_inside_window=(bool(started) and all(r["last_write"]<hi for r in started)))
            metric.update(fraction(metric, hi - lo))
            groups.append(metric)
        region_rows.append(dict(**region, source_completed_work=groups,
                                attribution="Actual publication cycle in [global_begin,global_end); no mcycle projection",
                                physical_memory_bytes=None, physical_bandwidth=None))
    busy = dict(
        observed_OPU_controller_preedge_busy_union_global_cycles={str(h): sum(b-a for a,b in merged(v)) for h,v in parsed["controller"].items()},
        OPU_controller_scope="Actual sampled VDQ/IQ/SEQ preedge busy only; FP8 numeric tails are separate; no full backend utilization",
        completed_source_pipe_valid_union_global_cycles={f"{u}.hart{h}": sum(b-a for a,b in merged(v)) for (u,h),v in pipe_spans.items()},
        completed_source_pipe_interval="[issue+divider,write+divider); VADD1/FADD4 source valid samples",
        OPU_numeric_pipe_valid_union_global_cycles=None,
        OPU_numeric_pipe_valid_scope="No complete OPU EXU-valid journal; queue/SEQ samples and owned numeric completion callbacks do not identify full pipeline occupancy",
        bookkeeping_owner_age_union_global_cycles={f"{u}.hart{h}": sum(b-a for a,b in merged(v)) for (u,h),v in owner_spans.items()},
        bookkeeping_owner_age_scope="[front RRD,final write+divider); includes CPU/queue/tail age, not source-controller busy or physical utilization")
    result = dict(schema="iris.source_event_profile.v2", pass_=True,
                  source_clock_hz=hz, hart_cycle_dividers=dividers,
                  whole_program_model_cycles=known_end, observed_global_cycle_horizon=horizon,
                  normal_execution_complete=bool(execution_complete),
                  whole_program_scope="Captured model journal including boot/setup/unmarked work; debugger-only receipts do not prove normal completion",
                  journal_complete=not bool(parsed["tail"]), unterminated_tail_bytes=len(parsed["tail"]),
                  unterminated_tail_sha256=hashlib.sha256(parsed["tail"]).hexdigest(),
                  unterminated_tail_text=parsed["tail"].decode(errors="replace"),
                  all_present_authoritative_COM_to_publication_joins_complete=True,
                  actual_ELF_qualified_mcycle_prefixes=prefix_count,
                  OPU_intermediate_K_MRF_values_materialized=False,
                  OPU_MAC_completion_scope="Guarded modeled source entry/K callbacks after checked MM COM; final MRF projection only at last K. Intermediate numerical MRF values are not separately materialized by the finite actor.",
                  wrongpath_or_reserved_work_credited=False, canceled_front_reservations=parsed["canceled"],
                  whole_program=list(metrics.values()), per_function=list(functions.values()),
                  busy_and_age=busy, regions=region_rows, region_scope=region_scope,
                  event_counts=parsed["counts"], uncredited_or_unrecognized_event_counts=parsed["unknown"],
                  source_capacity_report_sha256=CAPACITY_SHA,
                  alternate_format_or_shared_peaks_summed=False,
                  fractions_above_one=[dict(hart=v["hart"], family=v["family"], SEW=v["SEW"], fraction=v["fraction_of_conditional_source_ceiling"])
                                       for v in metrics.values() if v["inconsistency_fraction_above_one"]],
                  physical_memory_traffic_bytes=None, physical_bandwidth=None, physical_roofline=None,
                  source_image_attested=False, timing_accuracy_certified=False, CPU_COMplus1_bridge_qualified=False,
                  input_sha256=identities,
                  unavailable_metrics=["physical FPGA switching/utilization", "DRAM/L2/TL traffic and bandwidth", "physical roofline", "CPU additive stall attribution", "generic TMOVE authoritative work", "active perEG context switching"],
                  logical_work_scope="Complete checked COM owners only; add elements and MACs are separate. A MAC is not two MACs; 2 arithmetic ops/MAC is an optional convention, never an extra work credit.")
    result["pass"] = result.pop("pass_")
    need(all(sha(Path(name)) == expected for name,expected in identities.items()), "Profiling input changed")
    output.mkdir()
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    with (output / "commands.jsonl").open("w") as stream:
        for row in commands:
            public = {k:v for k,v in row.items() if k not in ("publications", "source_pipe_intervals")}
            public.update(actual_completed_work=sum(p[1] for p in row["publications"]),
                          actual_publication_groups=len(row["publications"]),
                          numeric_tail_complete=True)
            stream.write(json.dumps(public, separators=(",", ":")) + "\n")
    # Command-level Perfetto keeps large full-program exports finite. Every
    # individual source write remains available in the hash-bound raw journal.
    with (output / "trace.json").open("w") as stream:
        stream.write('{"traceEvents":[')
        comma = False
        def emit(event):
            nonlocal comma
            if comma:
                stream.write(",")
            stream.write(json.dumps(event, separators=(",", ":")))
            comma = True
        us = lambda t: t * 1e6 / hz
        for row in commands:
            end = row["last_write"] + dividers[row["hart"]]
            emit(dict(ph="X", pid=row["hart"], tid=row["unit"] + "-owner-age", name=row["family"],
                      ts=us(row["COM"]), dur=us(end-row["COM"]),
                      args=dict(owner=row["owner"], pc=hex(row["pc"]), function=row["function"],
                                checked_COM=True, source_publication_groups=len(row["publications"]),
                                completed_work=sum(p[1] for p in row["publications"]), work_units=row["work_units"],
                                bookkeeping_age_not_busy=True, physical_utilization=False)))
        for (unit,h), spans in pipe_spans.items():
            for a,b in merged(spans):
                emit(dict(ph="X",pid=h,tid=unit+"-source-valid",name="completed source pipe-valid union",ts=us(a),dur=us(b-a),args=dict(physical_utilization=False)))
        for h,spans in parsed["controller"].items():
            for a,b in merged(spans):
                emit(dict(ph="X",pid=h,tid="OPU-source-controller",name="sampled preedge VDQ/IQ/SEQ busy",ts=us(a),dur=us(b-a),args=dict(numeric_tail_included=False,physical_utilization=False)))
        for region in region_rows:
            emit(dict(ph="X",pid=region["hart"],tid="explicit-CSR-regions",name=region["case_id"],ts=us(region["global_begin"]),dur=us(region["global_end"]-region["global_begin"]),args=region))
        stream.write('],"displayTimeUnit":"ns"}\n')
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
    record = load(args.execution_receipt)
    complete = bool(record.get("pass") and record.get("normal_guest_exit_verified", not record.get("intentional_postmeasurement_debugger_stop", False)))
    result = profile(args.events, args.features, args.console, args.summary, args.elf, args.rom, args.output, args.nm,
                     args.region_map, args.region_format, complete, args.fixture, args.execution_receipt)
    print(json.dumps({"pass": result["pass"], "regions": len(result["regions"]), "journal_complete": result["journal_complete"]}))


if __name__ == "__main__":
    main()
