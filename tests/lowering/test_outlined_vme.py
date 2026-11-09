"""The explicit split adapter proves scalar semantics, not names or shapes."""

import pytest
from merlin.frontends.linalg_mlir import parse_mlir_text

from iris.lower.outlined_vme import admit_signed_gemm, worker_binding

SOURCE = """builtin.module {
func.func @forward(%a:tensor<3x17xi8>,%b:tensor<17x65xi8>)->tensor<3x65xi32> {
 %e=tensor.empty():tensor<3x65xi32>
 %zero=arith.constant 0:i32
 %init=linalg.fill ins(%zero:i32) outs(%e:tensor<3x65xi32>)->tensor<3x65xi32>
 %m=linalg.generic {indexing_maps=[affine_map<(i,j,k)->(i,k)>,affine_map<(i,j,k)->(k,j)>,affine_map<(i,j,k)->(i,j)>],iterator_types=["parallel","parallel","reduction"]}
 ins(%a,%b:tensor<3x17xi8>,tensor<17x65xi8>) outs(%init:tensor<3x65xi32>){
 ^bb0(%x:i8,%y:i8,%acc:i32):
 %xx=arith.extsi %x:i8 to i32
 %yy=arith.extsi %y:i8 to i32
 %prod=arith.muli %xx,%yy:i32
 %sum=arith.addi %prod,%acc:i32
 linalg.yield %sum:i32
 }->tensor<3x65xi32>
 func.return %m:tensor<3x65xi32>
}}
"""


@pytest.mark.parametrize(
    "harts,slots,logical_mask,physical_mask",
    [
        ((0,), (0,), 1, 1),
        ((1,), (0,), 1, 2),
        ((0, 1), (0,), 1, 1),
        ((0, 1), (1,), 2, 2),
        ((0, 1), (0, 1), 3, 3),
    ],
)
def test_worker_slots_are_distinct_from_physical_hart_bits(
    harts, slots, logical_mask, physical_mask
):
    from merlin.runtime.staged_dispatch import StagedRoles

    roles = StagedRoles((0, 1, 2), 2, 2, 2, 2, harts, harts)
    assert worker_binding(roles, slots) == (harts, logical_mask, physical_mask)


@pytest.mark.parametrize(
    "harts,slots",
    [
        ((0,), (1,)),
        ((1,), (1,)),
        ((0, 1), (2,)),
        ((0, 1), (0, 0)),
        ((0,), (False,)),
        ((False,), (0,)),
        ((1, 0), (0,)),
    ],
)
def test_unavailable_duplicate_or_unknown_workers_refuse(harts, slots):
    from types import SimpleNamespace

    roles = SimpleNamespace(
        compute_harts=harts,
        profile_harts=harts,
        model_init_hart=2,
        pack_hart=2,
        dispatch_hart=2,
        finish_hart=2,
    )
    with pytest.raises(ValueError):
        worker_binding(roles, slots)


@pytest.mark.parametrize("profile", [(1,), (2,), (0.0,), (False,)])
def test_compute_and_profile_rosters_must_match_exactly(profile):
    from types import SimpleNamespace

    roles = SimpleNamespace(
        compute_harts=(0,),
        profile_harts=profile,
        model_init_hart=2,
        pack_hart=2,
        dispatch_hart=2,
        finish_hart=2,
    )
    with pytest.raises(ValueError):
        worker_binding(roles, (0,))


def test_source_proof_derives_shapes_and_preserves_source():
    module = parse_mlir_text(SOURCE)
    before = str(module)
    assert admit_signed_gemm(module) == (3, 65, 17)
    assert str(module) == before


@pytest.mark.parametrize(
    "original,replacement",
    [
        ("arith.extsi %x", "arith.extui %x"),
        ("arith.constant 0:i32", "arith.constant 1:i32"),
        ("arith.muli %xx,%yy:i32", "arith.muli %xx,%xx:i32"),
        ("arith.addi %prod,%acc:i32", "arith.addi %acc,%acc:i32"),
        ("arith.muli %xx,%yy:i32", "arith.muli %xx,%yy overflow<nsw>:i32"),
        ("arith.addi %prod,%acc:i32", "arith.addi %prod,%acc overflow<nuw>:i32"),
        ("func.func @forward", "func.func @another"),
    ],
)
def test_semantics_and_overflow_are_not_inferred_from_shape(original, replacement):
    module = parse_mlir_text(SOURCE.replace(original, replacement))
    if original == "func.func @forward":
        assert admit_signed_gemm(module) == (
            3,
            65,
            17,
        )  # function spelling is not selection
    else:
        with pytest.raises(ValueError):
            admit_signed_gemm(module)


def test_unknown_contract_and_additional_live_math_refuse():
    from xdsl.dialects.builtin import StringAttr

    module = parse_mlir_text(SOURCE)
    next(op for op in module.walk() if op.name == "arith.muli").attributes[
        "unknown.contract"
    ] = StringAttr("must-not-drop")
    with pytest.raises(ValueError, match="unproved"):
        admit_signed_gemm(module)
    module = parse_mlir_text(
        SOURCE.replace(
            "%prod=arith.muli", "%extra=arith.constant 7:i32\n %prod=arith.muli"
        )
    )
    with pytest.raises(ValueError, match="canonical signed-byte contraction"):
        admit_signed_gemm(module)


def test_argument_contract_cannot_be_discarded():
    from xdsl.dialects.builtin import ArrayAttr, DictionaryAttr, StringAttr

    module = parse_mlir_text(SOURCE)
    fn = next(op for op in module.walk() if op.name == "func.func")
    fn.properties["arg_attrs"] = ArrayAttr(
        [
            DictionaryAttr({"unknown.contract": StringAttr("required")}),
            DictionaryAttr({}),
        ]
    )
    with pytest.raises(ValueError, match="argument/result"):
        admit_signed_gemm(module)


def test_actual_controller_macro_guard_and_scalar_build(tmp_path):
    import os

    from merlin.common.paths import runtime_dir
    from merlin.llvmlower import codegen, toolchain
    from merlin.runtime.staged_dispatch import StagedRoles

    from iris.lower.outlined_vme import prepare_staged_kernel

    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit source-pinned RTL and MLIR compiler required")
    roles = StagedRoles((0, 1, 2), 2, 2, 2, 2, (0, 1), (0, 1))
    flags = tuple(codegen.RISCV_FLAGS) + (
        "-mabi=lp64d",
        "-fno-vectorize",
        "-fno-slp-vectorize",
    )
    args = {
        "unit": "saturn_vme",
        "config": "IrisConfig",
        "roles": roles,
        "worker_slots": (0, 1),
        "cc": toolchain.clang(),
        "compute_cflags": flags + ("-march=rv64gcv_zvl256b",),
        "runtime_include": runtime_dir() / "c",
        "prefix": "guarded",
    }
    with pytest.raises(ValueError, match="unqualified RVV packing"):
        prepare_staged_kernel(
            parse_mlir_text(SOURCE),
            tmp_path / "vector",
            controller_cflags=flags + ("-march=rv64gcv_zvl256b",),
            **args,
        )
    assert not (tmp_path / "vector/prepared.json").exists()
    value = prepare_staged_kernel(
        parse_mlir_text(SOURCE),
        tmp_path / "scalar",
        controller_cflags=flags + ("-march=rv64gc",),
        **args,
    )
    assert value.provenance["controller_packing"] == "scalar_common_ISA"
    assert value.provenance["vector_controller_packing_qualified"] is False
    assert all(p.read_bytes()[:4] == b"\x7fELF" for p in value.objects)
