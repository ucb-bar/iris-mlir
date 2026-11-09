"""Typed E4M3 source proof and scalar controller bit-preserving storage gates."""

import shutil
import subprocess

import pytest
from merlin.frontends.linalg_mlir import parse_mlir_text
from xdsl.utils.exceptions import ParseError, VerifyException

from iris.lower.outlined_fp8 import admit_fp8_gemm
from iris.lower.split_fp8 import source_files

SOURCE = """builtin.module {
func.func @forward(%a:tensor<3x17xf8E4M3FN>,%b:tensor<17x65xf8E4M3FN>)->tensor<3x65xf32> {
 %e=tensor.empty():tensor<3x65xf32>
 %zero=arith.constant 0.0:f32
 %init=linalg.fill ins(%zero:f32) outs(%e:tensor<3x65xf32>)->tensor<3x65xf32>
 %m=linalg.generic {indexing_maps=[affine_map<(i,j,k)->(i,k)>,affine_map<(i,j,k)->(k,j)>,
 affine_map<(i,j,k)->(i,j)>],iterator_types=["parallel","parallel","reduction"]}
 ins(%a,%b:tensor<3x17xf8E4M3FN>,tensor<17x65xf8E4M3FN>) outs(%init:tensor<3x65xf32>){
 ^bb0(%x:f8E4M3FN,%y:f8E4M3FN,%acc:f32):
 %xx=arith.extf %x:f8E4M3FN to f32
 %yy=arith.extf %y:f8E4M3FN to f32
 %prod=arith.mulf %xx,%yy:f32
 %sum=arith.addf %prod,%acc:f32
 linalg.yield %sum:f32
 }->tensor<3x65xf32>
 func.return %m:tensor<3x65xf32>
}}
"""


def test_typed_exact_product_source_proof_preserves_source():
    source = parse_mlir_text(SOURCE)
    before = str(source)
    assert admit_fp8_gemm(source) == (3, 65, 17)
    assert str(source) == before
    assert admit_fp8_gemm(parse_mlir_text(SOURCE.replace("@forward", "@another"))) == (
        3,
        65,
        17,
    )


@pytest.mark.parametrize(
    "before,after",
    [
        ("f8E4M3FN", "f8E5M2"),
        ("f8E4M3FN", "f32"),
        ("arith.constant 0.0:f32", "arith.constant -0.0:f32"),
        ("arith.constant 0.0:f32", "arith.constant 1.0:f32"),
        ("arith.mulf %xx,%yy:f32", "arith.mulf %xx,%xx:f32"),
        ("arith.addf %prod,%acc:f32", "arith.addf %prod,%prod:f32"),
        ("arith.mulf %xx,%yy:f32", "arith.mulf %xx,%yy fastmath<reassoc>:f32"),
        ("arith.addf %prod,%acc:f32", "arith.addf %prod,%acc fastmath<contract>:f32"),
        ("affine_map<(i,j,k)->(i,k)>", "affine_map<(i,j,k)->(k,i)>"),
    ],
)
def test_no_format_numeric_or_reduction_reinterpretation(before, after):
    # Some mutations are already invalid in xDSL; none may reach a provider.
    with pytest.raises((ValueError, ParseError, VerifyException)):
        admit_fp8_gemm(parse_mlir_text(SOURCE.replace(before, after)))


def test_named_fill_body_is_proved_not_assumed():
    source = parse_mlir_text(SOURCE)
    fill = next(op for op in source.walk() if op.name == "linalg.fill")
    next(iter(fill.body.block.ops)).operands = (fill.body.block.args[1],)
    with pytest.raises(ValueError, match="actually yield"):
        admit_fp8_gemm(source)


def test_unknown_properties_and_tensor_encoding_refuse():
    from xdsl.dialects.builtin import StringAttr

    source = parse_mlir_text(SOURCE)
    op = next(op for op in source.walk() if op.name == "linalg.generic")
    op.properties["library_call"] = StringAttr("external")
    with pytest.raises(ValueError, match="property"):
        admit_fp8_gemm(source)
    encoded = SOURCE.replace(
        "tensor<3x17xf8E4M3FN>", 'tensor<3x17xf8E4M3FN, "unproved-layout">'
    )
    with pytest.raises((ValueError, ParseError, VerifyException)):
        admit_fp8_gemm(parse_mlir_text(encoded))


def test_actual_controller_has_transactional_domain_and_raw_float_copy(tmp_path):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable")
    files = source_files()
    c = tmp_path / "controller_test.c"
    c.write_text(r"""
#include "vme_fp8_split.h"
#include <assert.h>
#include <string.h>
unsigned iris_vme_fp8_split_test_hart=2;
static _Alignas(64) unsigned char a[21],b[35],at[21],bp[35];
static _Alignas(64) float cp[15],out[15];
int main(void){
 for(unsigned i=0;i<21;i++)a[i]=(unsigned char)(i%126);
 for(unsigned i=0;i<35;i++)b[i]=(unsigned char)(i%126);
 iris_vme_fp8_split_buffers s={a,b,out,at,bp,cp,21,35,60,21,35,60,3,5,7,4,16,1,3};
 iris_vme_fp8_split_work w;
 memset(&w,0x55,sizeof w);memset(at,0x66,sizeof at);memset(bp,0x77,sizeof bp);memset(cp,0x88,sizeof cp);
 assert(!iris_vme_fp8_split_prepare(&s,&w));
 assert(w.finite_inputs_verified==1&&w.block_columns==2&&w.block_count==2);
 for(unsigned k=0;k<7;k++)for(unsigned i=0;i<3;i++)assert(at[k*3+i]==a[i*7+k]);
 assert(!memcmp(bp,b,35));
 for(unsigned i=0;i<sizeof cp;i++)assert(!((unsigned char*)cp)[i]);
 for(unsigned value=0;value<256;value++){
  unsigned char old_at[21],old_bp[35],old_cp[60];iris_vme_fp8_split_work before=w;
  memcpy(old_at,at,21);memcpy(old_bp,bp,35);memcpy(old_cp,cp,60);a[0]=(unsigned char)value;
  int status=iris_vme_fp8_split_prepare(&s,&w);
  if(value==127||value==255){
   assert(status==IRIS_VME_FP8_SPLIT_NONFINITE);
   assert(!memcmp(old_at,at,21)&&!memcmp(old_bp,bp,35)&&!memcmp(old_cp,cp,60)&&!memcmp(&before,&w,sizeof w));
  }else assert(!status&&at[0]==value);
 }
 a[0]=0;assert(!iris_vme_fp8_split_prepare(&s,&w));
 for(unsigned i=0;i<15;i++){uint32_t bits=0x80000000u+i*65537u;memcpy(cp+i,&bits,4);}
 /* Include a NaN payload to prove finish copies bits rather than numeric/int32 aliases. */
 uint32_t payload=0x7fc12345u;memcpy(cp+3,&payload,4);
 assert(!iris_vme_fp8_split_finish(&s,&w));assert(!memcmp(cp,out,60));
 iris_vme_fp8_split_test_hart=0;assert(iris_vme_fp8_split_prepare(&s,&w)==IRIS_VME_FP8_SPLIT_ROLE);
 assert(iris_vme_fp8_split_finish(&s,&w)==IRIS_VME_FP8_SPLIT_ROLE);
 iris_vme_fp8_split_test_hart=2;s.a_bytes=20;assert(iris_vme_fp8_split_prepare(&s,&w)==IRIS_VME_FP8_SPLIT_CAPACITY);
 s.a_bytes=21;s.output=(float*)at;assert(iris_vme_fp8_split_prepare(&s,&w)==IRIS_VME_FP8_SPLIT_ALIAS);
 return 0;
}
""")
    executable = tmp_path / "controller_test"
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-O2",
            "-fstrict-aliasing",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DIRIS_VME_FP8_SPLIT_HOST_TEST",
            "-I" + str(files["vme_fp8_split.h"].parent),
            str(c),
            str(files["vme_fp8_split_prepare.c"]),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=30)


def test_explicit_fp8_prepared_binding_and_source_guards(tmp_path):
    import os

    from merlin.common.paths import runtime_dir
    from merlin.llvmlower import codegen, toolchain
    from merlin.runtime.staged_dispatch import StagedRoles

    from iris.lower.outlined_fp8 import prepare_staged_kernel

    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit source-pinned RTL and compiler required")
    roles = StagedRoles((0, 1, 2), 2, 2, 2, 2, (1,), (1,))
    flags = tuple(codegen.RISCV_FLAGS) + (
        "-mabi=lp64d",
        "-fno-vectorize",
        "-fno-slp-vectorize",
    )
    kwargs = {
        "unit": "saturn_vme",
        "config": "IrisConfig",
        "roles": roles,
        "worker_slots": (0,),
        "cc": toolchain.clang(),
        "controller_cflags": flags + ("-march=rv64gc",),
        "compute_cflags": flags + ("-march=rv64gcv_zvl256b",),
        "runtime_include": runtime_dir() / "c",
        "prefix": "typed_fp8",
    }
    value = prepare_staged_kernel(
        parse_mlir_text(SOURCE), tmp_path / "prepared", **kwargs
    )
    data = value.record()
    proof = data["provenance"]
    assert data["worker_slots"] == [0]
    assert proof["selected_worker_slot_mask"] == 1
    assert proof["selected_physical_hart_mask"] == 2
    assert proof["worker_harts"] == [1]
    assert proof["source_pins_all_match"]
    assert proof["controller_packing"] == "scalar_common_ISA"
    assert proof["layout"]["input_quantizer"] is False
    assert proof["layout"]["source_input_types"] == ["f8E4M3FN", "f8E4M3FN"]
    assert proof["layout"]["block_shape"] == [32, 32]
    assert proof["integrated_packing_shim_used"] is False
    assert proof["full_model_admission"] is False
    assert proof["physical_busy"] is None
    assert all(
        name in (tmp_path / "prepared" / "typed_fp8.h").read_text()
        for name in ["uint8_t at[51]", "float cp[195]"]
    )
    forbidden = kwargs | {"controller_cflags": flags + ("-march=rv64gcv_zvl256b",)}
    with pytest.raises(ValueError, match="unqualified RVV packing"):
        prepare_staged_kernel(
            parse_mlir_text(SOURCE), tmp_path / "vector_controller", **forbidden
        )
