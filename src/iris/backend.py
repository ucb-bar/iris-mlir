"""Iris's matrix_lowering plugin: route int8 contractions to the VME unit (see vme_shim.py).

The callables are the ones merlin.runtime.backends.zephyr_model.MatrixRouting requires, plus the optional
``device_simulator`` that lets a build run its SHIPPED image on a simulator implementing the unit. This
adapter certifies nothing by itself; the gates are the build's audit, the twin and the simulator run.
"""

from pathlib import Path

from merlin.compiler.backend.composition import validate_backend_composition

from . import pipelines, placement
from .lower import vme_shim

API_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]

signature_sidecar_name = vme_shim.SIDECAR_NAME
SIDECAR_NAME = vme_shim.SIDECAR_NAME
shim = vme_shim


def load_contract(unit, *, path=None):
    validate_backend_composition(ROOT, bases=[ROOT / "base/rvv-mlir"])
    return vme_shim.load_contract(unit, path=path)


def derive_encodings(contract):
    return vme_shim.derive_encodings(contract)


def geometry(*, unit, config):
    return vme_shim.load_contract(unit).geometry(config)


def selector(tile_edge):
    return placement.selector(tile_edge)


def rewrite_prepared_file(prepared, work, *, select, tile_edge):
    return pipelines.rewrite_prepared_file(prepared, work, select=select, tile_edge=tile_edge)


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
    (``merlin.analysis.kernels.matrix_kernels.KernelChoice``); None builds the generated kernel exactly as before."""
    extra = {} if kernel is None else {"kernel": kernel}
    return vme_shim.build_object(
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


def device_simulator(*, unit, config):
    return vme_shim.device_simulator(unit=unit, config=config)


def kernel_header_facts(*, unit, config):
    """Configuration facts the generated header of a hand-written kernel carries (kmax, operand layouts)."""
    return vme_shim.kernel_header_facts(unit=unit, config=config)


def generated_kernel(*, unit, config, max_k=None):
    """``(C source, function)`` of the generated kernel, for Merlin's kernel-test."""
    return vme_shim.generated_kernel(unit=unit, config=config)
