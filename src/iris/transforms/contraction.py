"""Rewrite selected contractions to the Iris VME shim ABI."""

from __future__ import annotations

from ..lower import vme_shim


def rewrite(prepared, work, *, select, tile_edge):
    from merlin.llvmlower import int8_contractions

    from .streaming import StreamingSelector

    if isinstance(select, StreamingSelector):
        from .streaming import rewrite_prepared_file

        return rewrite_prepared_file(
            prepared,
            work,
            select=select,
            tile_edge=tile_edge,
            symbol_prefix=vme_shim.SYMBOL_PREFIX,
            sidecar_name=vme_shim.SIDECAR_NAME,
        )

    return int8_contractions.rewrite_prepared_file(
        prepared,
        work,
        select=select,
        tile_edge=tile_edge,
        symbol_prefix=vme_shim.SYMBOL_PREFIX,
        sidecar_name=vme_shim.SIDECAR_NAME,
    )
