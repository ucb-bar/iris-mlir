#!/usr/bin/env python3
"""Reconstruct the frozen native source and companion inputs in a fresh directory.

This prepares source for the Spike fork. It does not claim a standalone native
build: the original generated component SDK archives are external dependencies.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", default="https://github.com/riscv-software-src/riscv-isa-sim.git",
                        help="Upstream URL or a local clone containing the pinned commit")
    args = parser.parse_args()
    release = json.loads((ROOT / "release.json").read_text())
    snapshot = json.loads((ROOT / "snapshot.json").read_text())
    output = args.output.resolve()
    try:
        for name, digest in snapshot["file_sha256"].items():
            path = (ROOT / name).resolve()
            if ROOT not in path.parents or sha(path) != digest:
                raise ValueError("Source snapshot input changed: " + name)
        output.mkdir(parents=True, exist_ok=False)
        source = output / "pipeline/riscv-isa-sim"
        source.parent.mkdir()
        commands = [
            ["git", "clone", "--no-checkout", args.upstream, str(source)],
            ["git", "-C", str(source), "checkout", "--detach", release["source_pins"]["spike"]],
            ["git", "-C", str(source), "apply", "--check", str(ROOT / "spike/source-overlay.patch")],
            ["git", "-C", str(source), "apply", str(ROOT / "spike/source-overlay.patch")],
        ]
        with (output / "prepare.log").open("w") as log:
            for command in commands:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
        inventory = json.loads((ROOT / "spike/native-inventory.json").read_text())
        for name, digest in inventory.items():
            path = (source / name).resolve()
            if source not in path.parents or sha(path) != digest:
                raise ValueError("Reconstructed native source mismatch: " + name)
        shutil.copytree(ROOT / "model-source", output, dirs_exist_ok=True)
        report = dict(schema="iris.native_source_preparation.v1", source_tree_verified=True,
                      base_commit=release["source_pins"]["spike"],
                      native_source_files=len(inventory), snapshot_sha256=sha(ROOT / "snapshot.json"),
                      source=str(source), clean_native_rebuild_performed=False,
                      exact_component_SDK_archives_required=True, hardware_contacted=False)
        (output / "source-prepared.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, "prepare-source: " + str(error) + "\n")


if __name__ == "__main__":
    sys.exit(main())
