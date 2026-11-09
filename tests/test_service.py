"""Public provider admission and real shared rewriting; no native or hardware claim."""

from pathlib import Path

import pytest
from merlin.api import compile
from merlin.compiler.request import CompileOptions, CompileRequest, Source
from merlin.targetgen.plugins import load_module

ROOT = Path(__file__).resolve().parents[1]
SOURCE = """builtin.module {
  func.func @fixture(%a: tensor<2x2xi8>, %b: tensor<2x2xi8>) -> tensor<2x2xi32> {
    %z = arith.constant 0 : i32
    %e = tensor.empty() : tensor<2x2xi32>
    %f = linalg.fill ins(%z : i32) outs(%e : tensor<2x2xi32>) -> tensor<2x2xi32>
    %r = linalg.matmul ins(%a, %b : tensor<2x2xi8>, tensor<2x2xi8>)
         outs(%f : tensor<2x2xi32>) -> tensor<2x2xi32>
    func.return %r : tensor<2x2xi32>
  }
}
"""


def selected(tmp_path, **parameters):
    values = {
        "iris.unit": "saturn_vme",
        "iris.config": "selected-config",
        "iris.emit": "prepared-ir",
    }
    values.update(parameters)
    return CompileRequest(
        Source(SOURCE, kind="upstream-mlir"),
        tmp_path / "result",
        CompileOptions(parameters=values),
    )


@pytest.fixture
def service():
    return load_module(ROOT, "service.py", package_name="iris_ordinary_service")


def test_admission_refuses_implicit_or_image_routes_before_output(service, tmp_path):
    request = selected(tmp_path)
    for parameters, match in (
        ({"iris.emit": "elf"}, "explicitly select"),
        ({"other.option": True}, "unsupported"),
        (
            {"iris.emit": "object", "iris.cc": "/absent/compiler"},
            "available C compiler",
        ),
    ):
        request = selected(tmp_path, **parameters)
        with pytest.raises((ValueError, FileNotFoundError), match=match):
            service.compile(request)
        assert not request.output.exists()
    request = selected(tmp_path)
    with pytest.raises(ValueError, match="platform images"):
        service.validate(
            CompileRequest(
                request.source,
                request.output,
                CompileOptions(
                    platform="zephyr", parameters=request.options.parameters
                ),
            )
        )
    assert not request.output.exists()


def test_actual_missing_rtl_refuses_before_output(service, tmp_path, monkeypatch):
    from merlin.common import paths

    monkeypatch.setattr(paths, "env", lambda name: None)
    request = selected(tmp_path)
    with pytest.raises(FileNotFoundError, match="unset"):
        service.compile(request)
    assert not request.output.exists()


def test_public_partial_ir_preserves_real_rewrite_and_truthful_artifacts(
    service, tmp_path, monkeypatch
):
    # Geometry is an explicit fixture; the real target selector and shared rewrite run.
    # This checks transport/admission, not selected-RTL geometry or native execution.
    driver = service
    provider = driver.adapter("saturn_vme")
    monkeypatch.setattr(
        driver,
        "selection",
        lambda unit, config: (provider, None, 2, 16, []),
    )
    request = selected(tmp_path)
    result = compile(
        request.source, backend=service, output=request.output, options=request.options
    )
    assert result.backend == "iris"
    assert result.deployment is None
    assert result.metadata["routed"] == 1
    assert result.metadata["execution"] == "not_run"
    assert result.metadata["qualification"] == "not_established"
    assert {artifact.kind for artifact in result.artifacts} == {
        "partial-mlir",
        "matrix-signatures",
        "matrix-build-receipt",
    }
    assert "bufferization.access" in (request.output / "prepared.mlir").read_text()
    assert (request.output / "compilation.json").is_file()
    with pytest.raises(FileExistsError):
        service.compile(request)


def test_empty_admission_remains_a_failure(service, tmp_path, monkeypatch):
    driver = service
    provider = driver.adapter("saturn_vme")
    monkeypatch.setattr(
        driver, "selection", lambda unit, config: (provider, None, 3, 16, [])
    )
    request = selected(tmp_path)
    with pytest.raises(ValueError, match="no contractions admitted"):
        compile(
            request.source,
            backend=service,
            output=request.output,
            options=request.options,
        )
    assert (request.output / "failure.json").is_file()
    assert not (request.output / "compilation.json").exists()


@pytest.fixture
def runtime(service, tmp_path):
    """Synthetic launcher inputs; no Iris model or hardware is executed."""
    import importlib
    from types import SimpleNamespace

    from merlin.compiler.artifact import Artifact, Deployment
    from merlin.runtime.service import RunRequest

    module = importlib.import_module(service.__package__ + ".src.iris.runtime")
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = (243).to_bytes(2, "little")
    header[48:52] = (4).to_bytes(4, "little")
    elf = tmp_path / "image.elf"
    elf.write_bytes(header)
    deployment = Deployment(
        "selected-iris", "baremetal", "rv64gcv_zfh", "lp64d", 3, 0x80000000, 4096
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    source = tmp_path / "simulator"
    (source / "src/iris").mkdir(parents=True)
    (source / "simulator").mkdir()
    launcher = tmp_path / "launcher"
    launcher.write_text("synthetic launcher bytes")
    descriptor = {
        "available": True,
        "binary": str(launcher),
        "native_binary": str(launcher),
        "binary_sha256": module._sha(launcher),
        "native_binary_sha256": module._sha(launcher),
        "per_hart_isa": {
            "0": "rv64gcv_zfh_zvt32t",
            "1": "rv64gcv_zfh_zvt32t",
            "2": "rv64gcv_zfh",
        },
        "matrix_hart_ids": [0, 1],
        "memory_regions": [[0x80000000, 4096]],
        "isa_extensions": ["zvt32t"],
        "launch_args": ["--selected-profile"],
        "execution_input_sha256": {str(launcher): module._sha(launcher)},
        "provenance": {"profile": "source_compute"},
        "accuracy_2percent_certified": False,
        "whole_model_certified": False,
        "timing_status": "experimental_source_prototype",
    }
    runner = module.IrisRuntime(source, manifest, deployment, (0, 1), True, True)
    request = RunRequest(
        Artifact.from_file(elf, kind="elf", role="executable"), deployment, 3, 1
    )
    return SimpleNamespace(
        module=module,
        runner=runner,
        request=request,
        descriptor=descriptor,
        launcher=launcher,
    )


def test_runtime_requires_opt_ins_and_exact_selected_source_before_import(runtime):
    module, runner = runtime.module, runtime.runner
    for empirical, allow in ((False, False), (True, False), (False, True)):
        with pytest.raises(ValueError, match="both explicit"):
            module.IrisRuntime(
                runner.simulator_root,
                runner.manifest,
                runner.deployment,
                (0,),
                empirical,
                allow,
            )
    marker = runner.simulator_root / "executed"
    adapter = runner.simulator_root / "src/iris/simulator.py"
    adapter.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    (runner.simulator_root / "simulator/release.json").write_bytes(
        (module.DATA / "release.json").read_bytes()
    )
    with pytest.raises(ValueError, match="differs from the pinned"):
        runner.validate(runtime.request)
    assert not marker.exists()
    # These installed data files are intentionally independent of checkout Git.
    selection = module._load(module.DATA / "selection.json")
    assert module._sha(module.DATA / "release.json") == selection["release_sha256"]
    assert (
        module._load(module.DATA / "release.json")["source_fork"]["commit"]
        == selection["native_commit"]
    )


@pytest.mark.parametrize(
    "case", ["harts", "workers", "isa", "memory", "profile", "arguments", "source"]
)
def test_runtime_refuses_incompatible_machine_or_changed_execution_inputs(
    runtime, monkeypatch, case
):
    from dataclasses import replace

    request = runtime.request
    descriptor = runtime.descriptor
    if case == "harts":
        descriptor["per_hart_isa"].pop("2")
    elif case == "workers":
        descriptor["matrix_hart_ids"] = [0]
    elif case == "isa":
        descriptor["per_hart_isa"]["2"] = "rv64gc_zfh"
    elif case == "memory":
        descriptor["memory_regions"] = [[0, 4096]]
    elif case == "profile":
        descriptor["provenance"]["profile"] = "source_os"
    elif case == "arguments":
        request = replace(request, arguments=("guest-option",))
    elif case == "source":
        runtime.launcher.write_text("changed launcher")
    monkeypatch.setattr(type(runtime.runner), "_descriptor", lambda _: descriptor)
    launches = []
    monkeypatch.setattr(
        runtime.module.subprocess, "Popen", lambda *a, **k: launches.append(a)
    )
    with pytest.raises(ValueError):
        runtime.runner.run(request)
    assert launches == []


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout", "changed_source"])
def test_public_runtime_preserves_machine_argv_and_selected_attempt_without_fallback(
    runtime, monkeypatch, outcome
):
    from types import SimpleNamespace

    from merlin.api import run
    from merlin.runtime.service import RunOptions

    monkeypatch.setattr(
        type(runtime.runner), "_descriptor", lambda _: runtime.descriptor
    )
    calls, killed = [], []
    communications = 0

    def communicate(**kwargs):
        nonlocal communications
        communications += 1
        if outcome == "timeout" and communications == 1:
            raise runtime.module.subprocess.TimeoutExpired("fixture", 1)
        if outcome == "changed_source":
            runtime.launcher.write_text("changed during selected execution")
        return b"selected stdout\n", b"selected stderr\n"

    process = SimpleNamespace(
        pid=123456, returncode=0 if outcome == "success" else 9, communicate=communicate
    )
    monkeypatch.setattr(
        runtime.module.subprocess,
        "Popen",
        lambda *a, **k: calls.append((a, k)) or process,
    )
    monkeypatch.setattr(runtime.module.os, "killpg", lambda *a: killed.append(a))
    options = RunOptions(deployment=runtime.request.deployment, timeout=1)
    if outcome == "changed_source":
        with pytest.raises(ValueError, match="missing or changed"):
            run(runtime.request.artifact, runner=runtime.runner, options=options)
        assert len(calls) == 1
        return
    result = run(runtime.request.artifact, runner=runtime.runner, options=options)
    assert len(calls) == 1
    assert (
        result.status
        == {"success": "completed", "failed": "failed", "timeout": "timed_out"}[outcome]
    )
    assert result.stdout == "selected stdout\n"
    assert result.metadata["stderr"] == "selected stderr\n"
    argv = result.metadata["argv"]
    assert "-p3" in argv and "-m0x80000000:0x1000" in argv
    assert "--hart-isa=0:rv64gcv_zfh_zvt32t" in argv
    assert "--hart-isa=2:rv64gcv_zfh" in argv
    assert result.metadata["matrix_harts"] == [0, 1]
    assert result.metadata["execution_request"]["deployment"]["harts"] == 3
    assert (
        result.metadata["execution_request"]["artifact_sha256"]
        == result.artifact_sha256
    )
    assert result.metadata["accuracy_2percent_certified"] is False
    assert result.metadata["whole_model_certified"] is False
    assert result.metadata["verification"] == "not_requested"
    assert (
        result.metadata["simulator"]["timing_status"] == "experimental_source_prototype"
    )
    assert bool(killed) == (outcome == "timeout")
