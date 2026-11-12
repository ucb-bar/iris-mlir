"""The hand-written kernels in kernels/: the registry, and vme_api.h against the saturn benchmarks' vme.h.

vme_api.h promises vme.h's API with every word taken from the header Merlin generates from the RTL. The
check here is that promise, instruction by instruction: each macro and function is expanded through BOTH
headers (the author's vme.h, and vme_api.h over the generated saturn_vme.h) and the .insn directives must
name the same opcode, funct3, funct7 and operands. It needs the pinned sources ($MERLIN_CHIPYARD_IRIS) and a
host C preprocessor; the registry checks need neither.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_the_registry_names_the_generated_default_and_the_port():
    from merlin.compiler.selection import kernels as MK

    reg = MK.load_registry(ROOT)
    assert reg.header == "saturn_vme.h" and reg.header_prefix == "VME"
    assert reg.default_for("saturn_vme").generated
    port = MK.resolve("vme_gemm_2x2_bias", package_root=ROOT, unit="saturn_vme")
    assert port.function == "vme_gemm_2x2_bias" and port.entry.constraints.any_shape and port.sha256
    assert not (ROOT / "kernels" / reg.header).exists(), "the header is generated at build time, never committed"


def test_the_kernel_header_facts_are_derived_values(monkeypatch):
    from merlin.targetgen.plugins import load_module

    shim = load_module(ROOT, "vme_shim.py", package_name="iris_test_facts")
    contract = shim.load_contract("saturn_vme")

    class _Spec:  # what kernel_spec() derives from the RTL, stood in so this runs without the sources
        kstep, row_stride, operand_type, readout_type = 4, 2, (8, 4), (32, 1)
        sew_code, readout_sew_code, operand_bits, acc_bits = 0, 2, 8, 32
        layout = shim.vme_kernel.OperandLayout(**{k: int(v) for k, v in contract.operand_layout.items()})
        widen_code = staticmethod(shim.vme_kernel.VmeKernelSpec.widen_code)

    monkeypatch.setattr(shim.UnitContract, "kernel_spec", lambda self: _Spec())
    facts = {k: v[0] for k, v in shim.kernel_header_facts(unit="saturn_vme", config="IrisConfig").items()}
    lay = contract.operand_layout
    assert facts["KSTEP"] == 4 and facts["OPERAND_MTYPE"] == 3 << lay["mtype_widen_lsb"]
    assert facts["OPERAND_VTYPE_BSIGNED"] == 1 << lay["vtype_altfmt_bit"]
    assert facts["MT_RD_2"] == 2 << lay["tile_rd_shift"] and facts["MTB_RD_2"] == facts["MT_RD_2"] | 1


@pytest.fixture
def headers(tmp_path):
    from merlin.common.paths import env

    root = env("MERLIN_CHIPYARD_IRIS")
    vme_h = Path(root or "/nonexistent") / "generators" / "saturn" / "benchmarks" / "common" / "vme.h"
    if not vme_h.is_file():
        pytest.skip("MERLIN_CHIPYARD_IRIS with the saturn_vme sources is not configured")
    if shutil.which("cc") is None:
        pytest.skip("no host C preprocessor")
    from merlin.compiler.selection import kernels as MK
    from merlin.compiler.backend.headers import unit_header
    from merlin.targetgen.plugins import load_module

    plugin = load_module(ROOT, "matrix_lowering.py", package_name="iris_test_api")
    (tmp_path / "saturn_vme.h").write_text(
        unit_header(plugin, unit="saturn_vme", config="IrisConfig", header="saturn_vme.h", prefix="VME")
    )
    shutil.copy(ROOT / "kernels" / "vme_api.h", tmp_path / "vme_api.h")
    (tmp_path / "author").mkdir()
    shutil.copy(vme_h, tmp_path / "author" / "vme.h")
    return tmp_path


_USES = """
void t_mm_s(void) { VME_MM_S_S(1, 8, 24); }
void t_mm_u(void) { VME_MM_U_U(3, 0, 16); }
void t_zero(void) { VME_VTZERO(2); }
void t_discard(void) { VME_VTDISCARD(); }
void t_v_t(size_t tss) { VME_VTMV_V_T(16, tss); }
void t_t_v(size_t tss) { VME_VTMV_T_V(tss, 8); }
"""
_FUNCTIONS = ("vme_msetmtype_raw", "vme_vsettn", "vme_vsettm", "vme_vsettk")


def _strings_after(text: str, start: int) -> str:
    """Adjacent C string literals from ``start`` on, concatenated (a tiny tokenizer, no regex)."""
    out, i = [], start
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i + 1 : j])
            i = j + 1
        else:
            break
    return "".join(out)


def _insn_in(text: str, owner: str) -> tuple:
    """The (opcode, funct3, funct7, rd, rs1, rs2) of the first .insn inside the function ``owner``."""
    at = text.find(owner + "(")
    while at >= 0 and text.find("{", at) > text.find(";", at):
        at = text.find(owner + "(", at + 1)  # a call or prototype, not the definition
    body = text[text.find("{", at) : text.find("}", at)]
    asm = body.find("volatile(")
    insn = _strings_after(body, asm + len("volatile("))
    head, _, ops = insn.partition(".insn r ")
    fields = [f.strip() for f in ops.split(",")]
    return tuple(int(f, 0) for f in fields[:3]) + tuple(fields[3:])


def _preprocess(work: Path, include: str, extra: str = "") -> str:
    src = work / f"use_{include.replace('.', '_')}.c"
    src.write_text(f'#include <stddef.h>\n#include <stdint.h>\n#include "{include}"\n{extra}{_USES}')
    got = subprocess.run(
        ["cc", "-E", "-P", "-I", str(work), "-I", str(work / "author"), str(src)], capture_output=True, text=True
    )
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_vme_api_spells_every_word_as_vme_h_does(headers):
    mine = _preprocess(headers, "vme_api.h")
    theirs = _preprocess(headers, "vme.h")
    for owner in ("t_mm_s", "t_mm_u", "t_zero", "t_discard", "t_v_t", "t_t_v", *_FUNCTIONS):
        assert _insn_in(mine, owner) == _insn_in(theirs, owner), owner


def test_the_operand_spellings_and_tss_agree(headers, tmp_path):
    prog = """
#include <stdio.h>
int main(void) {
  printf("%s %s %s %zu\\n", VME_MT(3), VME_MTB(1), VME_V(24), vme_tss(3, VME_TSS_ROW, 17));
  return 0;
}
"""
    outs = []
    for include in ("vme_api.h", "vme.h"):
        src = tmp_path / f"p_{include.replace('.', '_')}.c"
        src.write_text(f'#include <stddef.h>\n#include <stdint.h>\n#include "{include}"\n{prog}')
        exe = tmp_path / f"p_{include.replace('.', '_')}"
        subprocess.run(["cc", "-I", str(headers), "-I", str(headers / "author"), str(src), "-o", str(exe)], check=True)
        outs.append(subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout)
    assert outs[0] == outs[1]
