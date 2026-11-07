"""Join guest mcycle regions to completed CSR events, with explicit counter proof."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct

from .report import profile, symbols
from .counter_regions import annotate as annotate_captured
from .event_lifecycle import completed, validate_events

SOURCE_COUNTER_POLICY = "source-ungated-stall"
SOURCE_GRAPH_POLICY = "CSRFile-ungated-pre-edge-stall"


class SourceCounterClocks(dict):
    """Clock ratios with an explicit prohibition on linear counter projection."""
    counter_policy = SOURCE_COUNTER_POLICY


def source_counter_graph(graph):
    if "source_counter_clock" not in graph:
        return False
    value = graph["source_counter_clock"]
    if (not isinstance(value, dict) or value.get("policy") != SOURCE_GRAPH_POLICY
            or not isinstance(value.get("source"), str) or not value["source"].strip()):
        raise ValueError("unknown or unproven source counter clock policy")
    return True


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Instructions:
    def __init__(self, elf, rom=None):
        data=Path(elf).read_bytes()
        if len(data)<64 or data[:7]!=b"\x7fELF\x02\x01\x01" or struct.unpack_from("<H",data,18)[0]!=243:
            raise ValueError("RV64 little-endian RISC-V ELF required")
        offset=struct.unpack_from("<Q",data,32)[0]
        size,count=struct.unpack_from("<HH",data,54)
        if size<56 or offset+size*count>len(data): raise ValueError("invalid ELF program headers")
        self.ranges=[]
        for index in range(count):
            kind,flags,file_offset,address,_,file_size,_,_=struct.unpack_from("<IIQQQQQQ",data,offset+index*size)
            if kind==1 and flags&1 and file_size:
                if file_offset+file_size>len(data): raise ValueError("truncated executable ELF segment")
                self.ranges.append((address,data[file_offset:file_offset+file_size]))
        if rom is not None: self.ranges.append((0x10000,Path(rom).read_bytes()))

    def read(self, pc):
        for base,data in self.ranges:
            offset=pc-base
            if 0<=offset and offset+2<=len(data):
                low=int.from_bytes(data[offset:offset+2],"little")
                if low&3!=3: return low
                if offset+4>len(data): raise ValueError("truncated instruction")
                return int.from_bytes(data[offset:offset+4],"little")
        raise ValueError("executed PC missing from executable ELF/ROM: "+hex(pc))


def fixture_clocks(text, graph, constant_counter_proof=True):
    clocks={};base=None;purpose=None;counter_policy=None;idle={}
    for line in text.splitlines():
        fields=line.split("#",1)[0].split()
        if not fields: continue
        if fields[0]=="purpose": purpose=fields[1]
        if fields[0]=="base_hz": base=int(fields[1],0)
        if fields[0]=="counter_policy":
            if len(fields)!=2 or counter_policy is not None or fields[1]!=SOURCE_COUNTER_POLICY:
                raise ValueError("unknown or duplicate counter policy declaration")
            counter_policy=fields[1]
        if fields[0]=="hart":
            if len(fields)!=4: raise ValueError("invalid hart clock declaration")
            hart,divider=(int(value,0) for value in fields[1:3])
            wfi="source" if fields[3]=="source" else int(fields[3],0)
            if hart in clocks or divider<=0 or wfi not in (0,1,"source"):
                raise ValueError("invalid or duplicate hart clock policy")
            idle[hart]=wfi
            if constant_counter_proof and wfi!=1:
                raise ValueError("region projection requires unique clocks counting WFI on every hart")
            clocks[hart]=divider
    if purpose!="uncalibrated-integration-fixture" or base!=graph.get("base_clock_hz") or set(clocks)!=set(range(graph["harts"])):
        raise ValueError("fixture clock proof disagrees with graph")
    if [clocks[h] for h in range(graph["harts"])]!=graph.get("hart_cycle_dividers"):
        raise ValueError("fixture per-hart divider mismatch")
    source_graph=source_counter_graph(graph)
    if counter_policy==SOURCE_COUNTER_POLICY:
        capture=graph.get("counter_read_observations")
        if (set(idle.values())!={"source"} or constant_counter_proof or not source_graph
                or not isinstance(capture,dict) or capture.get("complete") is not True):
            raise ValueError("source counter policy requires source hart declarations and complete captured CSR reads; no constant clock mapping")
        return SourceCounterClocks(clocks)
    if "source" in idle.values() or source_graph:
        raise ValueError("fixture source counter policy disagrees with graph or is undeclared")
    return clocks


def verify_run(receipt, console):
    """Admit independently checked scalar or mixed OS runs, never a timing claim."""
    statuses={"passed_scalar_zephyr_smp_live_fixture", "passed_mixed_zephyr_smp_live_fixture"}
    if (receipt.get("status") not in statuses or receipt.get("exit_status")!=0 or
        receipt.get("timed_out") is not False or receipt.get("errors")!=[] or
        receipt.get("checksums_independent") is not True or receipt.get("physical_boot") is not True):
        raise ValueError("successful independently checked physical Zephyr live-fixture receipt required")
    rows=[json.loads(line.split(" ",1)[1]) for line in console.splitlines()
          if line.startswith("IRIS_REGION ")]
    if not rows or rows!=receipt.get("regions"):
        raise ValueError("console regions disagree with independently checked run receipt")
    if receipt["status"]=="passed_mixed_zephyr_smp_live_fixture":
        if receipt.get("harts")!=[0,1,2] or receipt.get("vme_ownership_abi")!="iris_vme_enable before measured VME region on compute harts only":
            raise ValueError("mixed run requires explicit three-hart VME ownership contract")
        vme=[row for row in rows if row.get("role")=="vme.compute"]
        if {row.get("hart") for row in vme}!={0,1}:
            raise ValueError("mixed run requires VME on both compute harts only")
        if not any(row.get("hart")==2 and row.get("role")=="vector.copy" for row in rows):
            raise ValueError("mixed run requires hart-2 vector work")
    return rows


def annotate(graph, console, instructions, clocks):
    validate_events(graph)
    if (getattr(clocks,"counter_policy",None)==SOURCE_COUNTER_POLICY or source_counter_graph(graph)) and "counter_read_observations" not in graph:
        raise ValueError("source counter regions require captured CSR reads; linear fallback is forbidden")
    if "counter_read_observations" in graph:
        return annotate_captured(graph,console,instructions)
    if graph.get("timing_mode")!="uncalibrated-integration-fixture":
        raise ValueError("counter proof currently supports the conservative live fixture only")
    reads={};cache={}
    for event in graph["events"]:
        if not completed(event) or event.get('squashed',False) or event["name"]=="trap-entry": continue
        pc=event["pc"]
        if pc not in cache: cache[pc]=instructions.read(pc)
        raw=cache[pc]
        if raw&127!=0x73: continue
        f3=(raw>>12)&7;csr=raw>>20;source=(raw>>15)&31;rd=(raw>>7)&31
        if f3 not in (1,2,3,5,6,7): continue
        writes=f3 in (1,5) or source!=0
        if writes and csr in (0xb00,0xb80,0x321):
            raise ValueError("mcycle write/configuration invalidates constant clock mapping")
        if writes and csr==0x320 and not (f3 in (1,5) and source==0):
            raise ValueError("mcountinhibit write invalidates constant clock mapping")
        if csr in (0xb00,0xc00) and rd and not writes:
            hart=event["hart"];value=event["end"]//clocks[hart]
            reads.setdefault((hart,value),[]).append(event)
    boundaries=[];seen=set()
    for line in console.splitlines():
        if not line.startswith("IRIS_REGION "): continue
        row=json.loads(line.split(" ",1)[1]);hart=row["hart"]
        if type(hart) is not int or hart not in clocks: raise ValueError("invalid region hart")
        name=row["case_id"]+"::"+row["region"];key=(hart,name)
        if key in seen: raise ValueError("duplicate guest region identity")
        seen.add(key)
        nodes=[]
        for field in ("start_cycle","end_cycle"):
            value=row[field]
            if type(value) is not int or value<0: raise ValueError("invalid guest counter value")
            matches=reads.get((hart,value),[])
            if len(matches)!=1: raise ValueError("missing or ambiguous completed mcycle boundary for "+repr((key,field,value)))
            nodes.append(matches[0])
        start,end=nodes[0]["end"],nodes[1]["end"]
        if end<=start: raise ValueError("reversed guest region")
        boundaries.append({"hart":hart,"region":name,"start":start,"end":end,
            "guest_start_cycle":row["start_cycle"],"guest_end_cycle":row["end_cycle"],
            "guest_mcycle_delta":row["end_cycle"]-row["start_cycle"],
            "start_node":nodes[0]["id"],"end_node":nodes[1]["id"],
            "source":"guest mcycle values matched to completed CSR events; constant-counter proof"})
    if not boundaries: raise ValueError("guest region records missing")
    for hart in clocks:
        selected=sorted((r for r in boundaries if r["hart"]==hart),key=lambda r:r["start"])
        if any(a["end"]>b["start"] for a,b in zip(selected,selected[1:])):
            raise ValueError("nested/overlapping guest regions need explicit nesting support")
    annotated=copy.deepcopy(graph)
    for event in annotated["events"]:
        for region in boundaries:
            if event["hart"]!=region["hart"] or event["end"]<=region["start"] or event["start"]>=region["end"]: continue
            if event["start"]<region["start"] or event["end"]>region["end"]:
                raise ValueError("in-flight event crosses guest region boundary")
            event["region"]=region["region"]
    annotated["region_boundaries"]=boundaries
    return annotated


def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument("graph",type=Path);p.add_argument("--elf",type=Path,required=True)
    p.add_argument("--console",type=Path,required=True);p.add_argument("--fixture",type=Path,required=True)
    p.add_argument("--rom",type=Path);p.add_argument("--run-receipt",type=Path,required=True)
    p.add_argument("--nm",default="nm");p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    try:
        receipt=json.loads(args.run_receipt.read_text());live=receipt["live_fixture"]
        console=args.console.read_text()
        verify_run(receipt,console)
        for path,expected in [(args.graph,live["graph_sha256"]),(args.fixture,live["fixture_sha256"]),
                              (args.elf,receipt["elf_sha256"]),(args.console,receipt["stdout_sha256"])]:
            if sha(path)!=expected: raise ValueError("region evidence digest mismatch: "+str(path))
        if args.rom and sha(args.rom)!=receipt["rom_sha256"]: raise ValueError("ROM evidence mismatch")
        graph=json.loads(args.graph.read_text());clocks=fixture_clocks(args.fixture.read_text(),graph,
            constant_counter_proof="counter_read_observations" not in graph)
        annotated=annotate(graph,console,Instructions(args.elf,args.rom),clocks)
        summary,timeline=profile(annotated,graph["base_clock_hz"],graph["clock_source"],symbols(args.elf,args.nm))
        summary["region_projection"]={"certified":False,"boundaries":annotated["region_boundaries"],
            "method":annotated.get("region_projection_method","constant-counter proof"),
            "graph_sha256":sha(args.graph),"elf_sha256":sha(args.elf),"console_sha256":sha(args.console),
            "fixture_sha256":sha(args.fixture),"run_receipt_sha256":sha(args.run_receipt),
            "run_status":receipt["status"],"vme_context_certified":False,
            "tool_source_sha256":{Path(__file__).name:sha(Path(__file__)),
                "report.py":sha(Path(__file__).with_name("report.py")),
                "memory_profile.py":sha(Path(__file__).with_name("memory_profile.py")),
                "counter_regions.py":sha(Path(__file__).with_name("counter_regions.py")),
                "event_lifecycle.py":sha(Path(__file__).with_name("event_lifecycle.py"))}}
        if getattr(clocks,"counter_policy",None)==SOURCE_COUNTER_POLICY:
            summary["region_projection"].update(counter_policy=SOURCE_COUNTER_POLICY,
                counter_values_projected_from_global_time=False,
                source_counter_clock={key:value for key,value in graph["source_counter_clock"].items() if key!="transitions"})
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
        (args.output/"timeline.json").write_text(json.dumps(timeline)+"\n")
        print(json.dumps({"status":"profiled_actual_guest_regions_uncalibrated","regions":len(annotated["region_boundaries"]),"output":str(args.output)}))
    except (KeyError,TypeError,ValueError,OSError) as error:
        p.exit(2,"iris-guest-regions: "+str(error)+"\n")


if __name__=="__main__": main()
