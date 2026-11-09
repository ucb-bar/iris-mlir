"""Iris's matrix_lowering plugin: route int8 contractions to the VME unit (see vme_shim.py).

The callables are the ones merlin.runtime.backends.zephyr_model.MatrixRouting requires, plus the optional
``device_simulator`` that lets a build run its SHIPPED image on a simulator implementing the unit. This
adapter certifies nothing by itself; the gates are the build's audit, the twin and the simulator run.
"""

from pathlib import Path

from merlin.targetgen.component_composition import validate_backend_composition

from . import pipelines, placement
from .lower import vme_shim

API_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]

signature_sidecar_name = vme_shim.SIDECAR_NAME
SIDECAR_NAME = vme_shim.SIDECAR_NAME
shim = vme_shim


def load_contract(unit, *, path=None):
    validate_backend_composition(ROOT)
    return vme_shim.load_contract(unit, path=path)


def derive_encodings(contract):
    return vme_shim.derive_encodings(contract)


def geometry(*, unit, config):
    return vme_shim.load_contract(unit).geometry(config)


def selector(tile_edge):
    return placement.selector(tile_edge)


def streaming_selector(
    *,
    unit,
    config,
    workspace_bytes,
    vlen_bits,
    panel_rows=None,
    select=None,
    prepacked_a=False,
    admission="always",
):
    """Opt in to bounded signed-I8 window panels using the selected RTL facts.

    Pass this callable as ``MatrixRouting.select``. The explicit VLEN is checked
    against the unit; DLEN is read independently. No cache size is inferred.
    ``prepacked_a=True`` produces the primitive's K-major A directly;
    ``admission='workspace_pressure'`` adds a static preference/work proxy.
    """
    from .transforms.streaming import StreamingOptions, StreamingSelector

    contract = load_contract(unit)
    facts = contract.vector_params(config)
    if vlen_bits != int(facts["vLen"]):
        raise ValueError("requested streaming VLEN disagrees with selected unit")
    edge, _ = contract.geometry(config)
    options = StreamingOptions(
        unit=unit,
        config=config,
        vlen_bits=vlen_bits,
        dlen_bits=int(facts["dLen"]),
        tile_edge=edge,
        workspace_bytes=workspace_bytes,
        panel_rows=panel_rows,
        prepacked_a=prepacked_a,
        admission=admission,
    )
    return StreamingSelector(select if select is not None else selector(edge), options)


def rewrite_prepared_file(prepared, work, *, select, tile_edge):
    return pipelines.rewrite_prepared_file(
        prepared, work, select=select, tile_edge=tile_edge
    )


def load_signatures(work):
    return pipelines.load_signatures(work)


def build_object(
    signatures,
    work,
    *,
    unit,
    config,
    cc,
    cflags,
    scalar_tile=False,
    parallel_tiles=False,
    kernel=None,
):
    """``kernel``: a hand-written kernel Merlin selected from ``kernels/kernels.yaml``
    (``merlin.kernels.matrix_kernels.KernelChoice``); None builds the generated kernel exactly as before."""
    from .transforms.streaming import build_schedule_provenance

    schedule = build_schedule_provenance(
        work,
        signatures=signatures,
        unit=unit,
        config=config,
        parallel_tiles=parallel_tiles,
    )
    extra = {} if kernel is None else {"kernel": kernel}
    if schedule and schedule["schedule"].get("prepacked_signatures"):
        extra["prepacked_signatures"] = schedule["schedule"]["prepacked_signatures"]
    built = vme_shim.build_object(
        signatures,
        work,
        unit=unit,
        config=config,
        cc=cc,
        cflags=cflags,
        scalar_tile=scalar_tile,
        parallel_tiles=parallel_tiles,
        **extra,
    )
    if schedule is None:
        return built
    from dataclasses import replace

    return replace(built, provenance={**built.provenance, "streaming_im2col": schedule})


def device_simulator(*, unit, config):
    return vme_shim.device_simulator(unit=unit, config=config)


def kernel_header_facts(*, unit, config):
    """Configuration facts the generated header of a hand-written kernel carries (kmax, operand layouts)."""
    return vme_shim.kernel_header_facts(unit=unit, config=config)


def generated_kernel(*, unit, config, max_k=None):
    """``(C source, function)`` of the generated kernel, for Merlin's kernel-test."""
    return vme_shim.generated_kernel(unit=unit, config=config)


def staged_kernel_api():
    """Explicit split prepare/compute/finish capability; no automatic graph policy."""
    from .lower import outlined_vme

    return outlined_vme


def staged_rvv_kernel_api():
    """Explicit same-layout comparator using the inherited generic RVV leaves."""
    from .lower import outlined_rvv

    return outlined_rvv
