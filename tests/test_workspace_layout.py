import hashlib
from pathlib import Path

import pytest

from iris.workspace_layout import DeclaredDram, WorkspaceLayout


def layout(**changes):
    values = {
        "dram": DeclaredDram(0x80000000, 4 << 30, "/memory@80000000"),
        "low_bytes": 1536 << 20,
        "reserve_bytes": 2560 << 20,
    }
    values.update(changes)
    return WorkspaceLayout(**values)


def sdk_linker(tmp_path):
    p = tmp_path / "sdk.ld"
    p.write_text(
        "/* unchanged */\nIDT_LIST (wx) : ORIGIN = 0xfffff7ff, LENGTH = 2K\n/* retained */\n"
    )
    return p


def test_exact_partition_and_nonload_reservation(tmp_path):
    p = layout()
    assert p.high_origin == 0xE0000000
    assert p.high_bytes == 2560 << 20
    assert p.high_origin + p.high_bytes == p.dram.origin + p.dram.bytes
    out = tmp_path / "new"
    record = p.write(out, linker_source=sdk_linker(tmp_path))
    assert record["physical_image_memory_verified"] is False
    assert record["automatic_initialization"] is False
    assert "@nobits" in (out / "workspace.S").read_text()
    assert ".space 2684354560" in (out / "workspace.S").read_text()
    ld = (out / "workspace.ld").read_text()
    assert "NOLOAD" in ld and "__bss_end" in ld and "_image_ram_end" in ld
    assert "SIZEOF(.iris_model_workspace) == 2684354560" in ld
    assert "GROUP_NOLOAD_LINK_IN(IRIS_MODEL_WORKSPACE" in ld
    overlay = (out / "workspace.overlay").read_text()
    assert "&{/memory@80000000}" in overlay
    assert "0x0 0x80000000 0x0 0x60000000" in overlay
    assert "0x0 0xe0000000 0x0 0xa0000000" in overlay
    assert '"+r"(base)' in (out / "workspace.h").read_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"low_bytes": True},
        {"low_bytes": 0},
        {"reserve_bytes": 0},
        {"low_bytes": 1536 * (1 << 20) + 1},
        {"reserve_bytes": 65},
        {"reserve_bytes": (2560 << 20) + 64},
        {"low_bytes": 4 << 30},
        {"low_bytes": 2 << 30},
        {"alignment": 3},
        {"alignment": 4},
        {"alignment": 0},
        {"near_margin_bytes": 0},
        {"near_margin_bytes": 1 << 40},
        {"dram": DeclaredDram(1, 4 << 30, "/memory@1")},
    ],
)
def test_invalid_bounds_decline(changes):
    with pytest.raises(ValueError):
        layout(**changes)


@pytest.mark.parametrize(
    "origin,size,path",
    [
        (-1, 4096, "/memory@0"),
        (0, 0, "/memory@0"),
        ((1 << 64) - 64, 128, "/memory@0"),
        (True, 4096, "/memory@0"),
        (0, 4096, "/"),
        (0, 4096, "/memory@0};bad"),
        (0, 4096, "/x\ny"),
    ],
)
def test_bad_declared_source(origin, size, path):
    with pytest.raises(ValueError):
        DeclaredDram(origin, size, path)


def test_explicit_subcapacity_and_other_dram():
    p = WorkspaceLayout(
        DeclaredDram(0x40000000, 3 << 30, "/memory@40000000"),
        1 << 30,
        64 << 20,
        alignment=128,
    )
    assert p.high_origin == 0x80000000
    assert p.high_bytes == 2 << 30
    assert ".space 67108864" in p.reservation()
    assert p.record()["workspace_region_bytes"] != p.reserve_bytes


def test_existing_output_is_immutable(tmp_path):
    d = tmp_path / "existing"
    d.mkdir()
    sentinel = d / "keep"
    sentinel.write_text("same")
    with pytest.raises(FileExistsError):
        layout().write(d, linker_source=sdk_linker(tmp_path))
    assert list(d.iterdir()) == [sentinel]
    assert sentinel.read_text() == "same"


def test_changed_source_refuses_before_output(tmp_path):
    source = tmp_path / "source"
    source.write_text("original")
    d = DeclaredDram(
        0x80000000,
        4 << 30,
        "/memory@80000000",
        ((str(source), hashlib.sha256(source.read_bytes()).hexdigest()),),
    )
    p = layout(dram=d)
    source.write_text("changed")
    with pytest.raises(ValueError, match="source changed"):
        p.write(tmp_path / "refused", linker_source=sdk_linker(tmp_path))
    assert not (tmp_path / "refused").exists()


def test_no_zeroing_or_ownership_permission():
    p = layout()
    r = p.record()
    assert r["NOLOAD"] and not r["automatic_initialization"]
    assert "PT_LOAD" in r["loader_cost_warning"]
    assert "quiescence" in r["initialization_contract"]
    assert "memset" not in p.reservation()


def test_real_selected_sdk_parser_and_seal(tmp_path):
    import os

    from iris.workspace_layout import from_zephyr_dts

    value = os.environ.get("IRIS_TEST_DTLIB")
    if not value:
        pytest.skip("explicit real Zephyr dtlib source required")
    dts = tmp_path / "generated.dts"
    dts.write_text("""/dts-v1/;
/ { #address-cells=<2>; #size-cells=<2>;
 chosen { zephyr,sram=&dram; };
 dram: memory@80000000 { device_type="memory"; reg=<0 0x80000000 1 0>; };
};
""")
    d = from_zephyr_dts(dts, Path(value))
    assert d.origin == 0x80000000 and d.bytes == 4 << 30
    assert d.node_path == "/memory@80000000"
    assert len(d.input_sha256) == 2
    p = layout(dram=d)
    dts.write_text(dts.read_text().replace("1 0>", "0 0x40000000>"))
    with pytest.raises(ValueError, match="source changed"):
        p.write(tmp_path / "stale", linker_source=sdk_linker(tmp_path))


@pytest.mark.parametrize(
    "text", ['/include/ "other.dtsi"', '/incbin/ ("raw.bin")', '#include "x"']
)
def test_preprocessed_input_required(tmp_path, text):
    from iris.workspace_layout import from_zephyr_dts

    dts, parser = tmp_path / "input", tmp_path / "parser.py"
    dts.write_text(text)
    parser.write_text('raise AssertionError("must not execute")')
    with pytest.raises(ValueError, match="preprocessed"):
        from_zephyr_dts(dts, parser)


def test_sdk_metadata_only_is_relocated(tmp_path):
    source = sdk_linker(tmp_path)
    before = source.read_text()
    script, record = layout().custom_linker(source)
    assert source.read_text() == before
    assert script == before.replace("0xfffff7ff", "0x7ffff800")
    assert record["only_IDT_LIST_origin_changed"]
    assert record["origin"] + record["bytes"] == layout().dram.origin
    assert not record["runtime_region"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "IDT_LIST (wx) : ORIGIN = f(), LENGTH = 2K",
        "IDT_LIST (wx) : ORIGIN = 0, LENGTH = unknown",
        "IDT_LIST (rx) : ORIGIN = 0, LENGTH = 2K",
        "IDT_LIST (wx) : ORIGIN = 0, LENGTH = 7",
    ],
)
def test_unknown_linker_metadata_contract_declines(tmp_path, text):
    source = tmp_path / "unknown.ld"
    source.write_text(text)
    with pytest.raises(ValueError):
        layout().write(tmp_path / "refuse", linker_source=source)
    assert not (tmp_path / "refuse").exists()
