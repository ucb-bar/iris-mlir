#!/usr/bin/env python3
"""Emit a common typed INT8 graph for paired compiled-operator experiments.

This creates source inputs, not executed measurements or a hardware admission.
Private experiment/model recipes and results stay outside the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

CASE_FIELDS = {
    "id",
    "kind",
    "split",
    "model",
    "part",
    "m",
    "n",
    "k",
    "rhs_layout",
    "batch",
    "ci",
    "co",
    "ih",
    "iw",
    "kh",
    "kw",
    "sh",
    "sw",
    "dh",
    "dw",
    "pt",
    "pb",
    "pl",
    "pr",
    "layout",
    "input_seed",
}
CONV_FIELDS = {
    "batch",
    "ci",
    "co",
    "ih",
    "iw",
    "kh",
    "kw",
    "sh",
    "sw",
    "dh",
    "dw",
    "pt",
    "pb",
    "pl",
    "pr",
    "layout",
}


def integer(value, name, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= (1 << 63) - 1:
        raise ValueError(f"{name}: explicit bounded integer required")
    return value


def tensor(shape, kind):
    return "tensor<" + "x".join(map(str, shape)) + "x" + kind + ">"


def _floor_sum(n, m, a, b):
    qa, a = divmod(a, m)
    qb, b = divmod(b, m)
    total = qa * n * (n - 1) // 2 + qb * n
    while True:
        qa, a = divmod(a, m)
        qb, b = divmod(b, m)
        total += qa * n * (n - 1) // 2 + qb * n
        top = a * n + b
        if top < m:
            return total
        n, b = divmod(top, m)
        m, a = a, m


def _valid_pairs(outputs, kernel, stride, dilation, padding, inputs):
    def below(bound):
        if bound < 0:
            return 0
        full = min(outputs, max(0, (bound - (kernel - 1) * dilation) // stride + 1))
        stop = min(outputs, bound // stride + 1)
        count = stop - full
        return (
            full * kernel
            + count
            + _floor_sum(count, dilation, -stride, bound - full * stride)
        )

    return below(padding + inputs - 1) - below(padding - 1)


def checked_case(raw):
    if not isinstance(raw, dict) or set(raw) - CASE_FIELDS:
        raise ValueError("unsupported case fields")
    c = dict(raw)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", c.get("id", "")):
        raise ValueError("safe unique case id required")
    if c.get("split") not in ("fit", "holdout", "smoke"):
        raise ValueError("declare fit, holdout or smoke before collecting measurements")
    if c.get("kind") not in ("gemm", "attention_qk", "attention_av", "conv2d"):
        raise ValueError("unsupported operator kind")
    for name in ("model", "part"):
        if name in c and (not isinstance(c[name], str) or len(c[name]) > 512):
            raise ValueError("bounded text labels required")
    c["input_seed"] = integer(c.get("input_seed", 1), "input_seed", zero=True)
    if c["kind"] != "conv2d":
        if set(c) & CONV_FIELDS:
            raise ValueError("convolution geometry cannot decorate a matrix case")
        for name in ("m", "n", "k"):
            c[name] = integer(c.get(name), name)
        c.setdefault("rhs_layout", "n_k" if c["kind"] == "attention_qk" else "k_n")
        if c["rhs_layout"] not in ("k_n", "n_k"):
            raise ValueError("explicit RHS layout must be k_n or n_k")
        a, b, out = (
            [c["m"], c["k"]],
            ([c["k"], c["n"]] if c["rhs_layout"] == "k_n" else [c["n"], c["k"]]),
            [c["m"], c["n"]],
        )
        valid = dense = c["m"] * c["n"] * c["k"]
        scope = "raw signed-INT8 matrix stage; no softmax, masking, scaling, quantization or epilogue included"
    else:
        if set(c) & {"m", "n", "k", "rhs_layout"}:
            raise ValueError("derive matrix dimensions from convolution geometry")
        for name in ("batch", "ci", "co", "ih", "iw", "kh", "kw"):
            c[name] = integer(c.get(name), name)
        for name in ("sh", "sw", "dh", "dw"):
            c[name] = integer(c.get(name, 1), name)
        for name in ("pt", "pb", "pl", "pr"):
            c[name] = integer(c.get(name, 0), name, zero=True)
        c.setdefault("layout", "NCHW")
        if c["layout"] not in ("NCHW", "NHWC"):
            raise ValueError("NCHW or NHWC required")
        padded_h = integer(c["ih"] + c["pt"] + c["pb"], "padded height")
        padded_w = integer(c["iw"] + c["pl"] + c["pr"], "padded width")
        integer(c["batch"] * c["ci"] * padded_h * padded_w, "padded tensor bytes")
        integer((c["kh"] - 1) * c["dh"] + 1, "effective kernel height")
        integer((c["kw"] - 1) * c["dw"] + 1, "effective kernel width")
        oh = (c["ih"] + c["pt"] + c["pb"] - (c["kh"] - 1) * c["dh"] - 1) // c["sh"] + 1
        ow = (c["iw"] + c["pl"] + c["pr"] - (c["kw"] - 1) * c["dw"] - 1) // c["sw"] + 1
        integer(oh, "output height")
        integer(ow, "output width")
        c.update(
            oh=oh,
            ow=ow,
            m=c["batch"] * oh * ow,
            n=c["co"],
            k=c["ci"] * c["kh"] * c["kw"],
        )
        integer(
            (oh - 1) * c["sh"] + (c["kh"] - 1) * c["dh"], "input row index", zero=True
        )
        integer(
            (ow - 1) * c["sw"] + (c["kw"] - 1) * c["dw"],
            "input column index",
            zero=True,
        )
        if c["layout"] == "NCHW":
            a, b, out = (
                [c["batch"], c["ci"], c["ih"], c["iw"]],
                [c["co"], c["ci"], c["kh"], c["kw"]],
                [c["batch"], c["co"], oh, ow],
            )
        else:
            a, b, out = (
                [c["batch"], c["ih"], c["iw"], c["ci"]],
                [c["kh"], c["kw"], c["ci"], c["co"]],
                [c["batch"], oh, ow, c["co"]],
            )
        # Count structural padding separately from non-padding algorithmic work.
        valid_h = _valid_pairs(oh, c["kh"], c["sh"], c["dh"], c["pt"], c["ih"])
        valid_w = _valid_pairs(ow, c["kw"], c["sw"], c["dw"], c["pl"], c["iw"])
        valid = c["batch"] * c["ci"] * c["co"] * valid_h * valid_w
        dense = c["m"] * c["n"] * c["k"]
        scope = "raw signed-INT8 convolution including explicit zero padding; no quantization or epilogue included"
    for extent in [*a, *b, *out]:
        integer(extent, "tensor extent")
    logical = math.prod(a) + math.prod(b) + 4 * math.prod(out)
    if logical > (1 << 63) - 1 or dense > (1 << 63) - 1:
        raise ValueError("work or logical tensor size exceeds signed index domain")
    return c, {
        "input_shapes": [a, b],
        "output_shape": out,
        "input_dtypes": ["i8", "i8"],
        "output_dtype": "i32",
        "dense_MACs": dense,
        "nonpadding_MACs": valid,
        "structural_padding_MACs": dense - valid,
        "logical_input_output_bytes": logical,
        "physical_traffic_bytes": None,
        "allocation_peak_bytes": None,
        "scope": scope,
    }


def source(raw):
    c, facts = checked_case(raw)
    a, b = facts["input_shapes"]
    out = facts["output_shape"]
    ta, tb, tc = tensor(a, "i8"), tensor(b, "i8"), tensor(out, "i32")
    pad, left, tleft = "", "%a", ta
    if c["kind"] != "conv2d":
        dims = "d0,d1,d2"
        rhs = "(d2,d1)" if c["rhs_layout"] == "k_n" else "(d1,d2)"
        maps = [
            f"affine_map<({dims})->(d0,d2)>",
            f"affine_map<({dims})->{rhs}>",
            f"affine_map<({dims})->(d0,d1)>",
        ]
        iters = ["parallel", "parallel", "reduction"]
    else:
        dims = "d0,d1,d2,d3,d4,d5,d6"
        if c["layout"] == "NCHW":
            exprs = [
                f"(d0,d4,d2*{c['sh']}+d5*{c['dh']},d3*{c['sw']}+d6*{c['dw']})",
                "(d1,d4,d5,d6)",
                "(d0,d1,d2,d3)",
            ]
            low, high = [0, 0, c["pt"], c["pl"]], [0, 0, c["pb"], c["pr"]]
        else:
            exprs = [
                f"(d0,d1*{c['sh']}+d4*{c['dh']},d2*{c['sw']}+d5*{c['dw']},d6)",
                "(d4,d5,d6,d3)",
                "(d0,d1,d2,d3)",
            ]
            low, high = [0, c["pt"], c["pl"], 0], [0, c["pb"], c["pr"], 0]
        if any(low + high):
            padded = [x + l + h for x, l, h in zip(a, low, high)]
            tleft = tensor(padded, "i8")
            left = "%padded"
            pad = f"""    %pz = arith.constant 0 : i8
    %padded = tensor.pad %a low[{",".join(map(str, low))}] high[{",".join(map(str, high))}] {{
    ^bb0(%i0:index,%i1:index,%i2:index,%i3:index):
      tensor.yield %pz : i8
    }} : {ta} to {tleft}
"""
        maps = [f"affine_map<({dims})->{x}>" for x in exprs]
        iters = ["parallel"] * 4 + ["reduction"] * 3
    text = f"""module {{
  func.func @forward(%a: {ta}, %b: {tb}) -> {tc} attributes {{llvm.emit_c_interface}} {{
{pad}    %zero = arith.constant 0 : i32
    %empty = tensor.empty() : {tc}
    %init = linalg.fill ins(%zero : i32) outs(%empty : {tc}) -> {tc}
    %result = linalg.generic {{indexing_maps=[{",".join(maps)}], iterator_types=[{",".join(json.dumps(x) for x in iters)}]}}
      ins({left},%b : {tleft},{tb}) outs(%init : {tc}) {{
    ^bb0(%x:i8,%y:i8,%acc:i32):
      %xx = arith.extsi %x : i8 to i32
      %yy = arith.extsi %y : i8 to i32
      %p = arith.muli %xx,%yy : i32
      %s = arith.addi %acc,%p : i32
      linalg.yield %s : i32
    }} -> {tc}
    func.return %result : {tc}
  }}
}}
"""
    return text, c, facts


def emit(spec, output):
    if spec.get("schema") != "iris.compiled_operator_suite.v1" or set(spec) - {
        "schema",
        "target",
        "variants",
        "cases",
        "max_logical_bytes_per_case",
    }:
        raise ValueError("explicit compiled suite schema required")
    target = spec["target"]
    if set(target) - {"vlen_bits", "dlen_bits", "compute_harts", "config"}:
        raise ValueError("unsupported target fields")
    vlen = integer(target["vlen_bits"], "VLEN")
    if vlen < 128 or vlen & (vlen - 1):
        raise ValueError("VLEN power of two >=128 required")
    if target.get("dlen_bits") is not None:
        dlen = integer(target["dlen_bits"], "DLEN")
        if dlen % 8:
            raise ValueError("DLEN is an independent whole-byte declaration")
    if target.get("compute_harts") not in ([0], [1], [0, 1]):
        raise ValueError("Iris compute harts are 0 and 1; hart2 is control")
    variants = spec["variants"]
    if (
        not variants
        or len(set(variants)) != len(variants)
        or set(variants) - {"rvv", "vme_full", "vme_prepacked"}
    ):
        raise ValueError("distinct supported backend variants required")
    budget = integer(spec["max_logical_bytes_per_case"], "logical payload budget")
    checked, seen = [], set()
    for raw in spec["cases"]:
        text, c, f = source(raw)
        if c["id"] in seen:
            raise ValueError("duplicate case id")
        seen.add(c["id"])
        if f["logical_input_output_bytes"] > budget:
            raise ValueError("case exceeds declared logical payload budget")
        checked.append((text, c, f))
    if not checked:
        raise ValueError("nonempty suite required")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": "iris.compiled_operator_suite_sources.v1",
        "target": target,
        "target_facts_verified": False,
        "execution_admission_required": True,
        "split_declarations_only": True,
        "native_validated": False,
        "hardware_executed": False,
        "cases": [],
    }
    for text, c, f in checked:
        p = out / (c["id"] + ".mlir")
        p.write_text(text)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest["cases"].append(
            {
                "case": c,
                "facts": f,
                "source": str(p.resolve()),
                "source_sha256": digest,
                "spec": next(x for x in spec["cases"] if x["id"] == c["id"]),
                "variants": [
                    {"backend": v, "authored_source_sha256": digest} for v in variants
                ],
            }
        )
    (out / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    m = emit(json.loads(a.spec.read_text()), a.output)
    print(
        json.dumps(
            {
                "cases": len(m["cases"]),
                "sources": str(a.output / "sources.json"),
                "hardware_executed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
