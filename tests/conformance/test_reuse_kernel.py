"""Admission checks for the opt-in kernel; native numerical evidence is separate."""
from pathlib import Path
import ctypes
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_reuse_is_explicit_and_does_not_replace_default():
    from merlin.kernels import matrix_kernels as kernels

    registry = kernels.load_registry(ROOT)
    assert registry.default_for("saturn_vme").generated
    choice = kernels.resolve("vme_gemm_1x2_reuse", package_root=ROOT, unit="saturn_vme")
    assert choice.function == "vme_gemm_1x2_reuse"
    assert choice.entry.constraints.bias is False
    assert choice.entry.constraints.violations(3, 65, 7, tile_edge=32) == []


def _preprocessed(tmp_path, *, parallel):
    # No generated encoding is needed for preprocessing. Macro definitions
    # supplied by saturn_vme.h are intentionally only placeholders here.
    (tmp_path / "saturn_vme.h").write_text("#define VME_KSTEP 4\n#define VME_ROW_STRIDE 2\n")
    argv = ["cc", "-E", "-P", "-I", str(tmp_path), "-I", str(ROOT / "kernels")]
    if parallel:
        argv.append("-D_OPENMP=201511")
    argv.append(str(ROOT / "kernels/vme_gemm_1x2_reuse.c"))
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def test_parallel_build_contains_only_original_partitioned_path(tmp_path):
    source = _preprocessed(tmp_path, parallel=True)
    assert "gemm_1x2_k4(" not in source
    assert "store_two_rows(" not in source
    assert "#pragma omp parallel for collapse(2) schedule(static)" in source
    assert "gemm_tile_bias(" in source


def test_single_compute_candidate_requires_actual_compatible_readout(tmp_path):
    source = _preprocessed(tmp_path, parallel=False)
    assert "gemm_1x2_k4(" in source
    assert "reuse_readout_legal(te)" in source
    assert "vl == te" in source and "type & vill" in source
    assert "if(!bias && m<=te && n>=2*te && reuse_readout_legal(te))" in source


def test_geometry_guard_rejects_wrong_lmul_sew_vill_or_truncated_vl(tmp_path):
    source = (ROOT / "kernels/vme_gemm_1x2_reuse.c").read_text()
    start = source.index("static inline int reuse_readout_legal")
    end = source.index("\n#endif", start)
    guard = source[start:end]
    # Substitute only the architectural CSR observation, then execute the
    # actual scalar admission function. This is not a VME arithmetic model.
    csr = 'asm volatile("csrr %0, vtype" : "=r"(type) :: "memory");'
    assert guard.count(csr) == 1
    guard = guard.replace(csr, "type = observed_type;")
    program = '''#include <stdint.h>
#include <stddef.h>
static size_t observed_vl;
static uintptr_t observed_type;
static size_t vme_vsettnt_e32w1(size_t n) { (void)n; return observed_vl; }
''' + guard + '''
int probe(size_t vl, uintptr_t type, size_t edge) {
  observed_vl = vl; observed_type = type; return reuse_readout_legal(edge);
}
'''
    c = tmp_path / "guard.c"
    c.write_text(program)
    library = tmp_path / "guard.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O2", str(c), "-o", str(library)], check=True)
    probe = ctypes.CDLL(str(library)).probe
    probe.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t]
    probe.restype = ctypes.c_int
    for edge in (16, 32, 64, 128):
        assert probe(edge, 0xD2, edge) == 1  # e32, LMUL4, no vill
        assert probe(edge - 1, 0xD2, edge) == 0
        assert probe(edge, 0xD3, edge) == 0  # LMUL8
        assert probe(edge, 0xCA, edge) == 0  # e16
        assert probe(edge, 0xD2 | (1 << (8 * ctypes.sizeof(ctypes.c_size_t) - 1)), edge) == 0
