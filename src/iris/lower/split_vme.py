"""Explicit controller-packed I8/I32 operator resources; no graph rewrite.

This opt-in ABI separates prepare/finish from compute. It does not install a
dispatcher, change the default matrix provider, or claim whole-model placement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SIZE_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class WorkspacePlan:
    m: int
    n: int
    k: int
    tile_edge: int
    alignment_bytes: int
    worker_mask: int
    a_pack_bytes: int
    b_pack_bytes: int
    c_pack_bytes: int
    block_columns: int
    block_count: int

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "schema": "iris.controller_packed_vme.v1",
            "numeric_contract": "signed_i8_times_signed_i8_wrapping_i32",
            "controller_hart": 2,
            "compute_harts": [h for h in (0, 1) if self.worker_mask & (1 << h)],
            "profile_harts": [h for h in (0, 1) if self.worker_mask & (1 << h)],
            "a_layout": "K_M",
            "b_layout": "K_N",
            "c_layout": "M_N",
            "in_flight_descriptors": 1,
            "compute_has_packing_or_openmp": False,
            "full_model_dispatch_integrated": False,
        }


def workspace_plan(
    m: int,
    n: int,
    k: int,
    *,
    tile_edge: int,
    alignment_bytes: int,
    worker_mask: int,
) -> WorkspacePlan:
    """Admit finite RV64 dense buffers using explicitly supplied unit facts."""
    for name, value in (("m", m), ("n", n), ("k", k), ("tile_edge", tile_edge)):
        if type(value) is not int or not 0 < value <= SIZE_MAX:
            raise ValueError(f"positive RV64 {name} required")
    if k > SIZE_MAX // 2:
        raise ValueError("A gather stride must fit signed RV64")
    if tile_edge & (tile_edge - 1) or tile_edge > SIZE_MAX // 2:
        raise ValueError("power-of-two tile edge with representable supertile required")
    if (
        type(alignment_bytes) is not int
        or alignment_bytes < 4
        or alignment_bytes > SIZE_MAX
        or alignment_bytes & (alignment_bytes - 1)
    ):
        raise ValueError("power-of-two alignment of at least four bytes required")
    if type(worker_mask) is not int or worker_mask not in (1, 2, 3):
        raise ValueError("nonempty subset of compute harts 0/1 required")
    sizes = (m * k, k * n, 4 * m * n)
    if any(size > SIZE_MAX for size in sizes):
        raise ValueError("workspace byte count overflows RV64 size_t")
    edge = 2 * tile_edge
    columns = (n - 1) // edge + 1
    blocks = ((m - 1) // edge + 1) * columns
    return WorkspacePlan(
        m, n, k, tile_edge, alignment_bytes, worker_mask, *sizes, columns, blocks
    )


def from_config(
    m: int, n: int, k: int, *, unit: str, config: str, worker_mask: int
) -> WorkspacePlan:
    """Read geometry from the existing selected, pinned unit contract."""
    from .vme_shim import load_contract

    edge, alignment = load_contract(unit).geometry(config)
    return workspace_plan(
        m, n, k, tile_edge=edge, alignment_bytes=alignment, worker_mask=worker_mask
    )


def source_files() -> dict[str, Path]:
    """Source-only build inputs; saturn_vme.h still comes from RTL derivation."""
    return {
        name: ROOT / "kernels" / name
        for name in (
            "vme_split.h",
            "vme_split_prepare.c",
            "vme_split_compute.c",
            "vme_gemm_2x2_bias.c",
            "vme_api.h",
        )
    }
