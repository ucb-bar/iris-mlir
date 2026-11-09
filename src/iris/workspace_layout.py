"""Explicit Zephyr DRAM carveout for a caller-owned outlined workspace.

This source-derived software map is not proof of an FPGA image's memory size.
Nothing changes unless a caller installs the emitted overlay and linker snippet.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _integer(value: int, name: str, *, positive: bool = True) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value >= 1 << 64:
        raise ValueError(f"{name}: explicit bounded integer required")
    return value


@dataclass(frozen=True)
class DeclaredDram:
    origin: int
    bytes: int
    node_path: str
    input_sha256: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        _integer(self.origin, "DRAM origin", positive=False)
        _integer(self.bytes, "DRAM bytes")
        if self.origin + self.bytes >= 1 << 64:
            raise ValueError("DRAM end overflows a 64-bit address")
        allowed = (
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/@,.-+"
        )
        if (
            not isinstance(self.node_path, str)
            or not self.node_path.startswith("/")
            or self.node_path == "/"
            or "//" in self.node_path
            or any(c not in allowed for c in self.node_path)
        ):
            raise ValueError("complete safe declared memory-node path required")

    def verify(self):
        for name, digest in self.input_sha256:
            if _sha(Path(name)) != digest:
                raise ValueError("declared-memory source changed: " + name)


def from_zephyr_dts(dts: Path, dtlib: Path) -> DeclaredDram:
    """Use the caller's actual Zephyr parser on a closed generated DTS file."""
    dts, dtlib = Path(dts).resolve(strict=True), Path(dtlib).resolve(strict=True)
    pins = tuple((str(p), _sha(p)) for p in (dts, dtlib))
    text = dts.read_text()
    if (
        "/include/" in text
        or "/incbin/" in text
        or any(
            line.split()
            and line.split()[0]
            in {"#include", "#define", "#if", "#ifdef", "#ifndef", "#line"}
            for line in text.splitlines()
        )
    ):
        raise ValueError("use a closed, already preprocessed generated zephyr.dts")
    name = "_iris_workspace_dtlib_" + hashlib.sha256(str(dtlib).encode()).hexdigest()
    module = sys.modules.get(name)
    if module is not None:
        if (
            Path(module.__file__).resolve() != dtlib
            or getattr(module, "_iris_source_sha", None) != pins[1][1]
        ):
            raise ValueError(
                "cached devicetree parser differs from the selected source"
            )
    else:
        spec = importlib.util.spec_from_file_location(name, dtlib)
        if spec is None or spec.loader is None:
            raise ValueError("cannot load explicit Zephyr devicetree parser")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module._iris_source_sha = pins[1][1]
    tree = module.DT(str(dts))
    node = tree.get_node("/chosen").props["zephyr,sram"].to_path()
    if node.props["device_type"].to_string() != "memory" or node.parent is None:
        raise ValueError("chosen SRAM is not an explicit memory node")
    ac = node.parent.props["#address-cells"].to_num()
    sc = node.parent.props["#size-cells"].to_num()
    values = node.props["reg"].to_nums()
    if ac not in (1, 2) or sc not in (1, 2) or len(values) != ac + sc:
        raise ValueError("one explicitly sized 32/64-bit DRAM tuple required")

    def combine(cells):
        result = 0
        for cell in cells:
            result = (result << 32) | cell
        return result

    result = DeclaredDram(combine(values[:ac]), combine(values[ac:]), node.path, pins)
    result.verify()
    return result


@dataclass(frozen=True)
class WorkspaceLayout:
    dram: DeclaredDram
    low_bytes: int
    reserve_bytes: int
    alignment: int = 64
    near_margin_bytes: int = 4096

    def __post_init__(self):
        if not isinstance(self.dram, DeclaredDram):
            raise TypeError("explicit declared DRAM source required")
        for name in ("low_bytes", "reserve_bytes", "alignment", "near_margin_bytes"):
            _integer(getattr(self, name), name)
        if self.alignment & (self.alignment - 1) or self.alignment < 8:
            raise ValueError(
                "workspace alignment must be a power of two of at least eight"
            )
        if self.low_bytes % 1024 or self.low_bytes % self.alignment:
            raise ValueError("low Zephyr RAM must be KiB and workspace aligned")
        if self.dram.origin % self.alignment or self.dram.bytes % self.alignment:
            raise ValueError("declared DRAM endpoints must be workspace aligned")
        if self.low_bytes >= self.dram.bytes or self.reserve_bytes > self.high_bytes:
            raise ValueError(
                "low RAM and workspace must fit disjointly inside declared DRAM"
            )
        if self.reserve_bytes % self.alignment:
            raise ValueError("workspace reservation must be alignment sized")
        if self.low_bytes > (1 << 31) - self.near_margin_bytes:
            raise ValueError(
                "workspace base exceeds the explicit medany near-anchor budget"
            )
        self.dram.verify()

    @property
    def high_origin(self):
        return self.dram.origin + self.low_bytes

    @property
    def high_bytes(self):
        return self.dram.bytes - self.low_bytes

    def overlay(self) -> str:
        self.dram.verify()

        def cells(value):
            return f"0x{value >> 32:x} 0x{value & 0xFFFFFFFF:x}"

        return f"""/* Explicit software map; physical image association is external. */
&{{{self.dram.node_path}}} {{ reg = <{cells(self.dram.origin)} {cells(self.low_bytes)}>; }};
/ {{
  iris_model_workspace: memory@{self.high_origin:x} {{
    compatible = "zephyr,memory-region";
    reg = <{cells(self.high_origin)} {cells(self.high_bytes)}>;
    zephyr,memory-region = "IRIS_MODEL_WORKSPACE";
    zephyr,memory-region-flags = "rw";
    status = "okay";
  }};
}};
"""

    def linker(self) -> str:
        self.dram.verify()
        return f"""/* Install with zephyr_linker_sources(RAM_SECTIONS ...). */
SECTION_PROLOGUE(.iris_model_workspace,(NOLOAD),)
{{
  . = ALIGN({self.alignment});
  __iris_workspace_start = .;
  KEEP(*(.iris_model_workspace))
  __iris_workspace_used_end = .;
}} GROUP_NOLOAD_LINK_IN(IRIS_MODEL_WORKSPACE, IRIS_MODEL_WORKSPACE)
ASSERT(ORIGIN(RAM) == {self.dram.origin}, "changed low RAM origin")
ASSERT(LENGTH(RAM) == {self.low_bytes}, "changed low RAM capacity")
ASSERT(ORIGIN(IRIS_MODEL_WORKSPACE) == {self.high_origin}, "changed workspace origin")
ASSERT(LENGTH(IRIS_MODEL_WORKSPACE) == {self.high_bytes}, "changed workspace capacity")
ASSERT(ORIGIN(RAM)+LENGTH(RAM) == ORIGIN(IRIS_MODEL_WORKSPACE), "memory regions overlap or gap")
ASSERT(ORIGIN(IRIS_MODEL_WORKSPACE)+LENGTH(IRIS_MODEL_WORKSPACE) == {self.dram.origin + self.dram.bytes}, "changed DRAM end")
ASSERT(__iris_workspace_start == ORIGIN(IRIS_MODEL_WORKSPACE), "workspace base must be near anchor")
ASSERT(SIZEOF(.iris_model_workspace) == {self.reserve_bytes}, "workspace reservation differs")
ASSERT(__iris_workspace_used_end <= ORIGIN(IRIS_MODEL_WORKSPACE)+LENGTH(IRIS_MODEL_WORKSPACE), "workspace overflow")
ASSERT(__bss_end < {self.dram.origin + (1 << 31) - self.near_margin_bytes}, "BSS boot anchor outside medany budget")
ASSERT(_image_ram_end < {self.dram.origin + (1 << 31) - self.near_margin_bytes}, "OS end anchor outside medany budget")
"""

    def reservation(self) -> str:
        self.dram.verify()
        return f""".section .iris_model_workspace,"aw",@nobits
.balign {self.alignment}
.globl iris_model_workspace
.type iris_model_workspace,@object
iris_model_workspace:
.space {self.reserve_bytes}
.size iris_model_workspace,.-iris_model_workspace
"""

    def header(self) -> str:
        self.dram.verify()
        return f"""#ifndef IRIS_MODEL_WORKSPACE_H
#define IRIS_MODEL_WORKSPACE_H
#include <stdint.h>
#include <stddef.h>
#define IRIS_MODEL_WORKSPACE_BYTES UINT64_C({self.reserve_bytes})
#define IRIS_MODEL_WORKSPACE_ALIGNMENT {self.alignment}
extern unsigned char iris_model_workspace[IRIS_MODEL_WORKSPACE_BYTES];
_Static_assert(sizeof(uintptr_t)==8, "workspace requires observed RV64 LP64");
/* Keep later large constant offsets relative to a runtime base register,
 * rather than folding them into a far PC-relative symbol relocation. */
static inline unsigned char *iris_model_workspace_base(void) {{
  unsigned char *base=iris_model_workspace;
  __asm__ volatile("" : "+r"(base));
  return base;
}}
#endif
"""

    def record(self) -> dict:
        self.dram.verify()
        return {
            "schema": "iris.explicit_workspace_layout.v1",
            "declared_dram_origin": self.dram.origin,
            "declared_dram_bytes": self.dram.bytes,
            "low_bytes": self.low_bytes,
            "workspace_origin": self.high_origin,
            "workspace_region_bytes": self.high_bytes,
            "workspace_reserved_bytes": self.reserve_bytes,
            "alignment": self.alignment,
            "near_margin_bytes": self.near_margin_bytes,
            "NOLOAD": True,
            "automatic_initialization": False,
            "initialization_contract": "caller initializes before use; quiescence and all ownership/guards remain required",
            "loader_cost_warning": "NOBITS may still enlarge PT_LOAD p_memsz and cause loader zero writes; inspect final ELF",
            "physical_image_memory_verified": False,
            "source_input_sha256": dict(self.dram.input_sha256),
        }

    def custom_linker(self, source: Path) -> tuple[str, dict]:
        """Relocate only Zephyr's discarded ISR-table build metadata region."""
        self.dram.verify()
        source = Path(source).resolve(strict=True)
        digest = _sha(source)
        text = source.read_text()
        lines = text.splitlines(keepends=True)
        candidates = [
            i for i, line in enumerate(lines) if line.strip().startswith("IDT_LIST ")
        ]
        if len(candidates) != 1:
            raise ValueError("one explicit SDK IDT_LIST region required")
        index = candidates[0]
        tokens = lines[index]
        for mark in "():,":
            tokens = tokens.replace(mark, " ")
        words = tokens.split()
        if (
            len(words) != 8
            or words[:4] != ["IDT_LIST", "wx", "ORIGIN", "="]
            or words[5:7] != ["LENGTH", "="]
        ):
            raise ValueError("unsupported SDK ISR metadata region syntax")
        try:
            old_origin = int(words[4], 0)
            size_text = words[7]
            multiplier = 1024 if size_text.endswith("K") else 1
            size = int(size_text[:-1] if multiplier != 1 else size_text, 0) * multiplier
        except ValueError as exc:
            raise ValueError("literal SDK ISR metadata bounds required") from exc
        if size <= 0 or size % 8 or self.dram.origin < size:
            raise ValueError(
                "no aligned build-only metadata region below declared DRAM"
            )
        origin = self.dram.origin - size
        if origin % 8:
            raise ValueError("unaligned build-only ISR metadata origin")
        lines[index] = lines[index].replace(words[4], hex(origin), 1)
        updated = "".join(lines)
        if _sha(source) != digest:
            raise ValueError("SDK linker source changed while copying")
        return updated, {
            "source": str(source),
            "source_sha256": digest,
            "old_origin": old_origin,
            "origin": origin,
            "bytes": size,
            "only_IDT_LIST_origin_changed": True,
            "runtime_region": False,
            "final_ELF_must_discard_intList": True,
        }

    def write(self, output: Path, *, linker_source: Path) -> dict:
        self.dram.verify()
        script, metadata = self.custom_linker(linker_source)
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        for name, text in {
            "workspace.overlay": self.overlay(),
            "workspace.ld": self.linker(),
            "workspace.S": self.reservation(),
            "workspace.h": self.header(),
            "zephyr-linker.ld": script,
            "workspace.conf": "CONFIG_HAVE_CUSTOM_LINKER_SCRIPT=y\nCONFIG_CUSTOM_LINKER_SCRIPT="
            + json.dumps(str((output / "zephyr-linker.ld").resolve()))
            + "\n",
        }.items():
            (output / name).write_text(text)
        record = self.record()
        record["build_only_ISR_metadata"] = metadata
        record["emitted_sha256"] = {
            str(p): _sha(p) for p in output.iterdir() if p.is_file()
        }
        self.dram.verify()
        if _sha(Path(metadata["source"])) != metadata["source_sha256"]:
            raise ValueError("SDK linker source changed before publication")
        (output / "layout.json").write_text(json.dumps(record, indent=2) + "\n")
        return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dts", type=Path, required=True)
    parser.add_argument("--dtlib", type=Path, required=True)
    parser.add_argument("--linker-source", type=Path, required=True)
    parser.add_argument("--low-bytes", type=int, required=True)
    parser.add_argument("--reserve-bytes", type=int, required=True)
    parser.add_argument("--alignment", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = WorkspaceLayout(
        from_zephyr_dts(args.dts, args.dtlib),
        args.low_bytes,
        args.reserve_bytes,
        args.alignment,
    ).write(args.output, linker_source=args.linker_source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
