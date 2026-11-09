"""Direct K-major ABI and explicit static-admission coverage."""

import ctypes
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(path):
    from merlin.targetgen.plugins import load_module

    return load_module(ROOT, path, package_name="iris")


def test_static_admission_retains_full_for_small_fragmented_or_fitting():
    stream = module("src/iris/transforms/streaming.py")
    options = stream.StreamingOptions(
        "saturn_vme",
        "IrisConfig",
        256,
        128,
        32,
        65536,
        prepacked_a=True,
        admission="workspace_pressure",
    )
    small = SimpleNamespace(m=42, k=18, n=2, oh=3, ow=7)
    assert not options.admit(spec=small, n=32, width=7)["admitted"]
    assert (
        options.width(k=144, ow=256) == 224
    )  # budget/2K, rounded to TE, not a universal T128
    regular = SimpleNamespace(m=4096, k=144, n=1, oh=64, ow=64)
    accepted = options.admit(spec=regular, n=64, width=64)
    assert (
        accepted["admitted"]
        and accepted["basis"] == "static_cost_proxy_not_measured_latency"
    )
    fragmented = SimpleNamespace(m=64 * 67, k=144, n=1, oh=64, ow=67)
    assert not options.admit(spec=fragmented, n=64, width=64)["admitted"]
    assert options.to_dict()["dlen_bits"] == 128
    huge = SimpleNamespace(m=(1 << 60) + 1, k=1, n=1, oh=1, ow=(1 << 60) + 1)
    assert (
        options.admit(spec=huge, n=32, width=32)["full_abstract_TE_tiles"]
        == ((1 << 60) + 32) // 32
    )


@pytest.mark.parametrize(
    "shape",
    [
        (1.0, 7, 9),
        (True, 7, 9),
        ("5", 7, 9),
        (0, 7, 9),
        (-5, 7, 9),
        (5, 7),
        (5, 7, 9, 1),
    ],
)
def test_prepacked_shape_witness_refuses_coercions(shape):
    shim = module("src/iris/lower/vme_shim.py")
    with pytest.raises(ValueError, match="three positive integers"):
        shim._prepacked_signatures({"entry": (5, 7, 9)}, {"entry": shape})
    assert shim._prepacked_signatures({"entry": (5, 7, 9)}, {"entry": [5, 7, 9]}) == {
        "entry": (5, 7, 9)
    }


class Memref(ctypes.Structure):
    _fields_ = [
        ("allocated", ctypes.c_void_p),
        ("aligned", ctypes.c_void_p),
        ("offset", ctypes.c_ssize_t),
        ("sizes", ctypes.c_ssize_t * 2),
        ("strides", ctypes.c_ssize_t * 2),
    ]


def test_prepacked_entry_exact_dense_strided_offsets_and_guards(tmp_path):
    shim = module("src/iris/lower/prepacked_shim.py")
    kernel = """static void gemm(int32_t*c,const int8_t*a,const int8_t*b,const int32_t*bias,size_t m,size_t n,size_t k){
      (void)bias; for(size_t i=0;i<m;i++)for(size_t j=0;j<n;j++){uint32_t s=0;for(size_t q=0;q<k;q++)s+=(uint32_t)((int32_t)a[q*m+i]*(int32_t)b[q*n+j]);c[i*n+j]=(int32_t)s;}}
    """
    text = shim.emit(
        kernel, "gemm", {"entry": (5, 7, 9)}, prefix="test", alignment_bytes=16
    )
    assert text.scratch_bytes == 0 and "lhs_pack" not in text and "pack_BLK" not in text
    src = tmp_path / "shim.c"
    src.write_text(text)
    so = tmp_path / "shim.so"
    subprocess.run(
        ["cc", "-O2", "-shared", "-fPIC", str(src), "-o", str(so)],
        check=True,
        capture_output=True,
    )
    lib = ctypes.CDLL(str(so))
    entry = lib.entry
    entry.restype = Memref
    entry.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *([ctypes.c_ssize_t] * 5)] * 3
    rng = np.random.default_rng(0)
    for strided in (False, True):
        ast = (13, 2) if strided else (5, 1)
        bst = (19, 2) if strided else (7, 1)
        cst = (23, 3) if strided else (7, 1)
        a = np.full(180, -99, np.int8)
        b = np.full(220, -99, np.int8)
        c = np.full(180, 0x12345678, np.int32)
        av = rng.integers(-128, 128, (9, 5), dtype=np.int8)
        bv = rng.integers(-128, 128, (9, 7), dtype=np.int8)
        oa, ob, oc = (3, 5, 7) if strided else (0, 0, 0)
        for q in range(9):
            for i in range(5):
                a[oa + q * ast[0] + i * ast[1]] = av[q, i]
            for j in range(7):
                b[ob + q * bst[0] + j * bst[1]] = bv[q, j]
        before = c.copy()
        ret = entry(
            a.ctypes.data,
            a.ctypes.data,
            oa,
            9,
            5,
            *ast,
            b.ctypes.data,
            b.ctypes.data,
            ob,
            9,
            7,
            *bst,
            c.ctypes.data,
            c.ctypes.data,
            oc,
            5,
            7,
            *cst,
        )
        expected = av.astype(np.int32).T @ bv.astype(np.int32)
        indices = []
        for i in range(5):
            for j in range(7):
                at = oc + i * cst[0] + j * cst[1]
                indices.append(at)
                assert c[at] == expected[i, j]
        assert np.array_equal(np.delete(c, indices), np.delete(before, indices))
        assert ret.aligned == c.ctypes.data and ret.offset == oc


def test_prepacked_module_has_transposed_input_and_no_packer(monkeypatch):
    from merlin.common import mlir_query as mq
    from merlin.targetgen.plugins import load_module
    from merlin.targetgen.provider_compose import resolve_base
    from merlin.targetgen.providers import read_provider

    tests = load_module(
        ROOT, "tests/lowering/test_streaming_im2col.py", package_name="iris"
    )
    base = resolve_base(read_provider(ROOT))
    monkeypatch.setenv("MERLIN_TARGET_PATH", f"{ROOT}:{base.root}")
    stream = module("src/iris/transforms/streaming.py")
    opts = stream.StreamingOptions(
        "u", "c", 256, 128, 32, 4096, panel_rows=4, prepacked_a=True
    )
    ir = mq.parse(tests.source())
    report, schedule = stream.rewrite(
        ir,
        select=stream.StreamingSelector(lambda s: True, opts),
        tile_edge=32,
        symbol_prefix="vme",
    )
    ir.verify()
    assert schedule["entries"][0]["shim_packed_A_bytes"] == 0
    assert schedule["entries"][0]["extra_A_to_At_copy"] is False
    assert set(map(tuple, schedule["prepacked_signatures"].values())) == {
        (4, 32, 18),
        (3, 32, 18),
    }
    calls = [
        o
        for o in ir.walk()
        if o.name == "func.call" and o.callee.root_reference.data.endswith("_tensor")
    ]
    assert {tuple(o.operands[0].type.get_shape()) for o in calls} == {(18, 4), (18, 3)}
    assert (
        sum(math.prod((*r.parallel, *r.reduction)) for r in report.routed)
        == 42 * 32 * 18
    )
