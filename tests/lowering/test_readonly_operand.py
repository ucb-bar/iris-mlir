"""Explicit sealed-B reuse changes transport/state, not selected arithmetic."""

import os
import shutil
import subprocess

import pytest
from merlin.common.paths import runtime_dir
from merlin.frontends.linalg_mlir import parse_mlir_text
from merlin.llvmlower import codegen, toolchain
from merlin.runtime.prepared_staged_kernel import ReadonlyInput
from merlin.runtime.staged_dispatch import StagedRoles
from test_outlined_vme import SOURCE

from iris.lower.outlined_vme import prepare_staged_kernel


@pytest.fixture
def compiler_args():
    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit source-pinned RTL and MLIR compiler required")
    flags = tuple(codegen.RISCV_FLAGS) + (
        "-mabi=lp64d",
        "-fno-vectorize",
        "-fno-slp-vectorize",
    )
    return {
        "unit": "saturn_vme",
        "config": "IrisConfig",
        "roles": StagedRoles((0, 1, 2), 2, 2, 2, 2, (0, 1), (0, 1)),
        "worker_slots": (0, 1),
        "cc": toolchain.clang(),
        "compute_cflags": flags + ("-march=rv64gcv_zvl256b",),
        "controller_cflags": flags + ("-march=rv64gc",),
        "runtime_include": runtime_dir() / "c",
        "prefix": "leased",
    }


def requirement(**changes):
    return ReadonlyInput(
        **(
            {
                "index": 1,
                "dtype": "i8",
                "shape": (17, 65),
                "bytes": 17 * 65,
                "sha256": "a" * 64,
                "alignment": 16,
            }
            | changes
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"index": 0},
        {"dtype": "f8E4M3FN"},
        {"shape": (65, 17)},
        {"bytes": 17 * 65 - 1},
        {"alignment": 1},
    ],
)
def test_exact_readonly_operand_contract_required_before_writing(
    tmp_path, compiler_args, changes
):
    with pytest.raises(ValueError, match="readonly"):
        prepare_staged_kernel(
            parse_mlir_text(SOURCE),
            tmp_path / "refused",
            readonly_b=requirement(**changes),
            **compiler_args,
        )
    assert not (tmp_path / "refused").exists()


def test_actual_build_removes_bp_state_and_propagates_rvv_requirement(
    tmp_path, compiler_args
):
    from iris.lower.outlined_rvv import prepare_staged_kernel as rvv_kernel

    old = prepare_staged_kernel(
        parse_mlir_text(SOURCE), tmp_path / "copied", **compiler_args
    )
    new = prepare_staged_kernel(
        parse_mlir_text(SOURCE),
        tmp_path / "leased",
        readonly_b=requirement(),
        **compiler_args,
    )
    assert old.readonly_inputs == () and new.readonly_inputs == (requirement(),)
    assert old.state_bytes - new.state_bytes >= 17 * 65
    assert "bp[" not in new.header.read_text()
    assert (
        new.provenance["readonly_operand_reuse"]["packed_weight_allocation_bytes"] == 0
    )
    # Kernel implementation bytes and compute wrapper semantics are unchanged.
    assert (tmp_path / "copied/kernel/vme_split_compute.c").read_bytes() == (
        tmp_path / "leased/kernel/vme_split_compute.c"
    ).read_bytes()
    assert (tmp_path / "copied/compute_bridge.o").read_bytes() != b""
    vector = rvv_kernel(
        parse_mlir_text(SOURCE),
        new,
        tmp_path / "rvv",
        cc=compiler_args["cc"],
        controller_cflags=compiler_args["controller_cflags"],
        compute_cflags=compiler_args["compute_cflags"],
        runtime_include=compiler_args["runtime_include"],
        prefix="leased_vector",
        descriptor_job_budget=16,
    )
    assert vector.readonly_inputs == new.readonly_inputs
    controller = (tmp_path / "rvv/leased_vector_control.c").read_text()
    assert (
        "(void*)s->base.work.bp" in controller and "sizeof s->base.bp" not in controller
    )


def test_generated_controller_uses_readonly_mapping_and_refuses_dynamic_kind(
    tmp_path, compiler_args
):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("host C compiler required")
    new = prepare_staged_kernel(
        parse_mlir_text(SOURCE),
        tmp_path / "leased",
        readonly_b=requirement(),
        **compiler_args,
    )
    source = tmp_path / "protocol.c"
    source.write_text(r"""
#define _GNU_SOURCE
#include "leased.h"
#include <assert.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
unsigned iris_vme_split_test_hart=2;
static leased_state state;
static _Alignas(64) int8_t a[3*17];
static _Alignas(64) int32_t output[3*65];
int main(void){
 size_t page=(size_t)sysconf(_SC_PAGESIZE);
 int8_t*b=mmap(0,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(b!=MAP_FAILED);
 for(size_t j=0;j<17*65;j++)b[j]=(int8_t)j;
 assert(!mprotect(b,page,PROT_READ));
 merlin_arg_t args[3]={{MERLIN_INPUT,0,2,{3,17},1},{MERLIN_WEIGHT,0,2,{17,65},1},{MERLIN_OUTPUT,0,2,{3,65},4}};
 void*data[3]={a,b,output};size_t caps[3]={sizeof a,17*65,sizeof output};
 merlin_descriptor_t d[3];void*descriptors[3]={&d[0],&d[1],&d[2]};unsigned harts[2]={0,1};
 merlin_staged_kernel_invocation i={MERLIN_STAGED_KERNEL_ABI_VERSION,2,1,3,args,data,caps,descriptors,1,2,3,harts};
 for(unsigned generation=1;generation<4;generation++){
  i.sequence=generation;assert(!leased_prepare(&state,&i));assert(state.work.bp==b);
  for(size_t j=0;j<3*65;j++)state.cp[j]=(int32_t)(generation+j);
  assert(!leased_finish(&state,&i));assert(!memcmp(output,state.cp,sizeof output));
 }
 leased_state before=state;args[1].kind=MERLIN_INPUT;
 assert(leased_prepare(&state,&i));assert(!memcmp(&state,&before,sizeof state));
 assert(!munmap(b,page));return 0;
}
""")
    exe = tmp_path / "protocol"
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-misleading-indentation",
            "-DIRIS_VME_SPLIT_HOST_TEST",
            "-I" + str(new.header.parent),
            "-I" + str(runtime_dir() / "c"),
            str(source),
            str(new.header.parent / "leased_control.c"),
            str(new.header.parent / "kernel/vme_split_prepare.c"),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run([str(exe)], check=True, capture_output=True, timeout=30)
