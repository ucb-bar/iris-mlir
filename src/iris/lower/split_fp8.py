"""Explicit finite E4M3FN/FP32 split resources; no numeric input conversion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from . import split_vme

ROOT = Path(__file__).resolve().parents[3]


def additional_sources(contract):
    """Byte-bound FP8 format/cell/sequencer evidence, separate from ISA decode."""
    import yaml

    data = yaml.safe_load(contract.contract_path.read_text())["units"][contract.unit][
        "fp8"
    ]
    if (
        data.get("accumulator") != "f32"
        or data.get("reduction") != "sequential_fma"
        or "e4m3" not in data.get("formats", [])
    ):
        raise ValueError("source contract does not declare the required FP8 arithmetic")
    sources = data.get("sources", {})
    if set(sources) != {"cell", "sequencer", "conversion"}:
        raise ValueError("complete FP8 arithmetic source evidence required")
    paths = [contract.checkout() / str(value) for value in sources.values()]
    if any(not path.is_file() for path in paths):
        raise ValueError("FP8 source evidence is unavailable")
    return paths


def from_config(m, n, k, *, unit, config, worker_mask):
    """Reuse checked byte/F32 sizes; FP8 primitive owns one TE×TE tile per job."""
    from .vme_shim import load_contract

    contract = load_contract(unit)
    flag = contract.declarations.get("fp8_enable_flag")
    if not flag or contract.vector_params(
        config, enable_flag=str(flag)
    ) != contract.vector_params(config):
        raise ValueError(
            "selected source configuration does not prove the FP8 matrix unit"
        )
    if "matmul_fp8" not in contract.kernel_roles:
        raise ValueError("selected source contract has no FP8 instruction role")
    additional_sources(contract)
    value = split_vme.from_config(
        m, n, k, unit=unit, config=config, worker_mask=worker_mask
    )
    columns = (n - 1) // value.tile_edge + 1
    return replace(
        value,
        block_columns=columns,
        block_count=((m - 1) // value.tile_edge + 1) * columns,
    )


def record(layout):
    data = layout.to_dict()
    data.update(
        schema="iris.controller_packed_fp8.v1",
        numeric_contract="finite_E4M3FN_exact_products_ordered_FP32_accumulation_RNE_positive_zero_seed",
        source_input_types=["f8E4M3FN", "f8E4M3FN"],
        output_type="f32",
        block_shape=[layout.tile_edge, layout.tile_edge],
        input_quantizer=False,
        scale_bias_inside_primitive=False,
        controller_packing="scalar_encoded_byte_layout_copy",
    )
    return data


def source_files():
    return {
        name: ROOT / "kernels" / name
        for name in (
            "vme_fp8_split.h",
            "vme_fp8_split_prepare.c",
            "vme_fp8_split_compute.c",
            "vme_gemm_fp8.c",
            "vme_api.h",
        )
    }
