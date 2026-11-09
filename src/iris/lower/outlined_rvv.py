"""Matched compiler-RVV leaves over Iris's controller-packed ownership blocks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def partition(m, n, tile_edge, worker_slots, *, job_budget):
    """Exact dyadic subviews of the SAME split-VME output ownership domains."""
    if any(type(v) is not int or v <= 0 for v in (m, n, tile_edge, job_budget)):
        raise ValueError("positive bounded partition parameters required")
    slots = tuple(sorted(worker_slots))
    if (
        not slots
        or len(set(slots)) != len(slots)
        or any(type(s) is not int or s not in (0, 1) for s in slots)
    ):
        raise ValueError("Iris compute slots0/1 required")

    def pieces(size):
        offset = 0
        while size:
            count = 1 << (size.bit_length() - 1)
            yield offset, count
            offset += count
            size -= count

    jobs = []
    block = 0
    edge = 2 * tile_edge
    for mi in range(0, m, edge):
        for ni in range(0, n, edge):
            start = len(jobs)
            for di, mm in pieces(min(edge, m - mi)):
                for dj, nn in pieces(min(edge, n - ni)):
                    jobs.append(
                        {
                            "slot": slots[block % len(slots)],
                            "row": mi + di,
                            "col": ni + dj,
                            "m": mm,
                            "n": nn,
                            "block": block,
                            "last": False,
                        }
                    )
                    if len(jobs) > job_budget:
                        raise ValueError("descriptor job budget exceeded")
            assert len(jobs) > start
            jobs[-1]["last"] = True
            block += 1
    return jobs


def prepare_staged_kernel(
    module,
    base,
    work,
    *,
    cc,
    controller_cflags,
    compute_cflags,
    runtime_include,
    prefix,
    descriptor_job_budget,
):
    """Reuse scalar H2 pack/finish; replace only compute with compiled RVV leaves."""
    from merlin.common.digest import sha256_file
    from merlin.llvmlower.deployable_ir import checked_generic_text
    from merlin.llvmlower.lower import lower_model
    from merlin.runtime.prepared_staged_kernel import PreparedStagedKernel
    from rvv import backend as vector_backend

    from .. import backend
    from .outlined_vme import admit_signed_gemm

    harts = tuple(base.provenance.get("worker_harts", ()))
    if harts not in ((0,), (1,), (0, 1)) or any(
        type(hart) is not int for hart in harts
    ):
        raise ValueError("base must bind the actual Iris compute-hart roster")
    worker_count = len(harts)
    base.verify(worker_count=worker_count)
    readonly = getattr(base, "readonly_inputs", ())
    if readonly and (len(readonly) != 1 or readonly[0].index != 1):
        raise ValueError("only the sealed common-layout B lease is supported")
    slot_mask = sum(1 << slot for slot in base.worker_slots)
    physical_mask = sum(1 << harts[slot] for slot in base.worker_slots)
    if (
        base.provenance.get("worker_count") != worker_count
        or base.provenance.get("selected_worker_slot_mask") != slot_mask
        or base.provenance.get("selected_physical_hart_mask") != physical_mask
        or base.provenance.get("layout", {}).get("worker_mask") != physical_mask
    ):
        raise ValueError("base logical slots and physical compute mask differ")
    source = checked_generic_text(module)
    if hashlib.sha256(source.encode()).hexdigest() != base.source_ir_sha256:
        raise ValueError("RVV comparator source differs from prepared VME source")
    if (
        base.provenance.get("schema") != "iris.outlined_split_vme.v1"
        or base.provenance.get("controller_packing") != "scalar_common_ISA"
    ):
        raise ValueError("explicit scalar-controller split-VME base required")
    if not prefix.isascii() or not prefix.isidentifier():
        raise ValueError("C-safe prefix required")
    m, n, k = admit_signed_gemm(module)
    unit = base.provenance["unit"]
    config = base.provenance["config"]
    contract = backend.load_contract(unit)
    vlen = int(contract.vector_params(config)["vLen"])
    edge, _alignment = backend.geometry(unit=unit, config=config)
    jobs = partition(m, n, edge, base.worker_slots, job_budget=descriptor_job_budget)
    api = vector_backend.borrowed_i8_leaf_api()
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    commands = []

    def command(argv, label):
        p = subprocess.run(
            list(map(str, argv)), capture_output=True, check=False, timeout=120
        )
        (work / (label + ".stdout")).write_bytes(p.stdout)
        (work / (label + ".stderr")).write_bytes(p.stderr)
        commands.append({"argv": list(map(str, argv)), "returncode": p.returncode})
        p.check_returncode()

    leaves = {}
    objects = []
    for job in jobs:
        key = (job["m"], job["n"])
        if key in leaves:
            continue
        name = prefix + "_leaf_" + str(key[0]) + "_" + str(key[1])
        d = work / name
        d.mkdir()
        emission = api.emit(
            name,
            api.LeafSpec(key[0], key[1], k, m, n, n, vlen),
            allow_scalar_1x1=key == (1, 1),
        )
        (d / "source.mlir").write_text(emission.source)
        lowering_kwargs = emission.lowering_kwargs()
        lowering_kwargs["features"] = frozenset(lowering_kwargs["features"]) | {
            "erase_self_copy"
        }
        result = lower_model(
            emission.source,
            d / "lower",
            targets=(),
            textual=True,
            codegen_target="rvv",
            **lowering_kwargs,
        )
        llvm = Path(result.ll_path).read_text()
        if any(
            token in llvm
            for token in (
                "@malloc",
                "@calloc",
                "@realloc",
                "@aligned_alloc",
                "@free",
                "@memrefCopy",
                "@memcpy",
                "@memmove",
                "@llvm.memcpy",
                "@llvm.memmove",
                "@llvm.memset",
                " alloca ",
                "__kmpc_",
            )
        ):
            raise ValueError("RVV compute leaf allocates, copies or creates a team")
        line = [
            line
            for line in llvm.splitlines()
            if line.startswith("define void @" + name + "(")
        ]
        if (
            len(line) != 1
            or [
                a.strip().split()[0]
                for a in line[0].split("(", 1)[1].split(")", 1)[0].split(",")
            ]
            != ["ptr", "ptr", "i64", "i64", "i64", "i64", "i64"] * 3
        ):
            raise ValueError(
                "RVV leaf actual ranked ABI differs from descriptor contract"
            )
        obj = d / "leaf.o"
        command([cc, *compute_cflags, "-c", result.ll_path, "-o", obj], name)
        objects.append(obj)
        leaves[key] = {
            **emission.record(),
            "object": str(obj),
            "object_sha256": sha256_file(obj),
            "LLVM": str(result.ll_path),
            "LLVM_sha256": sha256_file(result.ll_path),
        }
    counts = [sum(j["slot"] == s for j in jobs) for s in range(worker_count)]
    max_jobs = max(counts)
    state = prefix + "_state"
    header = work / (prefix + ".h")
    header.write_text(f"""#ifndef {prefix.upper()}_H
#define {prefix.upper()}_H
#include {json.dumps(str(base.header))}
typedef void (*{prefix}_leaf_fn)(void*,void*,void*);
typedef struct {{{prefix}_leaf_fn function;merlin_descriptor_t arguments[3];unsigned last;}} {prefix}_job;
typedef struct {{{base.state_type} base;unsigned counts[{worker_count}],calls[{worker_count}];{prefix}_job jobs[{worker_count}][{max_jobs}];}} {state};
int {prefix}_prepare(void*,const merlin_staged_kernel_invocation*);
int {prefix}_compute(void*,const merlin_staged_kernel_invocation*,unsigned);
int {prefix}_finish(void*,const merlin_staged_kernel_invocation*);
#endif
""")
    declarations = "\n".join(
        "extern void " + leaf["c_interface_symbol"] + "(void*,void*,void*);"
        for leaf in leaves.values()
    )
    preparation = []
    bp_pointer = "(void*)s->base.work.bp" if readonly else "s->base.bp"
    bp_capacity = str(k * n) if readonly else "sizeof s->base.bp"
    for slot in range(worker_count):
        for index, job in enumerate(j for j in jobs if j["slot"] == slot):
            fn = leaves[(job["m"], job["n"])]["c_interface_symbol"]
            row, col, mm, nn = job["row"], job["col"], job["m"], job["n"]
            preparation.append(f"""{{{prefix}_job*j=&s->jobs[{slot}][{index}];j->function={fn};j->last={int(job["last"])};
 const int64_t as[2]={{{mm},{k}}},bs[2]={{{k},{nn}}},cs[2]={{{mm},{nn}}},ap[2]={{1,{m}}},bp[2]={{{n},1}};
 if(merlin_prepare_strided_descriptor(s->base.at,sizeof s->base.at,1,2,{row},as,ap,&j->arguments[0])||
    merlin_prepare_strided_descriptor({bp_pointer},{bp_capacity},1,2,{col},bs,bp,&j->arguments[1])||
    merlin_prepare_strided_descriptor(s->base.cp,sizeof s->base.cp,4,2,{row * n + col},cs,bp,&j->arguments[2]))return -702;}}
""")
    count_initializers = "".join(
        f"s->counts[{slot}]={count};s->calls[{slot}]=0;"
        for slot, count in enumerate(counts)
    )
    control = work / (prefix + "_control.c")
    control.write_text(f'''#include "{header.name}"
{declarations}
int {prefix}_prepare(void*raw,const merlin_staged_kernel_invocation*i){{
 if(!raw)return -701;{state}*s=raw;int result={base.prepare}(&s->base,i);if(result)return result;
 {count_initializers}
 {"".join(preparation)}
 return 0;
}}
int {prefix}_finish(void*raw,const merlin_staged_kernel_invocation*i){{
 if(!raw)return -703;{state}*s=raw;for(unsigned q=0;q<{worker_count};q++)if(s->calls[q]!=s->counts[q])return -704;
 return {base.finish}(&s->base,i);
}}
''')
    hart_checks = "||".join(
        f"i->worker_harts[{slot}]!={hart}" for slot, hart in enumerate(harts)
    )
    compute = work / (prefix + "_compute.c")
    compute.write_text(f'''#include "{header.name}"
int {prefix}_compute(void*raw,const merlin_staged_kernel_invocation*i,unsigned slot){{
 if(!raw||!i||slot>={worker_count}||i->abi_version!=MERLIN_STAGED_KERNEL_ABI_VERSION||i->worker_count!={worker_count}||!i->worker_harts||{hart_checks}||iris_vme_split_hart()!=i->worker_harts[slot]||i->selected_worker_mask!={slot_mask}||!(i->selected_worker_mask&(1u<<slot)))return -705;
 {state}*s=raw;if(s->base.work.sequence!=i->sequence)return -706;
 for(unsigned q=0;q<s->counts[slot];q++){{{prefix}_job*j=&s->jobs[slot][q];j->function(&j->arguments[0],&j->arguments[1],&j->arguments[2]);s->calls[slot]++;if(j->last)s->base.completed[slot]++;}}
#if defined(__riscv)
 __asm__ volatile("fence rw,rw":::"memory");
#endif
 return 0;
}}
''')
    probe = work / "state_layout.c"
    probe.write_text(
        f'#include "{header.name}"\n#if defined(__riscv_vector)\n#error "scalar controller metadata flags required"\n#endif\nconst unsigned long staged_bytes=sizeof({state});\nconst unsigned long staged_alignment=_Alignof({state});\n'
    )
    includes = ["-I" + str(work), "-I" + str(runtime_include)]
    observed = work / "state_layout.ll"
    command(
        [cc, *controller_cflags, *includes, "-S", "-emit-llvm", probe, "-o", observed],
        "layout",
    )

    def constant(name):
        rows = [
            line
            for line in observed.read_text().splitlines()
            if line.startswith("@" + name + " =")
        ]
        if len(rows) != 1:
            raise ValueError("missing actual state layout")
        words = rows[0].replace(",", " ").split()
        index = words.index("constant")
        if words[index + 1] != "i64":
            raise ValueError("RVV bridge requires observed LP64 state layout")
        return int(words[index + 2])

    state_bytes, state_alignment = (
        constant("staged_bytes"),
        constant("staged_alignment"),
    )
    for file, flags, label in [
        (control, controller_cflags, "rvv_control"),
        (compute, compute_cflags, "rvv_compute"),
    ]:
        obj = work / (label + ".o")
        command([cc, *flags, *includes, "-c", file, "-o", obj], label)
        objects.append(obj)
    control_objects = tuple(Path(p) for p in base.provenance["controller_objects"])
    if not all(p in base.objects for p in control_objects):
        raise ValueError("unbound controller object")
    provenance = {
        "schema": "iris.outlined_compiler_rvv.v1",
        "source_shape": [m, n, k],
        "worker_harts": list(harts),
        "worker_count": worker_count,
        "selected_worker_slot_mask": slot_mask,
        "selected_physical_hart_mask": physical_mask,
        "VLEN_bits": vlen,
        "jobs": jobs,
        "leaves": list(leaves.values()),
        "descriptor_job_budget": descriptor_job_budget,
        "commands": commands,
        "controller_packing": "scalar_common_ISA",
        "base_controller_record": base.record(),
        "arithmetic_source": "public generic RVV borrowed-I8 leaf compiler; no arithmetic C kernel",
        "common_layout_qualification": "same Iris At/Bp/Cp ownership/copies as VME; not best or natural-layout RVV denominator",
        "whole_ELF_zero_VME_claimed": False,
        "compute_leaf_VME_instructions": None,
        "machine_register_and_ISA_audit": "separate actual-object gate required; source format alone grants none",
        "full_model_admission": False,
        "physical_busy": None,
    }
    if readonly:
        provenance["common_layout_qualification"] = (
            "same Iris At/Cp ownership and exact readonly-B lease as VME; no per-inference B allocation/copy; not best or natural-layout RVV denominator"
        )
    bound = [
        header,
        control,
        compute,
        probe,
        observed,
        Path(__file__),
        Path(api.__file__),
        Path(vector_backend.__file__),
        Path(runtime_include) / "merlin_model.h",
        Path(runtime_include) / "merlin_staged_kernel.h",
        *objects,
    ]
    bound += [p for p in work.rglob("*") if p.is_file()]
    pins = dict(base.input_sha256)
    pins.update({str(p.resolve()): sha256_file(p) for p in bound})
    value = PreparedStagedKernel(
        base.source_ir_sha256,
        header,
        state,
        state_bytes,
        state_alignment,
        prefix + "_prepare",
        prefix + "_compute",
        prefix + "_finish",
        base.worker_slots,
        (*control_objects, *objects),
        pins,
        provenance,
        **({"readonly_inputs": readonly} if readonly else {}),
    )
    value.verify(worker_count=worker_count)
    (work / "prepared.json").write_text(json.dumps(value.record(), indent=2) + "\n")
    return value
