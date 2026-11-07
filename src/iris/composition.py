"""Iris's checked VME extension over the pinned RVV component."""

from pathlib import Path

from merlin.targetgen.component_composition import validate_backend_composition
from merlin.targetgen.extension_offers import MatrixExtension

API_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]


def bind(routing):
    validate_backend_composition(ROOT)
    if routing.support_target != "iris":
        raise ValueError(f"Iris extension cannot bind target {routing.support_target!r}")
    return MatrixExtension(routing)
