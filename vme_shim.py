"""Iris VME contract binding over Merlin's shared Zvt emitter."""

from __future__ import annotations

from pathlib import Path

from merlin.kernels import vme_kernel
from merlin.kernels import vme_shim as core

CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "matrix_units.yaml"
SIDECAR_NAME = core.SIDECAR_NAME
SYMBOL_PREFIX = core.SYMBOL_PREFIX
SHIM_PREFIX = core.SHIM_PREFIX
OPERAND_BITS = core.OPERAND_BITS
ACC_BITS = core.ACC_BITS
UnitContract = core.UnitContract
VmeBuild = core.VmeBuild
GENERATED_KERNEL_RECORD = core.GENERATED_KERNEL_RECORD


def load_contract(unit, *, path=None):
    return core.load_contract(unit, path=path or CONTRACT_PATH)


derive_encodings = core.derive_encodings
emit_translation_unit = core.emit_translation_unit


def device_simulator(*, unit, config, contract_path=None):
    return core.device_simulator(unit=unit, config=config, contract_path=contract_path or CONTRACT_PATH)


def generated_kernel(*, unit, config):
    return core.generated_kernel(unit=unit, config=config, contract_path=CONTRACT_PATH)


def kernel_header_facts(*, unit, config):
    return core.kernel_header_facts(unit=unit, config=config, contract_path=CONTRACT_PATH)


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
    scratch_bytes=None,
    contract_path=None,
    kernel=None,
):
    return core.build_object(
        signatures,
        work,
        unit=unit,
        config=config,
        cc=cc,
        cflags=cflags,
        scalar_tile=scalar_tile,
        parallel_tiles=parallel_tiles,
        scratch_bytes=scratch_bytes,
        contract_path=contract_path or CONTRACT_PATH,
        kernel=kernel,
    )
