"""Host validation of controller packing/finish and fail-before-write rules.

Actual matrix instructions are covered by separate native proofs; these tests
do not substitute scalar host arithmetic for a claim of VME execution.
"""

import ctypes as C
import shutil
import subprocess

import numpy as np
import pytest

from iris.lower.split_vme import source_files, workspace_plan


class Buffers(C.Structure):
    _fields_ = (
        [(name, C.c_void_p) for name in ("a", "b", "output", "at", "bp", "cp")]
        + [
            (name, C.c_size_t)
            for name in (
                "a_bytes",
                "b_bytes",
                "output_bytes",
                "at_bytes",
                "bp_bytes",
                "cp_bytes",
                "m",
                "n",
                "k",
                "tile_edge",
                "alignment_bytes",
            )
        ]
        + [("sequence", C.c_uint64), ("worker_mask", C.c_uint32)]
    )


class Work(C.Structure):
    _fields_ = (
        [(name, C.c_void_p) for name in ("at", "bp", "cp")]
        + [
            (name, C.c_size_t)
            for name in (
                "m",
                "n",
                "k",
                "tile_edge",
                "alignment_bytes",
                "block_columns",
                "block_count",
            )
        ]
        + [("sequence", C.c_uint64), ("worker_mask", C.c_uint32)]
    )


class Guarded:
    def __init__(self, n):
        self.n = n
        self.storage = C.create_string_buffer(n + 192)
        self.address = (C.addressof(self.storage) + 127) & ~63
        C.memset(self.address - 64, 0x5A, n + 128)

    def bytes(self):
        return C.string_at(self.address, self.n)

    def check(self):
        assert C.string_at(self.address - 64, 64) == bytes([0x5A]) * 64
        assert C.string_at(self.address + self.n, 64) == bytes([0x5A]) * 64


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("host C compiler required")
    out = tmp_path_factory.mktemp("split-controller")
    src = source_files()
    role = out / "role.c"
    role.write_text("unsigned iris_vme_split_test_hart=2;\n")
    so = out / "prepare.so"
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-O2",
            "-shared",
            "-fPIC",
            "-DIRIS_VME_SPLIT_HOST_TEST",
            "-I",
            str(src["vme_split.h"].parent),
            str(src["vme_split_prepare.c"]),
            str(role),
            "-o",
            str(so),
        ],
        check=True,
    )
    lib = C.CDLL(str(so))
    for name in (
        "iris_vme_split_prepare",
        "iris_vme_split_finish",
        "iris_vme_split_prepare_readonly_b",
        "iris_vme_split_finish_readonly_b",
    ):
        getattr(lib, name).argtypes = [C.POINTER(Buffers), C.POINTER(Work)]
        getattr(lib, name).restype = C.c_int
    lib.iris_vme_split_validate_work.argtypes = [C.POINTER(Work)]
    lib.iris_vme_split_validate_work.restype = C.c_int
    return lib


def fixture(m, n, k, mask=3):
    plan = workspace_plan(m, n, k, tile_edge=32, alignment_bytes=16, worker_mask=mask)
    arrays = [
        Guarded(size) for size in (m * k, k * n, 4 * m * n, m * k, k * n, 4 * m * n)
    ]
    a = (
        (((np.arange(m * k, dtype=np.uint64) * 29 + 17) & 255).astype(np.int16) - 128)
        .astype(np.int8)
        .reshape(m, k)
    )
    b = (
        (((np.arange(k * n, dtype=np.uint64) * 37 + 28) & 255).astype(np.int16) - 128)
        .astype(np.int8)
        .reshape(k, n)
    )
    C.memmove(arrays[0].address, a.tobytes(), a.nbytes)
    C.memmove(arrays[1].address, b.tobytes(), b.nbytes)
    buffers = Buffers(
        *[a.address for a in arrays],
        *[a.n for a in arrays],
        m,
        n,
        k,
        plan.tile_edge,
        plan.alignment_bytes,
        1,
        mask,
    )
    work = Work()
    C.memset(C.addressof(work), 0xA5, C.sizeof(work))
    return buffers, work, arrays, a, b


@pytest.mark.parametrize(
    "shape", [(1, 1, 1), (1, 33, 7), (17, 65, 7), (64, 256, 128), (65, 67, 129)]
)
@pytest.mark.parametrize("mask", [1, 2, 3])
def test_pack_and_finish_full_buffers(library, shape, mask):
    s, w, arrays, a, b = fixture(*shape, mask=mask)
    C.c_uint.in_dll(library, "iris_vme_split_test_hart").value = 2
    assert library.iris_vme_split_prepare(C.byref(s), C.byref(w)) == 0
    assert library.iris_vme_split_validate_work(C.byref(w)) == 0
    assert arrays[3].bytes() == a.T.copy().tobytes()
    assert arrays[4].bytes() == b.tobytes()
    assert arrays[5].bytes() == bytes(arrays[5].n)
    # Host arithmetic only supplies a fixture for testing the finish copy.
    expected = (a.astype(np.int64) @ b.astype(np.int64)).astype("<i4").tobytes()
    C.memmove(arrays[5].address, expected, len(expected))
    assert library.iris_vme_split_finish(C.byref(s), C.byref(w)) == 0
    assert arrays[2].bytes() == expected
    assert arrays[0].bytes() == a.tobytes() and arrays[1].bytes() == b.tobytes()
    for array in arrays:
        array.check()


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("role0", 1),
        ("role1", 1),
        ("mask0", 2),
        ("mask4", 2),
        ("sequence0", 2),
        ("zeroK", 2),
        ("tile3", 2),
        ("overflow", 2),
        ("unsignedStride", 2),
        ("shortAt", 3),
        ("shortBp", 3),
        ("shortCp", 3),
        ("alignment", 5),
        ("aliasOutput", 4),
        ("aliasInput", 4),
        ("wrappedAddress", 2),
        ("metadataAlias", 4),
    ],
)
def test_refusal_writes_nothing(library, kind, expected):
    s, w, arrays, _, _ = fixture(17, 65, 7)
    C.c_uint.in_dll(library, "iris_vme_split_test_hart").value = (
        int(kind[-1]) if kind.startswith("role") else 2
    )
    changes = {
        "mask0": ("worker_mask", 0),
        "mask4": ("worker_mask", 4),
        "sequence0": ("sequence", 0),
        "zeroK": ("k", 0),
        "tile3": ("tile_edge", 3),
        "overflow": ("m", 2**64 - 1),
        "unsignedStride": ("k", 2**63),
        "shortAt": ("at_bytes", 1),
        "shortBp": ("bp_bytes", 1),
        "shortCp": ("cp_bytes", 1),
        "alignment": ("at", s.at + 1),
        "aliasOutput": ("output", s.cp),
        "aliasInput": ("bp", s.b),
        "wrappedAddress": ("at", 2**64 - 16),
    }
    if kind in changes:
        setattr(s, *changes[kind])
    target = (
        C.cast(arrays[3].address, C.POINTER(Work))
        if kind == "metadataAlias"
        else C.byref(w)
    )
    before = [a.bytes() for a in arrays]
    old = bytes(w)
    assert library.iris_vme_split_prepare(C.byref(s), target) == expected
    assert [a.bytes() for a in arrays] == before and bytes(w) == old
    for array in arrays:
        array.check()


def test_finish_rejects_changed_sequence_without_output(library):
    s, w, arrays, _, _ = fixture(17, 65, 7)
    C.c_uint.in_dll(library, "iris_vme_split_test_hart").value = 2
    assert library.iris_vme_split_prepare(C.byref(s), C.byref(w)) == 0
    w.sequence += 1
    before = arrays[2].bytes()
    assert library.iris_vme_split_finish(C.byref(s), C.byref(w)) == 6
    assert arrays[2].bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("m", True),
        ("k", 2**63),
        ("tile_edge", 3),
        ("worker_mask", 4),
        ("alignment_bytes", 2),
    ],
)
def test_plan_refuses_unknown_or_overflowing_geometry(field, value):
    args = {
        "m": 64,
        "n": 256,
        "k": 128,
        "tile_edge": 32,
        "alignment_bytes": 16,
        "worker_mask": 3,
    }
    args[field] = value
    with pytest.raises(ValueError):
        workspace_plan(**args)


@pytest.mark.parametrize("mask", [1, 2, 3])
@pytest.mark.parametrize("shape", [(1, 1, 1), (3, 65, 17), (32, 128, 64)])
def test_explicit_readonly_b_reuses_exact_input_without_writes(library, mask, shape):
    s, w, arrays, a, b = fixture(*shape, mask=mask)
    C.c_uint.in_dll(library, "iris_vme_split_test_hart").value = 2
    s.bp, s.bp_bytes = s.b, s.b_bytes
    assert library.iris_vme_split_prepare(C.byref(s), C.byref(w)) == 4
    for sequence in (1, 2, 3):
        s.sequence = sequence
        C.memset(arrays[5].address, 0xCC, arrays[5].n)
        assert library.iris_vme_split_prepare_readonly_b(C.byref(s), C.byref(w)) == 0
        assert w.bp == s.b and arrays[3].bytes() == a.T.copy().tobytes()
        assert arrays[5].bytes() == bytes(arrays[5].n)
        expected = (a.astype(np.int64) @ b.astype(np.int64)).astype("<i4").tobytes()
        C.memmove(arrays[5].address, expected, len(expected))
        assert library.iris_vme_split_finish_readonly_b(C.byref(s), C.byref(w)) == 0
        assert arrays[2].bytes() == expected and arrays[1].bytes() == b.tobytes()
        assert arrays[4].bytes() == bytes([0x5A]) * arrays[4].n
        for array in arrays:
            array.check()


@pytest.mark.parametrize(
    "bad", ["partial", "distinct", "capacity", "at", "cp", "output", "work", "buffers"]
)
def test_readonly_b_alias_exemption_is_exact_and_transactional(library, bad):
    s, w, arrays, _, _ = fixture(3, 65, 17)
    C.c_uint.in_dll(library, "iris_vme_split_test_hart").value = 2
    s.bp, s.bp_bytes = s.b, s.b_bytes
    if bad == "partial":
        s.bp = s.b + 16
    elif bad == "distinct":
        s.bp = arrays[4].address
    elif bad == "capacity":
        s.bp_bytes -= 1
    elif bad == "at":
        s.at = s.b
    elif bad == "cp":
        s.cp = s.b
    elif bad == "output":
        s.output = s.b
    elif bad == "work":
        s.b = s.bp = C.addressof(w)
    else:
        s.b = s.bp = C.addressof(s)
    before = [a.bytes() for a in arrays] + [bytes(w)]
    assert library.iris_vme_split_prepare_readonly_b(C.byref(s), C.byref(w)) != 0
    assert [a.bytes() for a in arrays] + [bytes(w)] == before


def test_readonly_b_page_is_never_written(tmp_path):
    """mprotect catches even same-byte writes, unlike an after-call checksum."""
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("host C compiler required")
    src = source_files()
    source = tmp_path / "readonly.c"
    source.write_text(r"""
#define _GNU_SOURCE
#include "vme_split.h"
#include <assert.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
unsigned iris_vme_split_test_hart=2;
static _Alignas(64) int8_t a[3*17],at[3*17];
static _Alignas(64) int32_t cp[3*65],out[3*65];
int main(void){
 size_t page=(size_t)sysconf(_SC_PAGESIZE);
 int8_t*b=mmap(0,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 assert(b!=MAP_FAILED);for(size_t j=0;j<17*65;j++)b[j]=(int8_t)j;
 assert(!mprotect(b,page,PROT_READ));
 iris_vme_split_buffers s={.a=a,.b=b,.output=out,.at=at,.bp=b,.cp=cp,
 .a_bytes=sizeof a,.b_bytes=17*65,.output_bytes=sizeof out,.at_bytes=sizeof at,
 .bp_bytes=17*65,.cp_bytes=sizeof cp,.m=3,.n=65,.k=17,.tile_edge=32,
 .alignment_bytes=16,.sequence=1,.worker_mask=3};iris_vme_split_work w;
 for(size_t generation=1;generation<4;generation++){
  s.sequence=generation;assert(!iris_vme_split_prepare_readonly_b(&s,&w));
  for(size_t j=0;j<3*65;j++)cp[j]=(int32_t)(generation+j);
  assert(!iris_vme_split_finish_readonly_b(&s,&w));
  assert(!memcmp(out,cp,sizeof cp));for(size_t j=0;j<17*65;j++)assert(b[j]==(int8_t)j);
 }assert(!munmap(b,page));return 0;
}
""")
    exe = tmp_path / "readonly"
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DIRIS_VME_SPLIT_HOST_TEST",
            "-I",
            str(src["vme_split.h"].parent),
            str(source),
            str(src["vme_split_prepare.c"]),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run([str(exe)], check=True, capture_output=True, timeout=30)
