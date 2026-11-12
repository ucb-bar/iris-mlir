"""The Iris route is an explicit addition to the pinned RVV component."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from merlin.compiler.backend.composition import validate_backend_composition

ROOT = Path(__file__).resolve().parents[2]


def test_vme_ownership_is_declared_and_composed_against_pinned_rvv():
    composition = validate_backend_composition(ROOT, bases=[ROOT / "base/rvv-mlir"])
    assert (composition.target, composition.base_target) == ("iris", "rvv")
    assert composition.plugin_owners == {
        "rvv_codegen": "rvv",
        "matrix_lowering": "iris",
        "extension": "iris",
    }
    assert composition.operations == (
        {
            "action": "add",
            "owner": "iris",
            "name": "iris.vme.w8a8",
            "unit": "saturn_vme",
            "pipeline": "iris.vme.w8a8",
        },
    )


def test_extension_preserves_selection_lowering_and_unknown_cost(monkeypatch, tmp_path):
    from merlin.development.targetgen.plugins import load_module

    extension = load_module(ROOT, "extension.py", package_name="iris_offer_contract")
    calls = []
    provider = SimpleNamespace(rewrite_prepared_file=lambda *args, **kwargs: (
        calls.append((args, kwargs)) or "lowered"
    ))
    routing = SimpleNamespace(
        support_target="iris", unit="saturn_vme",
        provider=lambda: provider,
        selector=lambda: lambda op: op == "accepted",
        tile_edge=lambda: 32,
    )
    bound = extension.bind(routing)
    assert bound.offers("refused") == ()
    offer, = bound.offers("accepted")
    assert (offer.owner, offer.unit, offer.estimated_cycles) == ("iris", "saturn_vme", None)
    assert bound.lower("prepared", tmp_path) == "lowered"
    assert calls == [(("prepared", tmp_path), {"select": bound, "tile_edge": 32})]
    routing.support_target = "wrong"
    with pytest.raises(ValueError, match="cannot bind"):
        extension.bind(routing)
