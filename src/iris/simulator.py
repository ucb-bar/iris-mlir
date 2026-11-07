"""Admission to the explicitly selected, frozen Iris development simulator.

The external release owns instruction semantics, clocks, devices and process
cleanup. This adapter never substitutes stock Spike or changes a frozen input.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "simulator" / "release.json"
NATIVE_PROVIDER = ROOT / "simulator/spike-model/iris/tools/native_model.py"
NATIVE_KIND = "iris.vendored_native_development.v1"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Nonfinite JSON value: " + value)

    result = json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                        parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def verified_model(manifest):
    """Check release identity before importing its separately verified provider."""
    release = load(RELEASE)
    path = Path(manifest).resolve()
    digest = sha(path)
    record = load(path)
    if record.get("package_kind") == NATIVE_KIND:
        return verified_native(path, record)
    profiles = release["profiles"]
    matches = [name for name, value in profiles.items()
               if value["manifest_sha256"] == digest]
    if len(matches) != 1:
        raise ValueError("Manifest is not one of the recorded frozen 35a profiles")
    profile = matches[0]
    if record.get("profile") != profile:
        raise ValueError("Profile identity differs from the frozen release")
    provider_path = Path(record["model_provider"])
    if not provider_path.is_absolute() or sha(provider_path) != release["provider_sha256"]:
        raise ValueError("Frozen provider changed or is missing")
    if record.get("input_sha256", {}).get(str(provider_path)) != release["provider_sha256"]:
        raise ValueError("Provider is outside the release input closure")
    spec = importlib.util.spec_from_file_location("iris_frozen_35a_provider", provider_path)
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    result = provider.experimental_empirical_simulator(path)
    if not isinstance(result, dict) or result.get("available") is not True:
        reason = result.get("reason") if isinstance(result, dict) else "invalid provider response"
        raise ValueError("Frozen release refused: " + str(reason))
    if (result.get("native_binary_sha256") != release["native_binary_sha256"]
            or set(result.get("per_hart_isa", {})) != {"0", "1", "2"}
            or result.get("matrix_hart_ids") != [0, 1]
            or result.get("accuracy_2percent_certified") is not False):
        raise ValueError("Release ISA, native identity or qualification differs")
    return bind_adapter(result)


def verified_native(path, record):
    """Admit only the provider code from the pinned Iris Spike submodule.

    A descriptor from a separate checkout is permitted when its provider bytes
    match the pinned provider; that provider checks the entire built input set.
    """
    if not NATIVE_PROVIDER.is_file():
        raise ValueError("Initialize the simulator/spike-model submodule")
    provider_path = Path(record["model_provider"])
    expected = load(RELEASE)["source_fork"]["provider_sha256"]
    if (sha(NATIVE_PROVIDER) != expected or not provider_path.is_absolute() or sha(provider_path) != expected
            or record.get("input_sha256", {}).get(str(provider_path)) != expected):
        raise ValueError("Development provider differs from the pinned Iris Spike provider")
    spec = importlib.util.spec_from_file_location("iris_pinned_native_provider", provider_path)
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    result = provider.experimental_empirical_simulator(path)
    if not isinstance(result, dict) or result.get("available") is not True:
        reason = result.get("reason") if isinstance(result, dict) else "invalid provider response"
        raise ValueError("Development model refused: " + str(reason))
    release = load(RELEASE)
    profile = record.get("profile")
    if (profile not in release["profiles"]
            or result.get("per_hart_isa") != release["profiles"][profile]["per_hart_isa"]
            or result.get("matrix_hart_ids") != [0, 1]
            or result.get("accuracy_2percent_certified") is not False):
        raise ValueError("Development model changed Iris hart or qualification metadata")
    result["execution_input_sha256"][str(NATIVE_PROVIDER.resolve())] = expected
    return bind_adapter(result)


def bind_adapter(result):
    result = dict(result)
    inputs = dict(result["execution_input_sha256"])
    inputs.update({str(Path(__file__).resolve()): sha(__file__), str(RELEASE): sha(RELEASE)})
    result["execution_input_sha256"] = inputs
    result["provenance"] = dict(result["provenance"], iris_mlir_adapter={
        "path": str(Path(__file__).resolve()), "sha256": sha(__file__),
        "release_sha256": sha(RELEASE)})
    return result


def device_simulator(*, unit, config):
    """Merlin's device_simulator hook; explicit timing opt-in is mandatory."""
    try:
        if (unit, config) != ("saturn_vme", "IrisConfig"):
            raise ValueError("Only saturn_vme / IrisConfig is source-audited")
        manifest = os.environ.get("MERLIN_IRIS_MODEL_MANIFEST")
        if not manifest:
            raise ValueError("Set MERLIN_IRIS_MODEL_MANIFEST to an exact release descriptor")
        if os.environ.get("MERLIN_IRIS_TIMING") != "empirical":
            raise ValueError("Set MERLIN_IRIS_TIMING=empirical")
        if os.environ.get("MERLIN_IRIS_ALLOW_EXPERIMENTAL_TIMING") != "1":
            raise ValueError("Set MERLIN_IRIS_ALLOW_EXPERIMENTAL_TIMING=1")
        return verified_model(manifest)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as error:
        return dict(available=False, reason=str(error), timing_available=False,
                    accuracy_2percent_certified=False, whole_model_certified=False)
