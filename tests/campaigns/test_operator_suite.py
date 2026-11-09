import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
S = importlib.util.spec_from_file_location(
    "operator_suite", ROOT / "tools/campaigns/operator_suite.py"
)
G = importlib.util.module_from_spec(S)
S.loader.exec_module(G)


def gemm(**updates):
    return dict(
        {"id": "tail", "kind": "gemm", "split": "smoke", "m": 3, "n": 65, "k": 7},
        **updates,
    )


def conv(**updates):
    return dict(
        {
            "id": "conv",
            "kind": "conv2d",
            "split": "fit",
            "batch": 2,
            "ci": 2,
            "co": 32,
            "ih": 6,
            "iw": 8,
            "kh": 3,
            "kw": 3,
            "sh": 2,
            "dw": 2,
            "pt": 1,
            "pl": 2,
            "pr": 1,
        },
        **updates,
    )


def test_pair_sources_have_identical_public_layout_and_numeric_contract(tmp_path):
    spec = {
        "schema": "iris.compiled_operator_suite.v1",
        "target": {"vlen_bits": 256, "dlen_bits": 128, "compute_harts": [0]},
        "variants": ["rvv", "vme_full", "vme_prepacked"],
        "max_logical_bytes_per_case": 100000,
        "cases": [gemm()],
    }
    result = G.emit(spec, tmp_path / "new")
    row = result["cases"][0]
    assert row["facts"]["output_shape"] == [3, 65]
    assert len({v["authored_source_sha256"] for v in row["variants"]}) == 1
    assert result["execution_admission_required"] and not result["native_validated"]
    with pytest.raises(FileExistsError):
        G.emit(spec, tmp_path / "new")


@pytest.mark.parametrize(
    "field,value",
    [("m", True), ("k", 0), ("n", -1), ("id", "../bad"), ("split", "unknown")],
)
def test_invalid_case_is_refused_before_output(field, value):
    case = gemm()
    case[field] = value
    with pytest.raises(ValueError):
        G.checked_case(case)


def test_structural_padding_work_is_not_credited_as_useful_compute():
    c, f = G.checked_case(conv())
    assert (c["oh"], c["ow"]) == (3, 7)
    assert f["dense_MACs"] == 2 * 3 * 7 * 32 * 18
    assert 0 < f["nonpadding_MACs"] < f["dense_MACs"]
    assert f["structural_padding_MACs"] == f["dense_MACs"] - f["nonpadding_MACs"]


def test_fast_padding_work_formula_matches_independent_enumeration():
    rng = random.Random(721)
    for _ in range(2000):
        o, k, st, di, pa, n = [rng.randint(1, 30) for _ in range(6)]
        expected = sum(
            0 <= y * st + q * di - pa < n for y in range(o) for q in range(k)
        )
        assert G._valid_pairs(o, k, st, di, pa, n) == expected
    assert G._valid_pairs(1 << 40, 3, 1, 1, 1, 1 << 40) > 0


@pytest.mark.parametrize(
    "case",
    [
        gemm(),
        gemm(kind="attention_qk", rhs_layout="n_k"),
        gemm(kind="attention_av"),
        conv(),
        conv(layout="NHWC"),
        conv(pt=0, pl=0, pr=0, ih=7, iw=11),
    ],
)
def test_emitted_mlir_parses_and_verifies(case):
    from merlin.common import mlir_query as mq

    text, _c, f = G.source(case)
    mq.parse(text).verify()
    assert "arith.extsi" in text and "llvm.emit_c_interface" in text
    assert f["output_dtype"] == "i32"


def test_padding_intermediate_overflow_is_refused():
    with pytest.raises(ValueError):
        G.checked_case(conv(ih=1, iw=1, pt=(1 << 63) - 1))
