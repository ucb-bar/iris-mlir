"""Run an Iris convolution through the registered VME kernel and verify the route.

Requires Merlin's Iris setup, pinned VME sources, and the Zvt Spike. The built-in
conv2d example is Merlin's W8A8 path; this is not a TorchAO source-model gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

IRIS = Path(__file__).resolve().parents[1]
KERNEL_NAME = "vme_gemm_2x2_bias"
KERNEL = IRIS / "kernels" / f"{KERNEL_NAME}.c"
WORKLOAD = "op_conv2d_16x64x16x3_h1_kernel-vme_gemm_2x2_bias"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_kept(kept: Path) -> dict:
    """Check the actual build's routing and runtime receipts, not command exit alone."""
    summary = json.loads((kept / "summary.json").read_text())
    report = json.loads((kept / "routing_report.json").read_text())
    matrix = summary.get("matrix") or {}
    matrix_detail = matrix.get("matrix") or {}
    shipped = matrix_detail.get("shipped_on_simulator") or {}
    selected = report.get("kernel") or {}
    expected_sha = hashlib.sha256(KERNEL.read_bytes()).hexdigest()

    require(
        summary.get("rvv", {}).get("status") == "verified",
        "RVV reference did not verify",
    )
    require(matrix.get("status") == "verified", "VME image did not verify")
    require(
        shipped.get("status") == "verified",
        "shipped VME image did not verify on Zvt Spike",
    )
    require(
        "zvt" in str(shipped.get("isa", "")), "shipped image did not run with Zvt ISA"
    )
    require(
        (shipped.get("provenance") or {}).get("pin") == "spike_zvt"
        and (shipped.get("provenance") or {}).get("ok") is True,
        "Zvt Spike source pin did not verify",
    )
    require(
        shipped.get("bit_exact_vs_twin") is True,
        "shipped VME output differs from scalar twin",
    )
    require(
        (shipped.get("twin_compare") or {}).get("prefix_equal") is True
        and (shipped.get("twin_compare") or {}).get("out_hash_equal") is True,
        "complete shipped/twin outputs were not bit-identical",
    )
    require(
        matrix_detail.get("routed_contractions", 0) > 0,
        "compiler routed no contractions",
    )
    require(
        report.get("routed_contractions", 0) > 0,
        "routing report has no routed contractions",
    )
    require(
        (report.get("macs_by_site") or {}).get("conv", {}).get("routed", 0) > 0,
        "routing report has no convolution MACs on VME",
    )
    require(
        report.get("unit") == "saturn_vme" and report.get("config") == "IrisConfig",
        "routing report names another unit or configuration",
    )
    require(
        selected.get("name") == KERNEL_NAME and selected.get("generated") is False,
        "routing report did not select the hand-written kernel",
    )
    require(
        selected.get("sha256") == expected_sha,
        "routing report names different kernel bytes",
    )
    require(
        (matrix_detail.get("kernel") or {}).get("sha256") == expected_sha,
        "compiler result disagrees with routing report's kernel bytes",
    )
    require(
        (report.get("numeric_policy") or {}).get("routing_changes_numerics") is False,
        "routing report lacks the numeric-policy statement",
    )
    for name in ("rvv.elf", "matrix.elf", "matrix_twin.elf"):
        require(
            (kept / name).is_file() and (kept / name).stat().st_size > 0,
            f"missing kept image: {name}",
        )
    return {
        "kernel": KERNEL_NAME,
        "kernel_sha256": expected_sha,
        "routed_contractions": report["routed_contractions"],
        "routed_conv_macs": report["macs_by_site"]["conv"]["routed"],
        "shipped_on_zvt_spike": shipped["status"],
        "bit_exact_vs_twin": shipped["bit_exact_vs_twin"],
        "kept": str(kept),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("merlin", type=Path, help="a set-up Merlin checkout")
    args = parser.parse_args()
    merlin = args.merlin.resolve(strict=True)
    launcher = merlin / "examples" / "iris" / "run.sh"
    require(launcher.is_file(), f"no Iris launcher in {merlin}")
    require(
        (IRIS / "base" / "rvv-mlir" / "provider.yaml").is_file(),
        "initialize Iris's pinned RVV submodule",
    )
    env = os.environ.copy()
    env["MERLIN_TARGET_PATH"] = f"{IRIS}:{IRIS / 'base' / 'rvv-mlir'}"
    env["MERLIN_BOARD_CATALOG"] = f"{merlin / 'examples' / 'board-catalog.yaml'}:{IRIS}"
    subprocess.run(
        [str(launcher), "kernel-test", KERNEL_NAME, "--quick"],
        cwd=merlin,
        env=env,
        check=True,
    )
    subprocess.run(
        [
            str(launcher),
            "op",
            "conv2d",
            "16",
            "64",
            "16",
            "3",
            "--matrix-kernel",
            KERNEL_NAME,
            "--harts",
            "1",
        ],
        cwd=merlin,
        env=env,
        check=True,
    )
    out_root = Path(env.get("MERLIN_OUT_ROOT") or merlin / "out")
    kept = out_root / "artifacts" / "probes" / "iris_rtl" / WORKLOAD
    print(json.dumps(check_kept(kept), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
