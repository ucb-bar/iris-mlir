"""Placement of eligible int8 contractions on Iris's VME tiles."""

from __future__ import annotations


def selector(tile_edge: int):
    """Select contractions large enough to fill an RTL-derived VME tile.

    The shared selector owns the shape/layout test. Iris supplies the tile edge
    derived from the selected unit rather than inventing one in this policy.
    """
    if not isinstance(tile_edge, int) or tile_edge <= 0:
        raise ValueError("Iris VME tile edge must be a positive integer")
    from merlin.compiler.lower.model import int8_contractions

    return int8_contractions.tile_filling_selector(tile_edge)
