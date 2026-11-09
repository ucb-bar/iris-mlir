"""Compile the selected matrix translation unit; preserve its hardware provenance."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .vme_shim import (
    GENERATED_KERNEL_RECORD,
    VmeBuild,
    emit_translation_unit,
    load_contract,
)


def build_object(
    signatures: Mapping[str, Sequence[int]],
    work: str | Path,
    *,
    unit: str,
    config: str,
    cc: str | Path,
    cflags: Sequence[str],
    scalar_tile: bool = False,
    parallel_tiles: bool = False,
    scratch_bytes: int | None = None,
    contract_path: str | Path | None = None,
    kernel: Any = None,
) -> VmeBuild:
    """Derive the unit's facts, emit the translation unit, compile it, record what it came from.

    ``kernel`` (a ``merlin.kernels.matrix_kernels.KernelChoice``) replaces the generated kernel with a
    hand-written one from ``kernels/``: the shim includes a snapshot of its file beside the generated
    header, and the stand-in twin (``scalar_tile``) compiles Merlin's scalar reference under the same name.
    None (the default) is the generated kernel, byte for byte as before.
    """
    from merlin.common import provenance as PROV

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    contract = load_contract(unit, path=contract_path)
    gaps: list[str] = []
    reads = [
        *contract.sources.values(),
        *(str(m) for m in contract.configs.get("mixin_scala", ())),
    ]
    pins = {
        contract.pin: PROV.verify(
            contract.pin, checkout=contract.checkout(), reads=reads
        )
    }
    if contract.core.get("pin"):
        pins[str(contract.core["pin"])] = PROV.verify(
            str(contract.core["pin"]),
            checkout=contract.core_checkout(),
            reads=[str(contract.core["source"])],
        )
    if contract.host.get("pin"):
        pins[str(contract.host["pin"])] = PROV.verify(
            str(contract.host["pin"]),
            checkout=contract.root(),
            reads=[str(contract.host["source"])],
        )
    for name, ver in pins.items():
        if not ver.ok:
            gaps.append(
                f"pin {name} drifted: {'; '.join([*ver.drift, *map(str, ver.missing_paths)])}"
            )

    tile_edge, alignment = contract.geometry(config)
    kernel_record = dict(GENERATED_KERNEL_RECORD)
    prepared = None
    if kernel is not None:
        prepared = kernel.prepare(work, scalar_tile=scalar_tile)
        kernel_record = prepared.record
    unit_src, derived = emit_translation_unit(
        signatures,
        contract,
        alignment_bytes=alignment,
        scratch_bytes=scratch_bytes,
        kernel=prepared,
    )
    src = work / "merlin_vme_shim.c"
    src.write_text(str(unit_src), encoding="utf-8")
    obj = work / "merlin_vme_shim.o"
    cmd = [str(cc), *cflags, "-c", str(src), "-o", str(obj)]
    if parallel_tiles:
        cmd[1:1] = ["-fopenmp"]
    if scalar_tile:
        cmd[1:1] = [
            "-DMERLIN_MATRIX_SCALAR_TILE",
            f"-DMERLIN_MATRIX_TILE_EDGE={tile_edge}",
        ]
    got = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if got.returncode != 0 or not obj.is_file():
        raise RuntimeError(
            f"the emitted VME shim did not compile:\ncmd: {' '.join(cmd)}\n{got.stderr[-3000:]}"
        )
    (work / "encodings.json").write_text(
        json.dumps(derived.to_dict(), indent=2), encoding="utf-8"
    )
    prov = PROV.record(
        pins=pins,
        sources=[contract.source(k) for k in sorted(contract.sources)]
        + [contract.core_source()]
        + (
            [contract.root() / str(contract.host["source"])]
            if contract.host.get("source")
            else []
        ),
        artifacts={"shim_object": obj, "shim_source": src},
    )
    # Which kernel the bytes came from: the image's provenance names it, and a hand-written one by sha256.
    prov["matrix_kernel"] = kernel_record
    return VmeBuild(
        object_path=obj,
        source_path=src,
        signatures={k: tuple(int(x) for x in v) for k, v in signatures.items()},
        alignment_bytes=alignment,
        scratch_bytes=unit_src.scratch_bytes,
        tile_edge=tile_edge,
        scalar_tile=scalar_tile,
        parallel_tiles=parallel_tiles,
        provenance=prov,
        gaps=tuple(gaps),
        kernel=kernel_record,
    )
