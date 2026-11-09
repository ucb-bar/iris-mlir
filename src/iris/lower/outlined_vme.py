"""Explicit source-proved split VME implementation for an outlined dispatch.

The ordinary integrated-packing provider is unchanged. This adapter accepts a
closed canonical signed-byte MAC only, derives its unit facts through the same
provider contract, and reuses the public split prepare/compute/finish sources.
It creates no thread or arithmetic C kernel and certifies no hardware result.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def worker_binding(roles, worker_slots):
    """Bind logical invocation slots to the selected physical compute harts."""
    harts = roles.compute_harts
    if (
        harts not in ((0,), (1,), (0, 1))
        or any(type(hart) is not int for hart in harts)
        or not isinstance(roles.profile_harts, tuple)
        or any(type(hart) is not int for hart in roles.profile_harts)
        or roles.profile_harts != harts
        or any(
            getattr(roles, key) != 2
            for key in ("model_init_hart", "pack_hart", "dispatch_hart", "finish_hart")
        )
    ):
        raise ValueError("Iris requires controller2 and compute/profile harts0/1")
    if (
        not isinstance(worker_slots, tuple)
        or not worker_slots
        or len(set(worker_slots)) != len(worker_slots)
        or any(
            type(slot) is not int or not 0 <= slot < len(harts) for slot in worker_slots
        )
    ):
        raise ValueError("explicit nonempty subset of the actual worker slots required")
    return (
        harts,
        sum(1 << slot for slot in worker_slots),
        sum(1 << harts[slot] for slot in worker_slots),
    )


def admit_signed_gemm(module) -> tuple[int, int, int]:
    """Prove exact zero-seeded wrapping S8×S8→I32 and dense canonical layout."""
    from merlin.kernels.shapes import zero_initialised
    from merlin.llvmlower import contraction_normal_form as cnf
    from merlin.llvmlower.deployable_outline import static_tensor_contract
    from xdsl.dialects.arith import IntegerOverflowAttr

    module.verify()
    if any(not key.startswith("prov.") for key in module.attributes):
        raise ValueError("unproved source module contract")
    functions = [op for op in module.body.block.ops if op.name == "func.func"]
    if len(functions) != 1 or len(functions[0].body.blocks) != 1:
        raise ValueError("one closed tensor kernel required")
    fn = functions[0]
    if fn.arg_attrs is not None or fn.res_attrs is not None:
        raise ValueError("unproved source argument/result contract")
    if len(fn.function_type.inputs) != 2 or len(fn.function_type.outputs) != 1:
        raise ValueError("exactly two signed-byte inputs and one I32 result required")
    a, b = [static_tensor_contract(t) for t in fn.function_type.inputs]
    c = static_tensor_contract(fn.function_type.outputs.data[0])
    if (
        a[1] != "i8"
        or b[1] != "i8"
        or c[1] != "i32"
        or any(len(t[0]) != 2 for t in (a, b, c))
    ):
        raise ValueError("rank-two signed-byte/I32 source ABI required")
    m, k = a[0]
    kk, n = b[0]
    if k != kk or c[0] != (m, n):
        raise ValueError("canonical A[M,K], B[K,N], C[M,N] source shapes required")
    allowed = {
        "func.func",
        "func.return",
        "tensor.empty",
        "arith.constant",
        "linalg.fill",
        "linalg.generic",
        "arith.extsi",
        "arith.muli",
        "arith.addi",
        "linalg.yield",
    }
    for op in module.walk():
        if op is module:
            continue
        if op.name not in allowed:
            raise ValueError("unsupported closed split-kernel operation " + op.name)
        if any(
            not key.startswith("prov.")
            for key in op.attributes
            if key != "llvm.emit_c_interface"
        ):
            raise ValueError("unproved source operation contract")
        if op.name in ("arith.muli", "arith.addi") and op.properties != {
            "overflowFlags": IntegerOverflowAttr("none")
        }:
            raise ValueError("source arithmetic must be unflagged wrapping I32")
    roots = [op for op in fn.body.block.ops if op.name == "linalg.generic"]
    if len(roots) != 1:
        raise ValueError("one arithmetic contraction required")
    op = roots[0]
    nf = cnf.analyze(op)
    if isinstance(nf, str) or nf.steps(cnf.AS_IS) or not zero_initialised(op):
        raise ValueError("zero-seeded canonical signed-byte contraction required")
    if tuple(op.inputs) != tuple(fn.body.block.args) or len(op.outputs) != 1:
        raise ValueError("untransformed direct tensor operands required")
    body = op.body.block
    ops = list(body.ops)
    if [v.name for v in ops] != [
        "arith.extsi",
        "arith.extsi",
        "arith.muli",
        "arith.addi",
        "linalg.yield",
    ]:
        raise ValueError(
            "exact two sign extensions, product, wrapping sum and yield required"
        )
    x, y, mul, add, yield_ = ops
    if (
        tuple(x.operands) != (body.args[0],)
        or tuple(y.operands) != (body.args[1],)
        or str(x.results[0].type) != "i32"
        or str(y.results[0].type) != "i32"
        or set(mul.operands) != {x.results[0], y.results[0]}
        or set(add.operands) != {mul.results[0], body.args[2]}
        or tuple(yield_.operands) != (add.results[0],)
    ):
        raise ValueError("source scalar dataflow is not acc + signed(A)*signed(B)")
    fill = op.outputs[0].owner
    if (
        fill.name != "linalg.fill"
        or fill.inputs[0].owner.name != "arith.constant"
        or fill.outputs[0].owner.name != "tensor.empty"
    ):
        raise ValueError("fresh zero-filled tensor accumulator required")
    top = list(fn.body.block.ops)
    if (
        set(top) != {fill.outputs[0].owner, fill.inputs[0].owner, fill, op, top[-1]}
        or top[-1].name != "func.return"
        or tuple(top[-1].operands) != (op.results[0],)
    ):
        raise ValueError("source kernel has additional live/control/effect operations")
    return m, n, k


def prepare_staged_kernel(
    module,
    work,
    *,
    unit,
    config,
    roles,
    worker_slots,
    cc,
    controller_cflags,
    compute_cflags,
    runtime_include,
    prefix,
    allow_vector_controller_packing=False,
):
    """Prepare a byte-bound staged implementation; explicit selection never falls back."""
    from merlin.common import provenance as source_provenance
    from merlin.common.digest import sha256_file
    from merlin.kernels.matrix_kernels import unit_header
    from merlin.llvmlower.deployable_ir import checked_generic_text
    from merlin.runtime.prepared_staged_kernel import PreparedStagedKernel

    from .. import backend
    from . import split_vme

    if not prefix.isascii() or not prefix.isidentifier():
        raise ValueError("C-safe explicit prefix required")
    if type(allow_vector_controller_packing) is not bool:
        raise ValueError(
            "explicit boolean vector-controller experiment selection required"
        )
    harts, slot_mask, mask = worker_binding(roles, worker_slots)
    worker_count = len(harts)
    m, n, k = admit_signed_gemm(module)
    layout = split_vme.from_config(m, n, k, unit=unit, config=config, worker_mask=mask)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    kernel = work / "kernel"
    kernel.mkdir()
    files = split_vme.source_files()
    for name, path in files.items():
        shutil.copyfile(path, kernel / name)
    header_text = unit_header(
        backend, unit=unit, config=config, header="saturn_vme.h", prefix="VME"
    )
    (kernel / "saturn_vme.h").write_text(header_text)
    align = layout.alignment_bytes
    te = layout.tile_edge
    state = prefix + "_state"
    header = work / (prefix + ".h")
    header.write_text(f"""#ifndef {prefix.upper()}_H
#define {prefix.upper()}_H
#include "merlin_staged_kernel.h"
#include "kernel/vme_split.h"
typedef struct {{
 iris_vme_split_buffers buffers;
 iris_vme_split_work work;
 size_t completed[{worker_count}];
 _Alignas({align}) int8_t at[{m * k}],bp[{k * n}];
 _Alignas({align}) int32_t cp[{m * n}];
}} {state};
int {prefix}_prepare(void*,const merlin_staged_kernel_invocation*);
int {prefix}_compute(void*,const merlin_staged_kernel_invocation*,unsigned);
int {prefix}_finish(void*,const merlin_staged_kernel_invocation*);
#endif
""")
    hart_checks = "||".join(
        f"i->worker_harts[{slot}]!={hart}" for slot, hart in enumerate(harts)
    )
    common = f'''#include "{header.name}"
static int valid(const merlin_staged_kernel_invocation*i){{
 if(!i||i->abi_version!=MERLIN_STAGED_KERNEL_ABI_VERSION||i->input_count!=2||i->output_count!=1||i->argument_count!=3||!i->sequence||i->worker_count!={worker_count}||i->selected_worker_mask!={slot_mask}||!i->worker_harts||{hart_checks}||!i->arguments||!i->data||!i->capacities||!i->descriptors)return 0;
 const long dims[3][2]={{{{{m},{k}}},{{{k},{n}}},{{{m},{n}}}}};
 for(unsigned j=0;j<3;j++)if(i->arguments[j].kind!=(j<2?MERLIN_INPUT:MERLIN_OUTPUT)||i->arguments[j].rank!=2||i->arguments[j].dims[0]!=dims[j][0]||i->arguments[j].dims[1]!=dims[j][1]||i->arguments[j].elem_size!=(j<2?1:4)||!i->descriptors[j])return 0;
 return 1;
}}
'''
    control = work / (prefix + "_control.c")
    control.write_text(
        common
        + f"""
int {prefix}_prepare(void*raw,const merlin_staged_kernel_invocation*i){{
 if(!raw||!valid(i))return -601;{state}*s=raw;
 for(unsigned slot=0;slot<{worker_count};slot++)s->completed[slot]=0;
 s->buffers=(iris_vme_split_buffers){{.a=i->data[0],.b=i->data[1],.output=i->data[2],.at=s->at,.bp=s->bp,.cp=s->cp,.a_bytes=i->capacities[0],.b_bytes=i->capacities[1],.output_bytes=i->capacities[2],.at_bytes=sizeof s->at,.bp_bytes=sizeof s->bp,.cp_bytes=sizeof s->cp,.m={m},.n={n},.k={k},.tile_edge={te},.alignment_bytes={align},.sequence=i->sequence,.worker_mask={mask}}};
 return iris_vme_split_prepare(&s->buffers,&s->work);
}}
int {prefix}_finish(void*raw,const merlin_staged_kernel_invocation*i){{
 if(!raw||!valid(i))return -602;{state}*s=raw;if(s->work.sequence!=i->sequence)return -603;
 return iris_vme_split_finish(&s->buffers,&s->work);
}}
"""
    )
    compute = work / (prefix + "_compute.c")
    compute.write_text(
        common
        + f"""
int {prefix}_compute(void*raw,const merlin_staged_kernel_invocation*i,unsigned slot){{
 if(!raw||!valid(i)||slot>={worker_count}||!(i->selected_worker_mask&(1u<<slot))||iris_vme_split_hart()!=i->worker_harts[slot])return -604;
 {state}*s=raw;if(s->work.sequence!=i->sequence)return -605;
 return iris_vme_split_compute(&s->work,&s->completed[slot]);
}}
"""
    )
    probe = work / "state_layout.c"
    probe.write_text(
        f'#include "{header.name}"\nconst unsigned long staged_state_bytes=sizeof({state});\nconst unsigned long staged_state_alignment=_Alignof({state});\n'
        "#if defined(__riscv_vector)\nconst unsigned long staged_controller_vector_packing=1;\n"
        "#else\nconst unsigned long staged_controller_vector_packing=0;\n#endif\n"
    )
    includes = ["-I" + str(work), "-I" + str(kernel), "-I" + str(runtime_include)]
    commands = []

    def command(argv, name):
        p = subprocess.run(
            list(map(str, argv)), capture_output=True, check=False, timeout=120
        )
        (work / (name + ".stdout")).write_bytes(p.stdout)
        (work / (name + ".stderr")).write_bytes(p.stderr)
        commands.append({"argv": list(map(str, argv)), "returncode": p.returncode})
        p.check_returncode()

    observed = work / "state_layout.ll"
    command(
        [cc, *controller_cflags, *includes, "-S", "-emit-llvm", probe, "-o", observed],
        "layout",
    )

    def constant(name):
        rows = [
            s
            for s in observed.read_text().splitlines()
            if s.startswith("@" + name + " =")
        ]
        if len(rows) != 1:
            raise ValueError("compiler state-layout observation missing")
        words = rows[0].replace(",", " ").split()
        i = words.index("constant")
        if words[i + 1] != "i64":
            raise ValueError("Iris split requires observed LP64 state layout")
        return int(words[i + 2])

    state_bytes, state_alignment = (
        constant("staged_state_bytes"),
        constant("staged_state_alignment"),
    )
    vector_controller = constant("staged_controller_vector_packing")
    if vector_controller and not allow_vector_controller_packing:
        raise ValueError(
            "controller flags enable unqualified RVV packing; use scalar common-ISA flags or explicit experiment opt-in"
        )
    objects = []
    for source, flags, name in [
        (kernel / "vme_split_prepare.c", controller_cflags, "split_prepare"),
        (control, controller_cflags, "controller_bridge"),
        (kernel / "vme_split_compute.c", compute_cflags, "split_compute"),
        (compute, compute_cflags, "compute_bridge"),
    ]:
        obj = work / (name + ".o")
        command([cc, *flags, *includes, "-c", source, "-o", obj], name)
        objects.append(obj)
    source_text = checked_generic_text(module)
    (work / "selected-source.mlir").write_text(source_text)
    contract = backend.load_contract(unit)
    derived = backend.derive_encodings(contract)
    verifications = {
        contract.pin: source_provenance.verify(
            contract.pin,
            checkout=contract.checkout(),
            reads=list(contract.sources.values()),
        )
    }
    if contract.core.get("pin"):
        verifications[str(contract.core["pin"])] = source_provenance.verify(
            str(contract.core["pin"]),
            checkout=contract.core_checkout(),
            reads=[str(contract.core["source"])],
        )
    if contract.host.get("pin"):
        verifications[str(contract.host["pin"])] = source_provenance.verify(
            str(contract.host["pin"]),
            checkout=contract.root(),
            reads=[str(contract.host["source"])],
        )
    bound = [
        header,
        probe,
        observed,
        control,
        compute,
        Path(__file__),
        Path(split_vme.__file__),
        Path(backend.__file__),
        contract.contract_path,
        Path(cc),
        Path(runtime_include) / "merlin_staged_kernel.h",
        Path(runtime_include) / "merlin_model.h",
        *objects,
        *files.values(),
        *kernel.iterdir(),
        *map(Path, derived.sources.values()),
        *contract.config_scala(),
        *contract.mixin_scala(),
    ]
    provenance = {
        "schema": "iris.outlined_split_vme.v1",
        "unit": unit,
        "config": config,
        "worker_harts": list(harts),
        "worker_count": worker_count,
        "selected_worker_slot_mask": slot_mask,
        "selected_physical_hart_mask": mask,
        "source_shape": [m, n, k],
        "numeric_contract": "closed zero-seeded signedI8×signedI8 wrappingI32",
        "layout": layout.to_dict(),
        "state_size_observation": str(observed),
        "commands": commands,
        "controller_cflags": list(controller_cflags),
        "controller_packing": "RVV_unqualified_explicit_experiment"
        if vector_controller
        else "scalar_common_ISA",
        "vector_controller_packing_qualified": False,
        "compute_cflags": list(compute_cflags),
        "controller_objects": [str(path) for path in objects[:2]],
        "required_worker_initialization": "pinned thread owns Zephyr iris_vme_enable context before ARM; no arbitrary migration",
        "integrated_packing_shim_used": False,
        "arithmetic_source": "existing public vme_split_compute + registered vme_gemm_2x2_bias",
        "hardware_correctness_qualified": False,
        "full_model_admission": False,
        "physical_busy": None,
        "outlined_stage_dispatch_integrated": True,
        "source_pin_verification": {
            name: verdict.to_dict() for name, verdict in verifications.items()
        },
        "source_pins_all_match": all(verdict.ok for verdict in verifications.values()),
        "source_image_association_qualified": False,
    }
    value = PreparedStagedKernel(
        hashlib.sha256(source_text.encode()).hexdigest(),
        header,
        state,
        state_bytes,
        state_alignment,
        prefix + "_prepare",
        prefix + "_compute",
        prefix + "_finish",
        worker_slots,
        tuple(objects),
        {str(p.resolve()): sha256_file(p) for p in bound if p.is_file()},
        provenance,
    )
    value.verify(worker_count=worker_count)
    (work / "prepared.json").write_text(json.dumps(value.record(), indent=2) + "\n")
    return value
