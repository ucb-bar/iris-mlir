import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/campaigns"))
import operator_harness as H
import operator_suite as G


@pytest.fixture
def fixture(tmp_path):
    spec = {
        "schema": "iris.compiled_operator_suite.v1",
        "target": {"vlen_bits": 256, "dlen_bits": 128, "compute_harts": [0]},
        "variants": ["rvv", "vme_full"],
        "max_logical_bytes_per_case": 10000,
        "cases": [
            {"id": "tail", "kind": "gemm", "split": "smoke", "m": 2, "n": 3, "k": 5}
        ],
    }
    manifest = G.emit(spec, tmp_path / "sources")
    bound = H.generate(manifest["cases"][0], spec["target"], tmp_path / "harness")
    region = {
        "case_id": "tail",
        "region": "operator",
        "hart": 0,
        "start_cycle": 10,
        "end_cycle": 100,
        "start_instret": 20,
        "end_instret": 90,
        "instret_delta": 70,
        "checksum": bound["expected_checksum"],
        "expected_checksum": bound["expected_checksum"],
        "compared_words": 6,
        "mismatch_words": 0,
        "guard_errors": 0,
        "input_errors": 0,
        "vlen_bytes": 32,
        "pass": True,
    }
    packet = {
        "case_id": "tail",
        "words": 6,
        "data": Path(bound["golden"]["path"]).read_bytes().hex(),
    }
    done = {"cases": 1, "regions": 1, "failed": 0}
    return bound, region, packet, done


def console(region, packet, done):
    return "\n".join(
        (
            "IRIS_REGION " + json.dumps(region),
            "IRIS_OUTPUT_HEX " + json.dumps(packet),
            "IRIS_BATCH_DONE " + json.dumps(done),
            "EXIT 0",
        )
    )


def test_all_words_are_independently_compared(fixture):
    bound, region, packet, done = fixture
    report = H.check_console(
        console(region, packet, done), bound, "functional_instructions"
    )
    assert report["pass"] and report["physical_utilization"] is None
    corrupted = copy.deepcopy(packet)
    corrupted["data"] = (
        bytes([int(packet["data"][:2], 16) ^ 1]).hex() + packet["data"][2:]
    )
    with pytest.raises(ValueError, match="raw output"):
        H.check_console(
            console(region, corrupted, done), bound, "functional_instructions"
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("hart", False),
        ("compared_words", 5),
        ("vlen_bytes", 16),
        ("guard_errors", 1),
        ("input_errors", 1),
        ("end_cycle", 1),
        ("instret_delta", 69),
    ],
)
def test_incomplete_or_inconsistent_guest_is_refused(fixture, key, value):
    bound, region, packet, done = fixture
    region[key] = value
    with pytest.raises(ValueError):
        H.check_console(console(region, packet, done), bound, "functional_instructions")


def test_duplicate_and_boolean_footer_are_refused(fixture):
    bound, region, packet, done = fixture
    text = console(region, packet, done)
    with pytest.raises(ValueError):
        H.check_console(
            text + "\nIRIS_REGION " + json.dumps(region),
            bound,
            "functional_instructions",
        )
    done["failed"] = False
    with pytest.raises(ValueError):
        H.check_console(console(region, packet, done), bound, "functional_instructions")


def test_changed_golden_bytes_are_refused(fixture):
    bound, region, packet, done = fixture
    Path(bound["golden"]["path"]).write_bytes(b"\0" * 24)
    with pytest.raises(ValueError, match="bound input changed"):
        H.check_console(console(region, packet, done), bound, "functional_instructions")


def test_duplicate_json_fields_are_refused(fixture):
    bound, region, packet, done = fixture
    text = console(region, packet, done).replace('"hart": 0', '"hart": 1, "hart": 0')
    with pytest.raises(ValueError, match="duplicate JSON"):
        H.check_console(text, bound, "functional_instructions")


def test_single_worker_driver_rejects_another_worker_declaration(tmp_path):
    with pytest.raises(ValueError, match="compute_harts"):
        H.generate({}, {"compute_harts": [1]}, tmp_path / "unsupported")
    assert not (tmp_path / "unsupported").exists()
