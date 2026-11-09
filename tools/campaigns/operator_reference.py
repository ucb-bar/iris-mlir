"""Independent signed integer reference and explicit row-major ABI witnesses."""

from __future__ import annotations

import math


def inputs(facts, seed):
    import numpy as np

    result = []
    for index, shape in enumerate(facts["input_shapes"]):
        n = math.prod(shape)
        values = (
            (np.arange(n, dtype=np.int64) * (29 + index * 8) + seed + index * 11) & 255
        ) - 128
        result.append(values.astype(np.int8).reshape(shape))
    return result


def reference(case, arrays):
    import numpy as np

    a, b = arrays
    if case["kind"] != "conv2d":
        right = b if case["rhs_layout"] == "k_n" else b.T
        return (a.astype(np.int64) @ right.astype(np.int64)).astype(np.int32)
    if case["layout"] == "NCHW":
        a = a.transpose(0, 2, 3, 1)
        b = b.transpose(2, 3, 1, 0)
    result = np.zeros(
        (case["batch"], case["oh"], case["ow"], case["co"]), dtype=np.int64
    )
    for y in range(case["oh"]):
        for x in range(case["ow"]):
            for ky in range(case["kh"]):
                iy = y * case["sh"] + ky * case["dh"] - case["pt"]
                for kx in range(case["kw"]):
                    ix = x * case["sw"] + kx * case["dw"] - case["pl"]
                    if 0 <= iy < a.shape[1] and 0 <= ix < a.shape[2]:
                        result[:, y, x, :] += a[:, iy, ix, :].astype(np.int64) @ b[
                            ky, kx
                        ].astype(np.int64)
    result = result.astype(np.int32)
    return result.transpose(0, 3, 1, 2).copy() if case["layout"] == "NCHW" else result


def flat_wrapper(facts, symbol="forward"):
    """For a verified LLVM function with three flattened memrefs and void return.

    Callers must verify the emitted signature; this does not infer a changed ABI.
    Input/output descriptors use explicit row-major extents and strides.
    """
    if not symbol.replace("_", "a").isalnum() or symbol[0].isdigit():
        raise ValueError("C identifier required")
    types = []
    args = []
    for name, shape in zip(
        ["a", "b", "out"], [*facts["input_shapes"], facts["output_shape"]]
    ):
        types += ["void*", "void*", "long"] + ["long"] * (2 * len(shape))
        strides = []
        stride = 1
        for extent in reversed(shape):
            strides.append(stride)
            stride *= extent
        args += [name, name, "0", *map(str, shape), *map(str, reversed(strides))]
    return (
        "extern void " + symbol + "(" + ",".join(types) + ");\n"
        "void run_operator(void*a,void*b,void*out){"
        + symbol
        + "("
        + ",".join(args)
        + ");}\n"
    )
