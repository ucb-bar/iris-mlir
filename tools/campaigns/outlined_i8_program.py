#!/usr/bin/env python3
"""Build a linkable, explicitly selected split-I8 outlined chain; never launch hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def scoped_support(root: Path):
    """Use the package's exact paired contract through normal Merlin verification."""
    root = root.resolve()
    pins = root / "contracts/streaming_support_pins.json"
    base = root / "base/rvv-mlir"
    if not pins.is_file() or not (base / "provider.yaml").is_file():
        raise ValueError(
            "complete recursive checkout and scoped support contract required"
        )
    previous = {
        key: os.environ.get(key)
        for key in ("MERLIN_SUPPORT_PINS", "MERLIN_TARGET_PATH")
    }
    old_path = list(sys.path)
    try:
        os.environ["MERLIN_SUPPORT_PINS"] = str(pins)
        os.environ["MERLIN_TARGET_PATH"] = str(base)
        sys.path[:0] = [str(root / "src"), str(base / "src")]
        yield pins
    finally:
        sys.path[:] = old_path
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("positive integer required")
    return result


def module_origins(root: Path):
    """Refuse cached provider modules from another checkout; never reload them."""
    roots = {
        "iris": root.resolve() / "src/iris",
        "rvv": root.resolve() / "base/rvv-mlir/src/rvv",
    }
    observed = {}
    for name, module in tuple(sys.modules.items()):
        family = name.split(".", 1)[0]
        if family not in roots:
            continue
        allowed = roots[family]
        file = getattr(module, "__file__", None)
        paths = list(getattr(module, "__path__", ()))
        candidates = ([file] if file else []) + paths
        if not candidates:
            raise ValueError("provider module has unknown origin: " + name)
        resolved = [Path(value).resolve(strict=True) for value in candidates]
        if not all(path.is_relative_to(allowed) for path in resolved):
            raise ValueError("provider module from foreign checkout: " + name)
        observed[name] = [str(path) for path in resolved]
    return observed


def build(args):
    """Delegate source proof, provider preparation and orchestration to their owners."""
    if args.route not in ("rvv", "vme"):
        raise ValueError("explicit rvv or vme route required")
    if args.dispatch_index < 0:
        raise ValueError("nonnegative selected dispatch index required")
    if not args.prefix.isascii() or not args.prefix.isidentifier():
        raise ValueError("C-safe prefix required")
    if args.output.exists():
        raise ValueError("output must be a fresh directory")
    module_origins(ROOT)
    with scoped_support(ROOT) as pins:
        from merlin.common.paths import runtime_dir
        from merlin.compile.outlined_program import build_outlined_program
        from merlin.frontends.linalg_mlir import parse_mlir_text
        from merlin.llvmlower import codegen, toolchain
        from merlin.llvmlower.deployable_outline import (
            canonical_kernel,
            plan_deployable_chain,
        )
        from merlin.runtime.staged_dispatch import StagedRoles
        from merlin.targetgen.component_composition import validate_backend_composition

        from iris import backend

        module_origins(ROOT)
        composition = validate_backend_composition(ROOT)
        source = args.source.resolve()
        module = parse_mlir_text(source.read_text())
        roles = StagedRoles((0, 1, 2), 2, 2, 2, 2, (0, 1), (0, 1))
        plan = plan_deployable_chain(
            module,
            worker_slots=tuple(args.worker_slots),
            worker_count=2,
            workspace_budget_bytes=args.workspace_budget_bytes,
            alignment_bytes=64,
        )
        if args.dispatch_index >= len(plan.dispatches):
            raise ValueError(
                "selected dispatch index is absent from the closed source chain"
            )
        selected = canonical_kernel(
            plan.outlined.module, plan.dispatches[args.dispatch_index]["symbol"]
        )
        vme_api = backend.staged_kernel_api()
        vme_api.admit_signed_gemm(selected)
        vlen = int(backend.load_contract(args.unit).vector_params(args.config)["vLen"])
        common = tuple(codegen.RISCV_FLAGS) + (
            "-mabi=lp64d",
            "-fno-vectorize",
            "-fno-slp-vectorize",
        )
        controller_flags = common + ("-march=rv64gc",)
        compute_flags = common + (f"-march=rv64gcv_zvl{vlen}b",)
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        try:
            base = vme_api.prepare_staged_kernel(
                selected,
                output / "split_vme",
                unit=args.unit,
                config=args.config,
                roles=roles,
                worker_slots=(0, 1),
                cc=toolchain.clang(),
                controller_cflags=controller_flags,
                compute_cflags=compute_flags,
                runtime_include=runtime_dir() / "c",
                prefix=args.prefix + "_vme",
            )
            prepared = base
            if args.route == "rvv":
                prepared = backend.staged_rvv_kernel_api().prepare_staged_kernel(
                    selected,
                    base,
                    output / "split_rvv",
                    cc=toolchain.clang(),
                    controller_cflags=controller_flags,
                    compute_cflags=compute_flags,
                    runtime_include=runtime_dir() / "c",
                    prefix=args.prefix + "_rvv",
                    descriptor_job_budget=args.descriptor_job_budget,
                )
            record = build_outlined_program(
                source,
                output / "chain",
                roles=roles,
                worker_slots=tuple(args.worker_slots),
                workspace_budget_bytes=args.workspace_budget_bytes,
                alignment_bytes=64,
                compute_cflags=compute_flags,
                prepared_staged_kernels={args.dispatch_index: prepared},
                prefix=args.prefix,
            )
            report = {
                "schema": "iris.outlined_i8_program_build.v1",
                "status": "built_linkable_not_execution_admitted",
                "route": args.route,
                "selected_dispatch": args.dispatch_index,
                "ordinary_dispatch_worker_slots": list(args.worker_slots),
                "prepared_selected_worker_slots": list(prepared.worker_slots),
                "provider_module_origins": module_origins(ROOT),
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "composition": asdict(composition),
                "support_pins": str(pins),
                "support_pins_sha256": hashlib.sha256(pins.read_bytes()).hexdigest(),
                "provider": prepared.record(),
                "chain_build": str(output / "chain/build.json"),
                "workspace_bytes": record["workspace_bytes"],
                "workspace_budget_bytes": args.workspace_budget_bytes,
                "descriptor_job_budget": args.descriptor_job_budget,
                "controller_flags": controller_flags,
                "compute_flags": compute_flags,
                "VLEN_bits": vlen,
                "source_quantization": "none performed by this command; selected GEMM inputs are already signed-I8",
                "automatic_offload_policy": False,
                "full_model_dispatch_integrated": False,
                "target_execution_admitted": False,
                "hardware_contacted": False,
                "physical_busy": None,
                "required_runtime_adapter": "pinned workers with target context init before ARM, launch/join, shared memory lifetime and full target numerical gate",
            }
            (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
            return report
        except Exception as error:
            (output / "failure.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error": str(error),
                        "integrated_packing_fallback": False,
                    },
                    indent=2,
                )
                + "\n"
            )
            raise


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--route", choices=("rvv", "vme"), required=True)
    result.add_argument("--dispatch-index", type=int, required=True)
    result.add_argument(
        "--worker-slots", type=int, choices=(0, 1), nargs="+", required=True
    )
    result.add_argument("--unit", required=True)
    result.add_argument("--config", required=True)
    result.add_argument("--workspace-budget-bytes", type=positive, required=True)
    result.add_argument("--descriptor-job-budget", type=positive, required=True)
    result.add_argument("--prefix", default="outlined_i8_chain")
    return result


def main():
    args = parser().parse_args()
    record = build(args)
    print(
        json.dumps(
            {
                "status": record["status"],
                "route": record["route"],
                "build": str(args.output / "build.json"),
                "hardware_contacted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
