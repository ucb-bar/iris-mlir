"""Public selected-provider edge delegates to the shared chain builder."""

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
            "--worker-slots",
            "0",
            "1",
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


def authored_source(operations):
    text = (ROOT / "examples/outlined_i8_chain.mlir").read_text()
    start, end = text.index("    %bias_empty"), text.index("    func.return")
    if operations == 1:
        return (
            text[:start].replace(", %bias:tensor<65xi32>", "")
            + "    func.return %result:tensor<3x65xi32>\n  }\n}\n"
        )
    if operations == 3:
        second = (
            text[start:end]
            .replace("%bias_empty", "%second_empty")
            .replace("%final", "%second")
            .replace("%result", "%final")
        )
        return text[:end] + second + text[end:].replace("%final", "%second")
    return text


def arguments(source, output, route="vme", slots=(0, 1)):
    return CLI.parser().parse_args(
        [
            "--source",
            str(source),
            "--output",
            str(output),
            "--route",
            route,
            "--worker-slots",
            *map(str, slots),
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


@pytest.mark.parametrize("route", ["vme", "rvv"])
@pytest.mark.parametrize("operations", [1, 2, 3])
def test_actual_public_example_builds_linkable_chain(tmp_path, route, operations):
    from merlin.llvmlower import toolchain

    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit pinned RTL and compiler installation required")
    source = tmp_path / "source.mlir"
    source.write_text(authored_source(operations))
    slots = tuple(index % 2 for index in range(operations))
    report = CLI.build(arguments(source, tmp_path / route, route, slots))
    assert report["status"] == "built_linkable_not_execution_admitted"
    assert report["route"] == route
    assert report["ordinary_dispatch_worker_slots"] == list(slots)
    assert report["prepared_selected_worker_slots"] == [0, 1]
    assert report["provider_module_origins"]
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


@pytest.mark.parametrize("slots", [(0,), (0, 1, 0)])
def test_wrong_dispatch_assignment_count_refuses_before_output(tmp_path, slots):
    output = tmp_path / "refused"
    with pytest.raises(ValueError):
        CLI.build(
            arguments(ROOT / "examples/outlined_i8_chain.mlir", output, slots=slots)
        )
    assert not output.exists()


@pytest.mark.parametrize("family", ["iris", "rvv"])
def test_cached_foreign_provider_refuses_without_module_replacement(
    tmp_path, monkeypatch, family
):
    foreign = tmp_path / "foreign.py"
    foreign.write_text("# different checkout\n")
    loaded = SimpleNamespace(__file__=str(foreign))
    name = family + ".foreign"
    monkeypatch.setitem(sys.modules, name, loaded)
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="foreign checkout"):
        CLI.build(arguments(ROOT / "examples/outlined_i8_chain.mlir", output))
    assert not output.exists()
    assert sys.modules[name] is loaded


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
            "--worker-slots",
            "0",
            "1",
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


def test_package_search_path_from_foreign_checkout_refuses(tmp_path, monkeypatch):
    loaded = SimpleNamespace(
        __file__=str(ROOT / "src/iris/__init__.py"), __path__=[str(tmp_path)]
    )
    monkeypatch.setitem(sys.modules, "iris.shadow", loaded)
    with pytest.raises(ValueError, match="foreign checkout"):
        CLI.module_origins(ROOT)
    assert sys.modules["iris.shadow"] is loaded
