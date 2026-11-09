"""Public selected-provider edge delegates to the shared chain builder."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "outlined_i8_program", ROOT / "tools/campaigns/outlined_i8_program.py"
)
CLI = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CLI)


def test_resource_limits_are_explicit_and_positive():
    with pytest.raises(SystemExit):
        CLI.parser().parse_args(["--source", "x", "--output", "y", "--route", "vme"])
    with pytest.raises(Exception, match="positive"):
        CLI.positive("0")


def test_support_selection_restores_environment_and_search_path(tmp_path, monkeypatch):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/streaming_support_pins.json").write_text("{}")
    (tmp_path / "base/rvv-mlir").mkdir(parents=True)
    (tmp_path / "base/rvv-mlir/provider.yaml").write_text("{}")
    monkeypatch.setenv("MERLIN_SUPPORT_PINS", "caller-selection")
    monkeypatch.delenv("MERLIN_TARGET_PATH", raising=False)
    previous = list(sys.path)
    with pytest.raises(RuntimeError), CLI.scoped_support(tmp_path):
        assert os.environ["MERLIN_SUPPORT_PINS"] == str(
            tmp_path / "contracts/streaming_support_pins.json"
        )
        assert os.environ["MERLIN_TARGET_PATH"] == str(tmp_path / "base/rvv-mlir")
        raise RuntimeError("failure during build")
    assert os.environ["MERLIN_SUPPORT_PINS"] == "caller-selection"
    assert "MERLIN_TARGET_PATH" not in os.environ
    assert sys.path == previous


def test_existing_output_is_never_modified(tmp_path):
    output = tmp_path / "retained"
    output.mkdir()
    marker = output / "keep"
    marker.write_bytes(b"immutable")
    args = CLI.parser().parse_args(
        [
            "--source",
            "unread-source",
            "--output",
            str(output),
            "--route",
            "vme",
            "--dispatch-index",
            "0",
            "--unit",
            "unread-unit",
            "--config",
            "unread-config",
            "--workspace-budget-bytes",
            "65536",
            "--descriptor-job-budget",
            "64",
        ]
    )
    with pytest.raises(ValueError, match="fresh"):
        CLI.build(args)
    assert marker.read_bytes() == b"immutable"
    assert sorted(p.name for p in output.iterdir()) == ["keep"]


@pytest.mark.parametrize("route", ["vme", "rvv"])
def test_actual_public_example_builds_linkable_chain(tmp_path, route):
    from merlin.llvmlower import toolchain

    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit pinned RTL and compiler installation required")
    args = CLI.parser().parse_args(
        [
            "--source",
            str(ROOT / "examples/outlined_i8_chain.mlir"),
            "--output",
            str(tmp_path / route),
            "--route",
            route,
            "--dispatch-index",
            "0",
            "--unit",
            "saturn_vme",
            "--config",
            "IrisConfig",
            "--workspace-budget-bytes",
            "65536",
            "--descriptor-job-budget",
            "64",
            "--prefix",
            "public_chain_" + route,
        ]
    )
    report = CLI.build(args)
    assert report["status"] == "built_linkable_not_execution_admitted"
    assert report["route"] == route
    assert report["provider"]["source_ir_sha256"]
    assert report["provider"]["provenance"]["controller_packing"] == "scalar_common_ISA"
    assert report["workspace_bytes"] <= report["workspace_budget_bytes"]
    assert (
        report["hardware_contacted"]
        is report["full_model_dispatch_integrated"]
        is report["target_execution_admitted"]
        is False
    )
    assert Path(report["chain_build"]).is_file()
    assert not any(p.suffix == ".elf" for p in (tmp_path / route).rglob("*"))


@pytest.mark.parametrize("refusal", ["missing_dispatch", "unsigned_source"])
def test_source_or_selection_refuses_before_output_creation(tmp_path, refusal):
    text = (ROOT / "examples/outlined_i8_chain.mlir").read_text()
    if refusal == "unsigned_source":
        text = text.replace("arith.extsi", "arith.extui", 1)
    source = tmp_path / "source.mlir"
    source.write_text(text)
    output = tmp_path / "refused"
    args = CLI.parser().parse_args(
        [
            "--source",
            str(source),
            "--output",
            str(output),
            "--route",
            "vme",
            "--dispatch-index",
            "99" if refusal == "missing_dispatch" else "0",
            "--unit",
            "saturn_vme",
            "--config",
            "IrisConfig",
            "--workspace-budget-bytes",
            "65536",
            "--descriptor-job-budget",
            "64",
        ]
    )
    with pytest.raises(ValueError):
        CLI.build(args)
    assert not output.exists()
