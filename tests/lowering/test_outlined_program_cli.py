"""Public selected-provider edge delegates to the shared chain builder."""

import importlib.util
import json
import os
import shutil
import subprocess
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


@pytest.mark.parametrize("route", ["rvv", "vme"])
@pytest.mark.parametrize("hart", [0, 1])
def test_actual_single_compute_hart_builds_both_prepared_routes(tmp_path, route, hart):
    from merlin.llvmlower import toolchain

    if not os.environ.get("MERLIN_CHIPYARD_IRIS") or not toolchain.available():
        pytest.skip("explicit pinned RTL and compiler installation required")
    source = tmp_path / "source.mlir"
    source.write_text(authored_source(2))
    args = arguments(source, tmp_path / route, route, slots=(0, 0))
    args.compute_harts = (hart,)
    report = CLI.build(args)
    assert report["compute_harts"] == [hart]
    assert report["ordinary_dispatch_worker_slots"] == [0, 0]
    assert report["prepared_selected_worker_slots"] == [0]
    provider = report["provider"]["provenance"]
    assert provider["worker_count"] == 1
    assert provider["worker_harts"] == [hart]
    assert provider["selected_worker_slot_mask"] == 1
    assert provider["selected_physical_hart_mask"] == 1 << hart
    assert all(
        Path(path).read_bytes().startswith(b"\x7fELF")
        for path in report["provider"]["objects"]
    )
    chain = json.loads(Path(report["chain_build"]).read_text())
    assert chain["roles"]["compute_harts"] == [hart]
    assert chain["roles"]["profile_harts"] == [hart]
    if route == "rvv":
        _check_host_worker_protocol(
            report["provider"], tmp_path / "host-protocol", hart
        )


def _check_host_worker_protocol(prepared, work, hart):
    """Exercise actual generated control/compute C; arithmetic leaves are stubs."""
    from merlin.common.paths import runtime_dir

    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("host C compiler required for callback protocol verification")
    work.mkdir()
    provider = prepared["provenance"]
    base = provider["base_controller_record"]
    header = Path(prepared["header"])
    base_header = Path(base["header"])
    control = header.with_name(header.stem + "_control.c")
    compute = header.with_name(header.stem + "_compute.c")
    base_control = base_header.with_name(base_header.stem + "_control.c")
    runtime = runtime_dir() / "c"
    stubs = "\n".join(
        f"void {leaf['c_interface_symbol']}(void*a,void*b,void*c){{(void)a;(void)b;(void)c;leaf_calls++;}}"
        for leaf in provider["leaves"]
    )
    source = work / "protocol.c"
    source.write_text(f'''#include <assert.h>
#include <stdint.h>
#include "{header}"
unsigned iris_vme_split_test_hart=2;
static unsigned leaf_calls;
{stubs}
int main(void){{
 _Alignas(64) int8_t a[3*17]={{0}},b[17*65]={{0}};
 _Alignas(64) int32_t c[3*65]={{0}};
 {prepared["state_type"]} state;
 const merlin_arg_t args[3]={{{{.kind=MERLIN_INPUT,.rank=2,.dims={{3,17}},.elem_size=1}},
  {{.kind=MERLIN_INPUT,.rank=2,.dims={{17,65}},.elem_size=1}},
  {{.kind=MERLIN_OUTPUT,.rank=2,.dims={{3,65}},.elem_size=4}}}};
 void*data[3]={{a,b,c}},*descriptors[3]={{a,b,c}};
 const size_t capacities[3]={{sizeof a,sizeof b,sizeof c}};
 unsigned harts[1]={{{hart}}};
 merlin_staged_kernel_invocation i={{.abi_version=MERLIN_STAGED_KERNEL_ABI_VERSION,
  .input_count=2,.output_count=1,.argument_count=3,.arguments=args,.data=data,
  .capacities=capacities,.descriptors=descriptors,.sequence=1,.worker_count=1,
  .selected_worker_mask=1,.worker_harts=harts}};
 assert({prepared["prepare"]}(&state,&i)==0);
 state.calls[0]=99;state.base.completed[0]=99;
 assert({prepared["prepare"]}(&state,&i)==0);
 assert(state.calls[0]==0&&state.base.completed[0]==0&&state.counts[0]>0);
 iris_vme_split_test_hart={hart};
 assert({prepared["compute"]}(&state,&i,0)==0);
 assert(leaf_calls==state.counts[0]&&state.calls[0]==state.counts[0]);
 assert({prepared["compute"]}(&state,&i,1)!=0);
 i.selected_worker_mask=2;
 assert({prepared["compute"]}(&state,&i,0)!=0);
 iris_vme_split_test_hart=2;
 assert({prepared["prepare"]}(&state,&i)!=0);
 i.selected_worker_mask=1;harts[0]=2;
 assert({prepared["prepare"]}(&state,&i)!=0);
 harts[0]={hart};
 assert({prepared["finish"]}(&state,&i)==0);
 return 0;
}}
''')
    executable = work / "protocol"
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-O1",
            "-DIRIS_VME_SPLIT_HOST_TEST",
            "-I" + str(runtime),
            "-I" + str(base_header.parent / "kernel"),
            str(source),
            str(control),
            str(compute),
            str(base_control),
            str(base_header.parent / "kernel/vme_split_prepare.c"),
            str(runtime / "merlin_descriptors.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)


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
