"""The Iris route is an explicit addition to the pinned RVV component."""

from pathlib import Path

from merlin.targetgen.component_composition import validate_backend_composition

ROOT = Path(__file__).resolve().parents[2]


def test_vme_ownership_is_declared_and_composed_against_pinned_rvv():
    composition = validate_backend_composition(ROOT)
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
