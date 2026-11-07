"""Frozen-release admission boundaries; these tests never contact hardware."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("iris_simulator_under_test", ROOT / "src/iris/simulator.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class ReleaseAdmission(unittest.TestCase):
    def test_explicit_selection_requires_both_opt_ins(self):
        for environment in (
            {"MERLIN_IRIS_MODEL_MANIFEST": "/missing"},
            {"MERLIN_IRIS_MODEL_MANIFEST": "/missing", "MERLIN_IRIS_TIMING": "empirical"},
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                result = adapter.device_simulator(unit="saturn_vme", config="IrisConfig")
                self.assertIs(result["available"], False)
                self.assertIs(result["accuracy_2percent_certified"], False)

    def test_unknown_manifest_is_refused_before_provider_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "executed"
            provider = root / "provider.py"
            provider.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"profile": "source_compute", "model_provider": str(provider)}))
            with self.assertRaisesRegex(ValueError, "not one of the recorded"):
                adapter.verified_model(manifest)
            self.assertFalse(marker.exists())

    def test_explicit_bad_selection_never_uses_legacy_spike(self):
        # The shim normally imports Merlin only for the legacy path; a bad
        # explicit selection must return its refusal before entering that path.
        import sys

        sys.path.insert(0, str(ROOT / "src"))
        try:
            from iris.lower import vme_shim

            environment = {"MERLIN_IRIS_MODEL_MANIFEST": "/missing/manifest.json",
                           "MERLIN_IRIS_TIMING": "empirical",
                           "MERLIN_IRIS_ALLOW_EXPERIMENTAL_TIMING": "1",
                           "MERLIN_SPIKE_ZVT": "/bin/true"}
            with patch.dict(os.environ, environment, clear=True):
                result = vme_shim.device_simulator(unit="saturn_vme", config="IrisConfig")
            self.assertIs(result["available"], False)
            self.assertIn("manifest.json", result["reason"])
        finally:
            sys.path.pop(0)

    def test_other_machine_configurations_are_refused(self):
        for unit, config in (("saturn_vme", "OtherConfig"), ("other_unit", "IrisConfig")):
            with self.subTest(unit=unit, config=config):
                result = adapter.device_simulator(unit=unit, config=config)
                self.assertIs(result["available"], False)

    def test_source_and_fixture_bytes_match_the_recorded_capsule(self):
        root = ROOT / "simulator"
        snapshot = adapter.load(root / "snapshot.json")
        for name, digest in snapshot["file_sha256"].items():
            self.assertEqual(adapter.sha(root / name), digest, name)
        release = adapter.load(root / "release.json")
        original = adapter.load(root / "spike/original-capsule.json")
        self.assertEqual(release["source_pins"]["spike"], original["base_commit"])
        self.assertEqual(adapter.sha(root / "spike/source-overlay.patch"),
                         original["artifact_sha256"]["source-overlay.patch"])
        self.assertEqual(adapter.sha(root / "spike/native-inventory.json"),
                         original["artifact_sha256"]["native-inventory.json"])
        for name, record in release["profiles"].items():
            self.assertEqual(adapter.sha(root / "calibration" / (name + ".fixture")),
                             record["fixture_sha256"])
            self.assertEqual(set(record["per_hart_isa"]), {"0", "1", "2"})

    def test_cli_requires_an_explicit_artifact_location(self):
        result = subprocess.run([str(ROOT / "simulator/iris-run"), "check"],
                                env={k: v for k, v in os.environ.items() if k != "IRIS_MODEL_ROOT"},
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Select --model-root", result.stderr)


if __name__ == "__main__":
    unittest.main()
