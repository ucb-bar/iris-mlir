"""Explicit finite E4M3FN source proof and controller/compute provider.

The source must already contain standard FP8 tensors. Ordinary FP32 arithmetic
is never silently requantized, and no model/FQN/shape-driven route is installed.
Every finite E4M3FN product is exactly representable in FP32 (at most eight
significand bits, magnitude between 2^-18 and 448^2 for nonzero products), so
ordered separate FP32 multiply/add and ordered FMA agree under RNE, including
signed zero and sticky inexact behavior. NaN inputs and non-RNE compute contexts
are explicitly refused at runtime. Scale and bias are separate worker arithmetic.
"""

from __future__ import annotations

import math


def admit_fp8_gemm(module):
    """Prove positive-zero, unflagged, direct E4M3FN→FP32 canonical MAC."""
    from merlin.llvmlower.deployable_outline import static_tensor_contract
    from xdsl.dialects.arith import FastMathFlagsAttr
    from xdsl.dialects.builtin import Float32Type, FloatAttr, NoneAttr, TensorType
    from xdsl.ir.affine import AffineDimExpr, AffineMap

    module.verify()
    if any(not key.startswith("prov.") for key in module.attributes):
        raise ValueError("unproved FP8 source module contract")
    functions = [op for op in module.body.block.ops if op.name == "func.func"]
    if len(functions) != 1 or len(functions[0].body.blocks) != 1:
        raise ValueError("one closed FP8 tensor kernel required")
    fn = functions[0]
    if fn.arg_attrs is not None or fn.res_attrs is not None:
        raise ValueError("unproved FP8 source argument/result contract")
    inputs, outputs = list(fn.function_type.inputs), list(fn.function_type.outputs)
    if len(inputs) != 2 or len(outputs) != 1:
        raise ValueError("two typed FP8 inputs and one FP32 result required")
    if any(not isinstance(t, TensorType) for t in [*inputs, *outputs]):
        raise ValueError("ranked FP8 tensor ABI required")
    if any(t.encoding != NoneAttr() for t in [*inputs, *outputs]):
        raise ValueError("only canonical unencoded dense FP8 tensors are admitted")
    a, b = [static_tensor_contract(t) for t in inputs]
    c = static_tensor_contract(outputs[0])
    if (
        a[1] != "f8E4M3FN"
        or b[1] != "f8E4M3FN"
        or c[1] != "f32"
        or any(len(x[0]) != 2 for x in (a, b, c))
    ):
        raise ValueError("rank-two E4M3FN×E4M3FN→FP32 source required")
    m, k = a[0]
    kk, n = b[0]
    if k != kk or c[0] != (m, n):
        raise ValueError("canonical A[M,K], B[K,N], C[M,N] shapes required")
    allowed = {
        "func.func",
        "func.return",
        "tensor.empty",
        "arith.constant",
        "linalg.fill",
        "linalg.generic",
        "arith.extf",
        "arith.mulf",
        "arith.addf",
        "linalg.yield",
    }
    for op in module.walk():
        if op is module:
            continue
        if op.name not in allowed:
            raise ValueError("unsupported closed FP8 operation " + op.name)
        if any(
            not key.startswith("prov.")
            for key in op.attributes
            if key != "llvm.emit_c_interface"
        ):
            raise ValueError("unproved FP8 operation contract")
        if op.name in ("arith.mulf", "arith.addf") and op.properties != {
            "fastmath": FastMathFlagsAttr("none")
        }:
            raise ValueError("unflagged ordered FP32 arithmetic required")
        if op.name == "arith.extf" and op.properties:
            raise ValueError("unflagged exact FP8 widening required")
        property_keys = {
            "func.func": {"sym_name", "function_type", "sym_visibility"},
            "func.return": set(),
            "tensor.empty": set(),
            "arith.constant": {"value"},
            "linalg.fill": {"operandSegmentSizes"},
            "linalg.yield": set(),
        }
        if op.name in property_keys and set(op.properties) - property_keys[op.name]:
            raise ValueError("unproved FP8 source operation property")
    roots = [op for op in fn.body.block.ops if op.name == "linalg.generic"]
    if len(roots) != 1:
        raise ValueError("one FP8 contraction required")
    op = roots[0]
    d0, d1, d2 = (AffineDimExpr(i) for i in range(3))
    maps = (
        AffineMap(3, 0, (d0, d2)),
        AffineMap(3, 0, (d2, d1)),
        AffineMap(3, 0, (d0, d1)),
    )
    if tuple(x.data for x in op.indexing_maps) != maps or tuple(
        x.data.value for x in op.iterator_types
    ) != ("parallel", "parallel", "reduction"):
        raise ValueError("canonical ordered K reduction maps required")
    if set(op.properties) != {"indexing_maps", "iterator_types", "operandSegmentSizes"}:
        raise ValueError("unproved FP8 contraction implementation property")
    if (
        tuple(op.inputs) != tuple(fn.body.block.args)
        or len(op.outputs) != 1
        or len(op.results) != 1
    ):
        raise ValueError("direct FP8 tensor operands required")
    body = op.body.block
    ops = list(body.ops)
    if [v.name for v in ops] != [
        "arith.extf",
        "arith.extf",
        "arith.mulf",
        "arith.addf",
        "linalg.yield",
    ]:
        raise ValueError("exact widening/product/accumulation scalar body required")
    x, y, mul, add, yield_ = ops
    if (
        tuple(x.operands) != (body.args[0],)
        or tuple(y.operands) != (body.args[1],)
        or not isinstance(x.results[0].type, Float32Type)
        or not isinstance(y.results[0].type, Float32Type)
        or set(mul.operands) != {x.results[0], y.results[0]}
        or set(add.operands) != {mul.results[0], body.args[2]}
        or tuple(yield_.operands) != (add.results[0],)
    ):
        raise ValueError(
            "source scalar dataflow is not ordered FP32 acc+exactFP8product"
        )
    fill = op.outputs[0].owner
    if fill.name != "linalg.fill" or len(fill.inputs) != 1 or len(fill.outputs) != 1:
        raise ValueError("fresh positive-zero FP32 seed required")
    zero, empty = fill.inputs[0].owner, fill.outputs[0].owner
    value = zero.properties.get("value") if zero.name == "arith.constant" else None
    if (
        not isinstance(value, FloatAttr)
        or not isinstance(value.type, Float32Type)
        or value.value.data != 0.0
        or math.copysign(1.0, value.value.data) < 0
        or empty.name != "tensor.empty"
        or empty.operands
    ):
        raise ValueError(
            "positive-zero FP32 bitpattern and fresh static accumulator required"
        )
    seed_body = fill.body.block
    seed_ops = list(seed_body.ops)
    if (
        len(seed_body.args) != 2
        or len(seed_ops) != 1
        or seed_ops[0].name != "linalg.yield"
        or tuple(seed_ops[0].operands) != (seed_body.args[0],)
    ):
        raise ValueError("fill must actually yield the positive-zero source value")
    top = list(fn.body.block.ops)
    if (
        set(top) != {empty, zero, fill, op, top[-1]}
        or top[-1].name != "func.return"
        or tuple(top[-1].operands) != (op.results[0],)
    ):
        raise ValueError("FP8 source has extra live/control/effect operations")
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
):
    """Explicit selection only; no integer/integrated/FP32 quantizer fallback."""
    from .outlined_vme import _prepare_staged_kernel

    return _prepare_staged_kernel(
        module,
        work,
        unit=unit,
        config=config,
        roles=roles,
        worker_slots=worker_slots,
        cc=cc,
        controller_cflags=controller_cflags,
        compute_cflags=compute_cflags,
        runtime_include=runtime_include,
        prefix=prefix,
        _format="finite_e4m3_f32",
    )
