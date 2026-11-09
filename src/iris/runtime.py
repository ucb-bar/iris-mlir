"""Explicit ordinary execution against Iris's separately selected simulator.

The installed provider carries release-selection data, not simulator source,
binaries, correctness oracles or timing qualification. The selected upstream
adapter admits its own independent native/provider closure before it is used.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from merlin.compiler.artifact import Deployment
from merlin.runtime.service import RunRequest, RunResult

DATA = Path(__file__).with_name("runtime-data")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load(path: Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate runtime JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"nonfinite runtime JSON value: {value}")

    result = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=invalid,
    )
    if not isinstance(result, dict):
        raise TypeError("runtime JSON requires an object")
    return result


def _inputs(descriptor: dict) -> dict[str, str]:
    expected = copy.deepcopy(descriptor.get("execution_input_sha256"))
    if not isinstance(expected, dict) or not expected:
        raise ValueError("simulator requires an explicit execution source closure")
    for name in ("binary", "native_binary"):
        path, digest = descriptor.get(name), descriptor.get(name + "_sha256")
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or not isinstance(digest, str)
            or expected.get(path) != digest
        ):
            raise ValueError(f"{name} is outside the simulator source closure")
    for path, digest in expected.items():
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not Path(path).is_file()
            or _sha(Path(path)) != digest
        ):
            raise ValueError(f"simulator execution input missing or changed: {path}")
    return expected


def _strings(value, name):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or "\x00" in item for item in value
    ):
        raise ValueError(f"{name} requires explicit string arguments")
    return value


@dataclass(frozen=True)
class IrisRuntime:
    """Construct with an exact source checkout, descriptor and image contract.

    deployment.harts counts physical harts; matrix_harts selects matrix workers.
    All physical harts remain present. Worker selection is a caller assertion,
    not an independent check of the image's instructions or numerical behavior.
    No environment variable or compiler support import selects this runtime.
    """

    simulator_root: Path
    manifest: Path
    deployment: Deployment
    matrix_harts: tuple[int, ...]
    empirical_timing: bool = False
    allow_experimental_timing: bool = False
    name: str = "iris-simulator"
    _manifest_sha256: str = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "simulator_root", Path(self.simulator_root).resolve())
        object.__setattr__(self, "manifest", Path(self.manifest).resolve())
        if (
            type(self.matrix_harts) is not tuple
            or not self.matrix_harts
            or any(type(hart) is not int or hart < 0 for hart in self.matrix_harts)
            or len(set(self.matrix_harts)) != len(self.matrix_harts)
        ):
            raise ValueError("matrix_harts requires distinct physical worker IDs")
        if not isinstance(self.deployment, Deployment):
            raise TypeError("runtime requires the image's exact Deployment")
        if (
            self.empirical_timing is not True
            or self.allow_experimental_timing is not True
        ):
            raise ValueError(
                "Iris empirical timing requires both explicit experimental opt-ins"
            )
        object.__setattr__(self, "_manifest_sha256", _sha(self.manifest))

    def _descriptor(self):
        selection = _load(DATA / "selection.json")
        release = DATA / "release.json"
        if _sha(release) != selection["release_sha256"]:
            raise ValueError("installed Iris release selection changed")
        adapter = self.simulator_root / selection["adapter"]
        selected_release = self.simulator_root / selection["release"]
        if (
            _sha(adapter) != selection["adapter_sha256"]
            or _sha(selected_release) != selection["release_sha256"]
        ):
            raise ValueError(
                "selected simulator adapter/release differs from the pinned source"
            )
        if _sha(self.manifest) != self._manifest_sha256:
            raise ValueError("selected simulator descriptor changed after construction")
        spec = importlib.util.spec_from_file_location(
            "iris_selected_runtime_admission", adapter
        )
        if spec is None or spec.loader is None:
            raise ValueError("selected simulator admission module is unavailable")
        provider = importlib.util.module_from_spec(spec)
        source = adapter.read_bytes()
        if hashlib.sha256(source).hexdigest() != selection["adapter_sha256"]:
            raise ValueError("selected simulator admission source changed")
        # Load the verified bytes themselves rather than rereading mutable source.
        exec(compile(source, str(adapter), "exec"), provider.__dict__)  # noqa: S102
        # The exact upstream admission checks its manifest/native/source closure.
        # Call it directly: explicit constructor flags replace its shell opt-ins.
        result = provider.verified_model(self.manifest)
        if (
            not isinstance(result, dict)
            or result.get("available") is not True
            or result.get("accuracy_2percent_certified") is not False
            or result.get("whole_model_certified") is not False
        ):
            raise ValueError(
                "selected simulator refused or changed its qualification scope"
            )
        result = copy.deepcopy(result)
        inputs = _inputs(result)
        for path in (adapter, selected_release, self.manifest):
            if inputs.get(str(path)) != _sha(path):
                raise ValueError(
                    "simulator admission source is outside its execution closure"
                )
        if (
            _sha(adapter) != selection["adapter_sha256"]
            or _sha(selected_release) != selection["release_sha256"]
            or _sha(self.manifest) != self._manifest_sha256
        ):
            raise ValueError(
                "selected simulator admission inputs changed during resolution"
            )
        result["execution_input_sha256"].update(
            {
                str(path.resolve()): _sha(path)
                for path in (Path(__file__), DATA / "selection.json", release)
            }
        )
        return result

    def _launch(self, request: RunRequest, descriptor: dict) -> list[str]:
        from merlin.runtime.spike import validate as validate_elf

        validate_elf(request)
        if request.arguments:
            raise ValueError("Iris simulator does not support guest process arguments")
        if (
            request.deployment != self.deployment
            or request.harts != self.deployment.harts
        ):
            raise ValueError(
                "runtime request differs from the selected image deployment"
            )
        per_hart = descriptor.get("per_hart_isa")
        if not isinstance(per_hart, dict) or set(per_hart) != {
            str(hart) for hart in range(request.harts)
        }:
            raise ValueError("simulator must describe every physical image hart")
        # Exact token inclusion is intentionally conservative; this adapter does
        # not infer ISA aliases, extension versions or feature upgrades.
        base, *extensions = request.deployment.isa.split("_")
        for isa in per_hart.values():
            if (
                not isinstance(isa, str)
                or not isa
                or "\x00" in isa
                or isa.split("_")[0] != base
                or not set(extensions) <= set(isa.split("_")[1:])
            ):
                raise ValueError(
                    "image's common ISA is not admitted on every physical hart"
                )
        workers = descriptor.get("matrix_hart_ids")
        if (
            not isinstance(workers, list)
            or not workers
            or any(
                type(hart) is not int or hart < 0 or hart >= request.harts
                for hart in workers
            )
            or len(set(workers)) != len(workers)
            or not set(self.matrix_harts) <= set(workers)
        ):
            raise ValueError("requested matrix workers lack the selected unit")
        regions = descriptor.get("memory_regions")
        if (
            not isinstance(regions, list)
            or not regions
            or any(
                not isinstance(region, (list, tuple))
                or len(region) != 2
                or type(region[0]) is not int
                or region[0] < 0
                or type(region[1]) is not int
                or region[1] <= 0
                for region in regions
            )
        ):
            raise ValueError("simulator requires an explicit physical memory map")
        if [request.deployment.dram_base, request.deployment.memory_bytes] not in [
            list(r) for r in regions
        ]:
            raise ValueError(
                "image DRAM differs from the selected simulator memory map"
            )
        provenance = descriptor.get("provenance")
        if not isinstance(provenance, dict):
            raise TypeError("simulator requires explicit source provenance")
        profile = provenance.get("profile")
        required = (
            "source_os" if request.deployment.platform == "zephyr" else "source_compute"
        )
        if profile != required:
            raise ValueError("simulator profile does not admit the requested platform")
        isa_extensions = _strings(descriptor.get("isa_extensions"), "isa_extensions")
        arguments = _strings(descriptor.get("launch_args"), "launch_args")
        isa = "_".join(
            dict.fromkeys([*request.deployment.isa.split("_"), *isa_extensions])
        )
        return [
            descriptor["binary"],
            "--isa=" + isa,
            f"-p{request.harts}",
            "-m" + ",".join(f"{base:#x}:{size:#x}" for base, size in regions),
            *arguments,
            *(
                f"--hart-isa={hart}:{per_hart[str(hart)]}"
                for hart in range(request.harts)
            ),
            str(request.artifact.path),
        ]

    def validate(self, request: RunRequest) -> None:
        self._launch(request, self._descriptor())

    def run(self, request: RunRequest) -> RunResult:
        descriptor = self._descriptor()
        argv = self._launch(request, descriptor)
        if os.name != "posix":
            raise ValueError(
                "the selected Iris native launcher requires a POSIX execution host"
            )
        inputs = _inputs(descriptor)
        request.artifact.verify()
        # The fatal-aware selected launcher owns guest cleanup. The outer group
        # also bounds the launcher/native descendants if its own timeout expires.
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=request.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        finally:
            # A selected attempt never retries through the legacy Zvt route.
            request.artifact.verify()
            _inputs({**descriptor, "execution_input_sha256": inputs})
        return RunResult(
            self.name,
            request.artifact.sha256,
            "timed_out"
            if timed_out
            else "completed"
            if process.returncode == 0
            else "failed",
            stdout.decode("utf-8", errors="replace"),
            {
                "argv": argv,
                "execution_request": {
                    "artifact": str(request.artifact.path),
                    "artifact_sha256": request.artifact.sha256,
                    "deployment": asdict(request.deployment),
                    "timeout": request.timeout,
                    "matrix_harts": list(self.matrix_harts),
                },
                "returncode": process.returncode,
                "stderr": stderr.decode("utf-8", errors="replace"),
                "physical_harts": request.harts,
                "matrix_harts": list(self.matrix_harts),
                "per_hart_isa": descriptor["per_hart_isa"],
                "memory_regions": descriptor["memory_regions"],
                "execution_input_sha256": inputs,
                "simulator": descriptor,
                "verification": "not_requested",
                "accuracy_2percent_certified": False,
                "whole_model_certified": False,
            },
        )
