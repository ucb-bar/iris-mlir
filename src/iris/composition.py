"""Iris's checked VME extension over the pinned RVV component."""

from pathlib import Path

from merlin.compiler.backend.composition import validate_backend_composition
from merlin.compiler.backend.proposal import MatrixExtension

API_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]


def bind(routing):
    validate_backend_composition(ROOT, bases=[ROOT / "base/rvv-mlir"])
    if routing.support_target != "iris":
        raise ValueError(f"Iris extension cannot bind target {routing.support_target!r}")
    return MatrixExtension(routing)
