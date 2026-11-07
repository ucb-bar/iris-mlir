"""Named Iris additions to the inherited RVV lowering path."""

from __future__ import annotations

from .lower import vme_shim
from .transforms import contraction

VME_W8A8_PIPELINE = "iris.vme.w8a8"


def rewrite_prepared_file(prepared, work, *, select, tile_edge):
    """Apply the VME route before the shared RVV fallback lowers remaining ops."""
    return contraction.rewrite(prepared, work, select=select, tile_edge=tile_edge)


def load_signatures(work):
    from merlin.llvmlower import int8_contractions

    return int8_contractions.load_sidecar(work, vme_shim.SIDECAR_NAME)
