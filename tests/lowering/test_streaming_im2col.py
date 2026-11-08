"""Provider opt-in, strict typed arithmetic, window/tail and dispatch tests."""

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stream(monkeypatch):
    from merlin.targetgen.plugins import load_module
    from merlin.targetgen.provider_compose import resolve_base
    from merlin.targetgen.providers import read_provider

    base = resolve_base(read_provider(ROOT))
    monkeypatch.setenv("MERLIN_TARGET_PATH", f"{ROOT}:{base.root}")
    return load_module(ROOT, "src/iris/transforms/streaming.py", package_name="iris")


def source(layout="NHWC", *, batch=2, oh=3, ow=7, sh=2, sw=1, dh=1, dw=2):
    h, w = (oh - 1) * sh + 2 * dh + 1, (ow - 1) * sw + 2 * dw + 1
    if layout == "NHWC":
        a, b, c = [batch, h, w, 2], [3, 3, 2, 32], [batch, oh, ow, 32]
        maps = f"(d0,d1*{sh}+d4*{dh},d2*{sw}+d5*{dw},d6),(d4,d5,d6,d3),(d0,d1,d2,d3)"
    else:
        a, b, c = [batch, 2, h, w], [32, 2, 3, 3], [batch, 32, oh, ow]
        maps = f"(d0,d4,d2*{sh}+d5*{dh},d3*{sw}+d6*{dw}),(d1,d4,d5,d6),(d0,d1,d2,d3)"

    def tensor(shape, dtype):
        return "tensor<" + "x".join(map(str, shape)) + "x" + dtype + ">"

    ta, tb, tc = tensor(a, "i8"), tensor(b, "i8"), tensor(c, "i32")
    dims = "d0,d1,d2,d3,d4,d5,d6"
    parts = maps.split("),(")
    parts = [p.strip("()") for p in parts]
    m = ",".join(f"affine_map<({dims})->({p})>" for p in parts)
    return f"""builtin.module {{
 func.func @forward(%a:{ta},%b:{tb})->{tc} {{
 %z=arith.constant 0:i32
 %e=tensor.empty():{tc}
 %init=linalg.fill ins(%z:i32) outs(%e:{tc})->{tc}
 %r=linalg.generic {{indexing_maps=[{m}],iterator_types=["parallel","parallel","parallel","parallel","reduction","reduction","reduction"]}}
 ins(%a,%b:{ta},{tb}) outs(%init:{tc}) {{
 ^bb0(%x:i8,%y:i8,%acc:i32):
 %xx=arith.extsi %x:i8 to i32
 %yy=arith.extsi %y:i8 to i32
 %p=arith.muli %xx,%yy:i32
 %s=arith.addi %acc,%p:i32
 linalg.yield %s:i32
 }}->{tc}
 func.return %r:{tc}
 }} }}"""


def select(stream, *, width=4, budget=4096, vlen=256, dlen=128, edge=32):
    return stream.StreamingSelector(
        lambda shape: True,
        stream.StreamingOptions(
            unit="saturn_vme",
            config="IrisConfig",
            vlen_bits=vlen,
            dlen_bits=dlen,
            tile_edge=edge,
            workspace_bytes=budget,
            panel_rows=width,
        ),
    )


@pytest.mark.parametrize("layout", ["NHWC", "NCHW"])
def test_typed_stream_matches_shapes_and_logical_work(stream, layout, tmp_path):
    from merlin.common import mlir_query as mq

    module = mq.parse(source(layout))
    report, schedule = stream.rewrite(
        module, select=select(stream), tile_edge=32, symbol_prefix="test_vme"
    )
    module.verify()
    entry = schedule["entries"][0]
    assert entry["panel_width"] == 4 and entry["row_tail"] == 3
    assert entry["logical_panel_invocations"] == 12
    assert entry["static_call_sites"] == 2
    assert entry["combined_workspace_bound_bytes"] == (4 + 3 + 4) * 18
    assert sum(r.batch * r.m * r.n * r.k for r in report.routed) == 2 * 3 * 7 * 32 * 18
    assert set(report.signatures.values()) == {(4, 32, 18), (3, 32, 18)}
    text = str(module)
    assert "scf.for" in text
    assert "tensor<2x3x7x3x3x2xi8>" not in text
    for op in module.walk():
        if op.name == "linalg.generic":
            assert math.prod(op.results[0].type.get_shape()) <= 4 * 18


def test_budget_geometry_never_infers_dlen_and_refuses_too_small(stream):
    options = select(stream, width=128, budget=3 * 128 * 144).streaming
    assert options.width(k=144, ow=64) == 64  # spatial-row boundary, not flat-M T128
    assert options.to_dict()["dlen_bits"] == 128
    assert options.to_dict()["cache_capacity_bytes"] is None
    assert options.to_dict()["measured_performance_claim"] is False
    assert (
        select(
            stream, width=128, budget=3 * 128 * 144, vlen=512, dlen=128, edge=64
        ).streaming.width(k=144, ow=128)
        == 128
    )
    with pytest.raises(ValueError, match="one row"):
        select(stream, budget=2).streaming.width(k=18, ow=7)


@pytest.mark.parametrize(
    "old,new",
    [
        ("arith.addi %acc,%p", "arith.addi %p,%acc"),
        ("arith.constant 0:i32", "arith.constant 1:i32"),
        ("arith.extsi %x", "arith.extui %x"),
    ],
)
def test_exact_dag_and_zero_accumulator(stream, old, new):
    from merlin.common import mlir_query as mq
    from merlin.llvmlower.contraction_normal_form import analyze

    module = mq.parse(source().replace(old, new))
    op = next(o for o in module.walk() if o.name == "linalg.generic")
    if "addi" in new:
        assert stream._signed_zero_mac(op)
    else:
        assert not stream._signed_zero_mac(op)
    if "constant 1" in new:
        with pytest.raises(ValueError, match="zero-accumulator"):
            stream.rewrite(
                module, select=select(stream), tile_edge=32, symbol_prefix="test_vme"
            )
    if "extui" in new:
        assert isinstance(analyze(op), str)
        with pytest.raises(ValueError, match="unsigned window"):
            stream.rewrite(
                module, select=select(stream), tile_edge=32, symbol_prefix="test_vme"
            )


def test_file_sidecar_access_and_default_path(stream, tmp_path):
    from merlin.targetgen.plugins import load_module

    contraction = load_module(
        ROOT, "src/iris/transforms/contraction.py", package_name="iris"
    )
    path = tmp_path / "model.mlir"
    path.write_text(source())
    result = contraction.rewrite(path, tmp_path, select=select(stream), tile_edge=32)
    sidecar = json.loads((tmp_path / "vme_signatures.json").read_text())
    assert sidecar["streaming_im2col"]["source_contractions"] == 1
    assert result.count == 12
    assert path.read_text().count('bufferization.access = "write"') == 2
    ordinary = tmp_path / "ordinary.mlir"
    ordinary.write_text(source())
    contraction.rewrite(
        ordinary, tmp_path / "ordinary", select=lambda s: True, tile_edge=32
    )
    assert "scf.for" not in ordinary.read_text()
    assert (
        "streaming_im2col"
        not in (tmp_path / "ordinary/vme_signatures.json").read_text()
    )


@pytest.mark.parametrize("operation", ["muli", "addi"])
@pytest.mark.parametrize("flag", ["nsw", "nuw"])
def test_nonwrapping_source_arithmetic_is_refused(stream, operation, flag):
    from merlin.common import mlir_query as mq

    old = f"arith.{operation} " + (
        "%xx,%yy:i32" if operation == "muli" else "%acc,%p:i32"
    )
    new = old.replace(":i32", f" overflow<{flag}>:i32")
    module = mq.parse(source().replace(old, new))
    with pytest.raises(ValueError, match="exact signed I8/I32"):
        stream.rewrite(
            module, select=select(stream), tile_edge=32, symbol_prefix="test_vme"
        )


def test_public_selector_checks_derived_vlen_independently(stream, monkeypatch):
    from types import SimpleNamespace

    from merlin.targetgen.plugins import load_module

    backend = load_module(ROOT, "src/iris/backend.py", package_name="iris")
    monkeypatch.setattr(
        backend,
        "load_contract",
        lambda unit: SimpleNamespace(
            vector_params=lambda config: {"vLen": 512, "dLen": 128},
            geometry=lambda config: (64, 16),
        ),
    )
    with pytest.raises(ValueError, match="VLEN disagrees"):
        backend.streaming_selector(
            unit="u", config="c", workspace_bytes=4096, vlen_bits=256
        )
    selected = backend.streaming_selector(
        unit="u", config="c", workspace_bytes=4096, vlen_bits=512
    )
    assert selected.streaming.dlen_bits == 128
    assert selected.streaming.tile_edge == 64


def test_build_binds_schedule_and_refuses_unproved_parallel(
    stream, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from merlin.targetgen.plugins import load_module

    source_path = tmp_path / "model.mlir"
    source_path.write_text(source())
    result = stream.rewrite_prepared_file(
        source_path,
        tmp_path,
        select=select(stream),
        tile_edge=32,
        symbol_prefix="test_vme",
        sidecar_name="vme_signatures.json",
    )
    shim = load_module(ROOT, "src/iris/lower/vme_shim.py", package_name="iris")
    monkeypatch.setattr(
        shim,
        "load_contract",
        lambda unit: SimpleNamespace(
            vector_params=lambda config: {"vLen": 256, "dLen": 128},
            geometry=lambda config: (32, 16),
        ),
    )
    kwargs = {"signatures": result.signatures, "unit": "saturn_vme", "config": "IrisConfig"}
    bound = stream.build_schedule_provenance(
        tmp_path / "matrix", parallel_tiles=False, **kwargs
    )
    assert bound["sha256"] and bound["hardware_performance_certified"] is False
    with pytest.raises(ValueError, match="one compute hart"):
        stream.build_schedule_provenance(
            tmp_path / "matrix", parallel_tiles=True, **kwargs
        )
    with pytest.raises(ValueError, match="signatures differ"):
        stream.build_schedule_provenance(
            tmp_path / "matrix", parallel_tiles=False, **{**kwargs, "signatures": {}}
        )
