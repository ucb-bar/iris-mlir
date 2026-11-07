# iris-mlir

Merlin support for **Iris** (chipyard `iris-cy`, `IrisConfig`). This repository is a layer on top of
[`rvv-mlir`](https://github.com/ucb-bar/rvv-mlir). It inherits all of RVV and adds Iris's configuration,
its boards, and its **VME/Xsfmm matrix unit**.

Our Iris-specific Spike fork lives in
[`copparihollmann/riscv-isa-sim-gemmini`, branch `iris-model`](https://github.com/copparihollmann/riscv-isa-sim-gemmini/tree/iris-model),
pinned as the `simulator/spike-model` submodule. Its README starts with the
latest capability and single-hart/multicore accuracy tables. The
[Iris development simulator](simulator/README.md) also retains explicit frozen
three-hart Spike profiles for baremetal compute, Zephyr, and JSON/Perfetto
profiling. Start with `IRIS_MODEL_ROOT=/path/to/iris-model ./simulator/iris-run check`.
Its source handoff, calibration fixtures and measured accuracy limits are in
`simulator/`; the full ±2% timing target remains unmet.

Starting from a new machine? Follow [Develop Iris VME kernels](GETTING_STARTED.md)
for repository setup, model capture, ELF builds, and the kernel iteration loop.

```
rvv-mlir   (RVV codegen, feature and policy passes, schedules, contract)
 ├── kodiak-mlir   (+ BME OPU, + Kodiak boards)
 └── iris-mlir     (+ VME matrix unit, + Iris boards)    <- this repo
```

| Iris (from `IrisConfigs.scala`) | |
|---|---|
| harts 0–1 | Shuttle + Saturn, VLEN 256 / DLEN 128, `opuMxParams` (VME/Xsfmm matrix unit incl. MX/FP8), 128 KiB TCM each |
| hart 2 | Rocket + Saturn DMA vector unit, VLEN 256 / DLEN 256, **no matrix unit** |
| memory | 512 KiB L2, 128 KiB SBUS scratchpad @ `0x580000000`, serial-TL DRAM @ `0x80000000` |

## The VME route

int8 contractions `A(M,K)·B(K,N)` with a zero-initialised int32 accumulator, at least one tile in M and N,
are rewritten by Merlin into calls to `merlin_vme_gemm_i8_<n>`. This package supplies what those resolve to:

| file | |
|---|---|
| `contracts/matrix_units.yaml` | where the unit's facts are read from: saturn's decode table (`opuInsns`, `Instructions.scala`), the host core's `BitPat`s and `kmax` table (rocket-chip `Matrix.scala`), the config, the cross-check header (`vme.h`), and the simulator. Declaration names only, plus the register-operand layouts with their sources |
| `src/iris/lower/vme_kernel.py` | the GEMM microkernel, modeled on Miles Rusch's `vme_gemm.h` / `vme-fused-lm2-gemm-transpose`: a 2×2 super-tile of four TE×TE int32 tiles (`sf.vtzero.t`), `kmax` reduction rows per `sf.mm.s.s` (`altfmt` = B signed, `rd[0]` = A signed), readout with `sf.vtmv.v.t`, optional fused bias. Edge tiles run TE×TE with `sf.msettm`/`sf.msettn` clamped; a short last reduction step sets `sf.msettk`. No shape is refused |
| `src/iris/lower/vme_shim.py` | reads the contract, derives encodings (`merlin.targetgen.rtl.matrix_isa`), geometry (TE = VLMAX at e8 of the vector unit whose params set `useOpu`; alignment = DLEN/8), wraps the kernel in Merlin's generic shim (`merlin.kernels.matrix_shim`), compiles it, records provenance; declares the simulator |
| `src/iris/backend.py` | the plugin Merlin's `MatrixRouting` loads through the root `matrix_lowering.py` entrypoint (`--matrix-support-target iris --matrix-unit saturn_vme --matrix-config IrisConfig`) |
| `kernels/` | hand-written VME kernels, their registry and `vme_api.h` ([Write your own kernel](#write-your-own-kernel)) |

`backend.toml` declares Iris's VME W8A8 route as an additive implementation over the pinned RVV base.
`src/iris/composition.py` checks that declaration and the pinned composition before binding the extension;
`src/iris/placement.py`, `pipelines.py`, and `transforms/` supply the actual chip route. The root Python
files are compatibility entrypoints for Merlin's existing plugin contract. RVV lowering and shared LLVM
finalization remain in their respective providers; Iris defines no new dialect.

Every instruction word is derived from the RTL and cross-checked against `vme.h`; with the simulator's
source present also against its `riscv/encoding.h` (all ten instructions agree on every bit both state).
The kernel reads TE and `kmax` from the hardware at run time and takes a scalar path if they are not what
its register layout was generated for.

Facts derived from the pinned sources (Merlin's `merlin/contract/hardware_pins.yaml`: `chipyard_iris`,
`saturn_vme`, `rocket_chip_iris`, `spike_zvt`): TE = 32, operand alignment 16 B, matrix configurations
(e8, w4) and (e32, w1), `kmax(8, 4) = 4`.

### What runs where

* **Shipped image**: the kernel's `.insn` words. Audited by Merlin: the unit's instructions present.
* **Stand-in twin** (`-DMERLIN_MATRIX_SCALAR_TILE`): same lowering, same tiling loop, scalar tile body,
  none of the unit's instructions; gated on stock spike.
* **Shipped image on spike with Zvt**: upstream riscv-isa-sim implements the Zvt draft the RTL follows.
  Build it (≈2 min on 32 cores) and point Merlin at it:

  ```sh
  git clone https://github.com/riscv-software-src/riscv-isa-sim && cd riscv-isa-sim
  git checkout f90d5af53f50b0feec7a7cbcb4411e1e0410c465 && git submodule update --init
  mkdir build && cd build && ../configure --prefix=$PWD/../install && make -j32 install
  export MERLIN_SPIKE_ZVT=$PWD/../install/bin/spike MERLIN_SPIKE_ZVT_SRC=$PWD/..
  ```

  It runs as `--isa=rv64gcv_zfh_zvfh_zvl256b_zvt32t_zvt8e_zvti8i32mm`. The kernel sets `mstatus.MS` first,
  as saturn's `crt.S` does; Iris's RTL has no such field and ignores the write. A spike verdict is a
  functional one under the Zvt draft's semantics, not an RTL one, and its counts are not Iris cycles.
* Matrix images use **harts 0–1 only** (`boards.yaml` `matrix_hart_ids`); a 3-hart matrix image is refused.

### Iris RTL smoke tests (2026-11-05 to 2026-11-06)

The pinned `IrisNoTraceConfig` Verilator simulator ran the shipped VME kernel object with three
one-case bare-metal drivers. Each driver has buffers sized for its case; none of the ELFs was manually
edited, and all three load through the ordinary SerialTL path. Pinned Zvt Spike and Iris RTL agreed on
all outputs, with no mismatches or guard writes, and all three images printed `KT DONE`:

| Case | Outputs | Output digest | RTL kernel cycles | ELF SHA-256 |
| --- | ---: | --- | ---: | --- |
| 1×1×1 | 1 | `0x4d50163cec202f6d` | 10,825 | `e12b096c0d2ddcd6de700a3a6f58a6e31d3aa32a7200f6000b7ba939c08e83ac` |
| 32×32×4 full tile | 1,024 | `0xb5bb00c34e1e5079` | 13,118 | `548385b064bcac22efb04403e86bc752e423ca113a4fc4faf7aa52da52ab0bf3` |
| 32×32×33 K-tail | 1,024 | `0x921f37902c10b2fa` | 6,627 | `b2a556bf00eeeb613a5d1368618f963fbe4675337d9e16b67d266a8e327d0312` |

These cycle counts are observed kernel intervals for the stated cases, not matrix-array occupancy.
The full 44-case kernel-test ELF passed pinned Zvt Spike, but its bounded Iris RTL run did not
complete. It has about 99 KiB of zero-initialized data sent over SerialTL before the test starts;
a three-million-cycle RTL run produced no `KT BEGIN`. A separate diagnostic ELF that skipped the
host's zero writes reached `KT BEGIN one_tile` and returned from the VME kernel, but timed out
before its scalar comparison. That edited ELF is diagnostic evidence only. The full 44-case RTL
verdict remains pending.

The K-tail driver's exact pinned Zvt Spike digest matched its direct RTL run with zero mismatches,
unwritten outputs or guard errors; it reached `KT DONE` in 442 seconds of host wall time. A
larger three-case ELF then completed through the ordinary SerialTL path in 1,192.40 seconds of
host wall time. Its exact ELF SHA-256 is
`554a0ea2d8e8518c6b5e536ca8b4d16e45c1e310e4e5ff2932785c7c3d1fdd5c`. Each case
matched pinned Zvt Spike and its scalar reference with zero mismatches, unwritten outputs, or
guard writes:

| Combined RTL case | Output digest | RTL kernel cycles |
| --- | --- | ---: |
| 32×32×33 K-tail | `0x921f37902c10b2fa` | 6,397 |
| 35×63×33 biased edge | `0x15d98143c9a17f64` | 9,771 |
| Three 32×33×17 batches | `0x250de050092f3504` | 9,126 |

The [three-case receipt](evidence/zvt_three_case_20261106/receipt.json) includes the pinned
simulator, Spike, ELF, kernel, driver, and transcript hashes; the transcripts are beside it.
This configuration uses SerialRAM through SerialTL with no SimDRAM memory port, so `+loadmem`
silently skips program writes and must not be used as a fast boot path. Its 30 KiB BSS explains
why the earlier five-minute direct attempt was inconclusive. The full 44-case RTL suite remains
unverified; a separately validated fast-memory configuration or smaller one-case drivers are
needed to finish it.

## Use

```sh
cd merlin
export MERLIN_CHIPYARD_IRIS=/path/to/chipyard-iris   # iris-cy, with generators/saturn and generators/rocket-chip
export MERLIN_BOARD_CATALOG=examples/board-catalog.yaml:/path/to/iris-mlir
merlin-compile --workload <model> --dtype int8 --target rvv --board iris_rtl --harts 2 --run spike --verify \
    --matrix-unit saturn_vme --matrix-config IrisConfig --matrix-support-target iris
```

`merlin-target-compose --check .` (run by `tests/`) fails when the rvv base this layer pins has moved.
`tests/lowering/test_matrix_lowering.py` checks the adapter, the contract, the kernel's refusals and the stand-in's
tiling against a reference on the host; with `MERLIN_CHIPYARD_IRIS` set it also runs the derivation.
`tests/conformance/test_kernels.py` checks the kernel registry and that `kernels/vme_api.h` spells every word exactly
as the saturn benchmarks' own `vme.h` does.

## Write your own kernel

The VME kernel every routed int8 contraction calls can be a plain C file in `kernels/`, written the way the
saturn `vme` benchmarks are, picked with one flag. Merlin builds the model with it, builds the stand-in twin
from its own scalar reference, and requires the shipped image (your kernel, on the Zvt spike) to match the
twin bit for bit.

**The ABI.** One function, exactly this signature (`VME_KERNEL(my_gemm)` spells it):

```c
void my_gemm(int32_t *c, const int8_t *at, const int8_t *b, const int32_t *bias, size_t m, size_t n, size_t k);
/* C[i][j] = (bias ? bias[j] : 0) + sum_k at[k*m + i] * b[k*n + j]     int32, wrapping; C is WRITTEN */
```

* `at`: A, **K-major** (`k` rows of `m` int8, row stride `m`) -- `vme_gemm.h`'s `at`. Merlin packs
  (transposes) the model's A into an aligned scratch buffer for you.
* `b`: B, **K-major** (`k` rows of `n`, row stride `n`): the weights, in place.
* `c`: int32, row-major `m x n` (row stride `n`). Write every element; do not accumulate into it.
* `bias`: `n` int32 or NULL. The model always passes NULL; kernel-test also checks a non-NULL one unless
  you declare `bias: false`.
* `m, n, k >= 1`, any values unless you declare constraints. Operands are **signed** int8 (`VME_MM_S_S`
  after `vme_vsettnt_e8w4_alt`). `at`, `b`, `c` are aligned to `VME_ALIGN_BYTES` (16). Requantization to
  int8 happens after you return: stop at int32.
* Call `vme_enable()` (sets `mstatus.MS`) before the first matrix instruction: saturn's `crt.S` does it for a
  benchmark, Merlin's runtime does not. On a 2-hart image the file is compiled with `-fopenmp`; a
  `#pragma omp parallel for` over blocks uses both harts' units (each block calls `vme_enable()` itself).

**The headers.** Merlin writes `saturn_vme.h` at build time from the encodings it derives from the RTL
(cross-checked against `vme.h` and the Zvt spike's `encoding.h`): `VME_SF_MM_INT(rd, rs1, rs2)`,
`VME_SF_VTZERO_T(rd)`, `VME_SF_VTMV_V_T(rd, rs1)`, `VME_SF_MSETMTYPE(...)`, `VME_SF_VSETTN/M/K(rd, rs1)`,
`VME_SF_VTDISCARD()`, ... (the `.insn r` directive as a string; an operand the encoding fixes is spelled in,
not taken), `_R` statement forms, `_MATCH/_MASK`, and the configuration's facts: `VME_TILE_EDGE`,
`VME_KSTEP` (kmax), `VME_OPERAND_MTYPE/VTYPE[_BSIGNED]`, `VME_READOUT_MTYPE/VTYPE`, `VME_MT_RD_<t>`,
`VME_MTB_RD_<t>`, the tile-subset-specifier fields, `VME_MSTATUS_MS`. On top of it, **`kernels/vme_api.h`**
is `vme.h`'s own API with the same names and arguments -- `VME_MM_S_S(t, vs2, vs1)`, `VME_VTZERO(t)`,
`VME_VTMV_V_T(vd, tss)`, `VME_VTDISCARD()`, `vme_vsettnt_e8w4_alt`, `vme_vsettnt_e32w1`, `vme_vsettm`,
`vme_vsettk`, `vme_tss`, `VME_LOAD_ROW` -- so a benchmark kernel ports by changing its `#include`.
`tests/conformance/test_kernels.py` checks that every one of those spells the same word as `vme.h`. `run.sh kernel-test`
writes `saturn_vme.h` beside its report so you can read it.

**The template.** `kernels/vme_gemm_2x2_bias.c`: Miles Rusch's `vme_gemm_2x2_bias`
(`benchmarks/vme-fused-lm2-gemm-transpose/main.c`) on this ABI -- four tiles zeroed with `sf.vtzero.t`,
`vme_load_k4`, four `VME_MM_S_S` per kmax rows, a short last chunk with its own `sf.msettk`, readout with the
bias added -- plus `vme_gemm_tiled`'s one-tile loop for edges (loading exactly `tm`/`tn` lanes). Copy it.

**The registry.** `kernels/kernels.yaml` lists each kernel (`unit`, `file`, `function`, `description`,
`constraints`) and the default (`generated`: the kernel `vme_kernel.py` emits, unchanged). Constraints are
optional: `m_multiple`, `n_multiple`, `k_multiple`, `max_m`, `max_n`, `max_k` (integers or `tile`,
`2*tile`...) and `bias: false` -- e.g. the benchmark's original 2x2-only loop would declare
`m_multiple: 2*tile, n_multiple: 2*tile`. Merlin refuses a build that would give a kernel any other shape,
naming each contraction and the rule.

**The loop** (from a Merlin checkout; your clone of this repo on `MERLIN_TARGET_PATH`, so an edit is picked
up by the next command and no pin moves):

```sh
export MERLIN_TARGET_PATH=$HOME/src/iris-mlir:$PWD/examples/rvv/support
cp $HOME/src/iris-mlir/kernels/vme_gemm_2x2_bias.c $HOME/src/iris-mlir/kernels/my_gemm.c   # + a kernels.yaml entry
examples/iris/run.sh kernel-test my_gemm                                  # 1. seconds, ~90 shapes, a table
examples/iris/run.sh op gemm 96 80 200 --matrix-kernel my_gemm --harts 2  # 2. one operator, end to end
examples/iris/run.sh op encoder_block --matrix-kernel my_gemm             # 3. a whole encoder block
```

`kernel-test` runs your kernel on the Zvt spike over partial M/N around one and two tiles, K tails, deep K,
multi-tile shapes, a bias, full-range operands, back-to-back calls and the OPU certification corpus's
shapes, and prints `C[i][j]: got X, reference Y` for the first wrong element, elements never written, and
writes past the end of C. `--matrix-kernel path/to/k.c[:function]` tries a file outside this directory.
Merlin's side is documented in its `docs/guides/chip_packages.md` ("Write your own kernel").

### End-to-end convolution route example

From a set-up Merlin checkout with pinned Iris VME sources and Zvt Spike, run:

```sh
python3 /path/to/iris-mlir/examples/conv2d_kernel_demo.py /path/to/merlin
```

The example uses the registered `vme_gemm_2x2_bias` C kernel. It first runs the
kernel's quick reference sweep, then asks Merlin to capture and lower its built-in
16-input/64-output-channel convolution. Merlin recognizes its signed-int8
contraction, chooses the VME route, converts the convolution's layout to the GEMM
kernel ABI, and links the selected kernel into the shipped image. The checker
requires routed convolution MACs, the selected C file's SHA-256 in the routing
report, verified RVV and VME results, and a shipped Zvt Spike output bit-exact
with the scalar twin. It prints the kept ELFs and routing receipt directory.

This demonstrates Merlin's built-in W8A8 operator path. It does not establish
TorchAO source-model fidelity, FPGA timing, or physical Iris execution. A plain
`op` exit or a kernel-test pass alone is insufficient to establish the entire route.

## The rvv base (`base/rvv-mlir`)

This package is a layer on [rvv-mlir](https://github.com/ucb-bar/rvv-mlir): `contracts/overlay.yaml` holds only what
this chip adds or changes, and `contracts/target_contract.yaml` is base + overlay, generated. The base is a submodule
at `base/rvv-mlir`, pinned to the same commit as `extends.commit` in `provider.yaml` (a test keeps the two equal).
The inherited RVV source package supplies RVV pipeline construction, transforms, packing, and policy;
Iris adds the VME matrix route and does not copy those RVV implementations.

```sh
git clone --recurse-submodules -b handwritten-implementation https://github.com/ucb-bar/<this-repo>.git
MERLIN_TARGET_PATH=base/rvv-mlir merlin-target-compose . --check     # is the generated contract fresh?
```

Changes that belong to every RVV chip go to rvv-mlir itself (commit and push there). Changes for this chip only go
in `contracts/overlay.yaml` (use `replace:` / `remove:` to override a base fact). To move to a newer base:
`git -C base/rvv-mlir checkout <sha>`, set `extends.commit` to the same sha, run `merlin-target-compose .`, commit.
