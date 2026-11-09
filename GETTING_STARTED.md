# Develop Iris VME kernels from a new machine

This guide starts with public checkouts and ends with a compiler-selected C
kernel in a Zephyr image. It uses Merlin's `feat/OPU-VME` branch and this
repository's `handwritten-implementation` branch. The code in this repository
provides Iris's VME extension; Merlin provides model capture, shared lowering,
the Zephyr build, and numerical checks; `rvv-mlir` provides the RVV fallback.
For the setup and capture flow shared with Kodiak, see Merlin's
[fresh-machine guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/docs/guides/chip_fresh_machine.md).

## 1. Get the repositories and toolchains

```sh
mkdir iris-work && cd iris-work
git clone --branch feat/OPU-VME https://github.com/ucb-bar/merlin.git
git clone --recurse-submodules --branch handwritten-implementation \
  https://github.com/ucb-bar/iris-mlir.git
export IRIS_MLIR="$PWD/iris-mlir"
cd merlin/examples/iris
./run.sh setup --llvm /path/to/llvm23-install \
  --chipyard /path/to/chipyard-with-riscv-tools \
  --zephyr-sdk /path/to/zephyr-sdk-0.17.0
./run.sh preflight
```

Have `uv`, Git, `make`, a C++ compiler, and `dtc` on `PATH`. The LLVM install
needs `clang-23` and `mlir-translate` from Merlin's pinned LLVM 23 build; the
Chipyard installation needs stock Spike and `riscv64-unknown-elf-gcc`;
Zephyr SDK 0.17.0 supplies the target toolchain. Merlin's
[machine setup guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/docs/guides/chip_packages.md#setting-up-a-machine)
and [LLVM recipe](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/docs/guides/llvm_toolchain.md)
give the prerequisites. `setup` builds Merlin's and model2MLIR's Python
environments, fetches model2MLIR at the **capture pin** and Iris at the **support
pin**, sets up Zephyr, and verifies/builds the pinned Zvt Spike. It prints the
commands for any missing Chipyard Iris or Zvt source checkout; run those
commands, then rerun `setup`. It writes the selected paths to `merlin/.env`.
Do not point `MERLIN_M2M_DIR` at an arbitrary model2MLIR branch for capture:
Merlin checks that checkout against its capture pin.

The standalone `iris-mlir` clone is for edits. Its `base/rvv-mlir` submodule is
the RVV revision it composes against; there is no separate RVV implementation to
copy into Iris. `setup` also fetches the pinned Iris package for ordinary runs.
To change model capture or the RVV backend itself, work in the separate
[`model2MLIR`](https://github.com/ucb-bar/model2MLIR) or
[`rvv-mlir`](https://github.com/ucb-bar/rvv-mlir) repository. A new frontend
commit needs an updated Merlin capture pin before Merlin will use it; a new RVV
commit requires Iris to recompose against that base and a matching Merlin
support-pin selection. Neither change is required for a C kernel experiment.
To test a local kernel edit without moving Merlin's pin, from the same shell:

```sh
export MERLIN_TARGET_PATH="$IRIS_MLIR:$IRIS_MLIR/base/rvv-mlir"
export MERLIN_SUPPORT_PINS="$IRIS_MLIR/contracts/streaming_support_pins.json"
```

This feature revision composes against RVV commit
`251e63c44a5472bc99adba1a64a0045eeb2e782e`. Its checked project-local manifest
selects that exact commit and tree for this explicit Iris checkout. Merlin's
older global default can remain at its own pin; the manifest does not disable
verification or change other shells. Both repositories must contain the
matching dependency before this local selection will work. Ordinary setup
above still uses Merlin's global package pins until these exports are set.

Dependency selection does not enable streaming. The separate
[streaming INT8 im2col guide](docs/STREAMING_IM2COL.md) shows its opt-in selector,
explicit workspace budget, one-compute-hart restriction and numerical gates.
Its recorded compiler/native checks establish functionality; hand-written
kernel FPGA measurements do not establish a speedup for this compiler schedule.

The `run.sh` commands below load Merlin's `.env`. If `preflight` marks a
setting `!`, an old shell export is overriding `.env`; unset that variable.

## 2. Prove the toolchain and kernel route without model downloads

From `merlin/examples/iris`:

```sh
./run.sh kernel-test vme_gemm_2x2_bias --quick
./run.sh op conv2d 16 64 16 3 --matrix-kernel vme_gemm_2x2_bias --harts 1
../../.venv/bin/python "$IRIS_MLIR/examples/conv2d_kernel_demo.py" ../..
```

The first command compares the C kernel with Merlin's scalar reference over
edge tiles, reduction tails, bias, and batches on pinned Zvt Spike. The second
captures a built-in convolution, compiles plain RVV and VME images, and runs
the shipped VME image on Zvt Spike. The final checker requires the selected C
file's SHA-256 in the routing report, routed convolution MACs, verified RVV and
VME results, and a shipped output bit-exact with its scalar twin. Its kept
ELFs and `routing_report.json` are under
`merlin/out/artifacts/probes/iris_rtl/`.

## 3. Capture a model with model2MLIR

`./run.sh capture NAME` uses the model2MLIR checkout installed by `setup` and
writes `merlin/out/artifacts/recaptures/NAME_int8_full/`. That directory has
`model.mlir`, weights, inputs, and numerical references. Merlin's normal `int8`
path computes its W8A8 reference from the capture; this is a compiler
correctness reference, not an independent TorchAO source-model result.

The first model needs no external files:

```sh
./run.sh capture encoder_block --loader ../workloads/encoder_block/loader.py
./run.sh matrix --models encoder_block --harts 1
```

The model matrix runs simulations and can take substantially longer than a
single `kernel-test`, especially for larger models.

For **SpectFormer**, obtain the architecture source from
`https://github.com/badripatro/SpectFormers`, set `SPECTFORMER_DIR` to that
checkout, and supply a trained SpectFormer-Ti checkpoint as
`SPECTFORMER_CKPT` if model accuracy matters:

```sh
mkdir -p "$HOME/models"
git clone https://github.com/badripatro/SpectFormers.git "$HOME/models/SpectFormers"
export SPECTFORMER_DIR="$HOME/models/SpectFormers"
export SPECTFORMER_CKPT=/path/to/checkpoint_best.pth
./run.sh capture spectformer
./run.sh matrix --models spectformer --harts 1
```

The checkpoint is not in these repositories. The loader prints whether it
loaded the checkpoint and its missing/unexpected key counts. If none is
available it **announces random initialization**; that capture is useful for
kernel/compiler development but cannot substantiate pretrained-model accuracy
or reproduce a published capture hash.

For **TinyLlama**, the section loader uses the
`TinyLlama/TinyLlama-1.1B-Chat-v1.0` checkpoint through Hugging Face
Transformers. Obtain access to its model files in the same Hugging Face cache
used by Merlin's capture Python. For an exact checkpoint, set
`CAUSAL_LM_REVISION` to a 40-character commit and prefetch that snapshot;
the loader rejects a different revision. This two-layer, 64-token section
keeps real pretrained layer weights and their original matrix shapes:

```sh
export CAUSAL_LM_MODEL=TinyLlama/TinyLlama-1.1B-Chat-v1.0
export CAUSAL_LM_REVISION=fe8a4ea1ffedaf415f4da2f062534de366a451e6
uv pip install --python ../../out/build/venv/model2mlir/bin/python huggingface_hub
../../out/build/venv/model2mlir/bin/python -c \
  'import os; from huggingface_hub import snapshot_download; snapshot_download(os.environ["CAUSAL_LM_MODEL"], revision=os.environ["CAUSAL_LM_REVISION"])'
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export CAUSAL_LM_LAYERS=2 CAUSAL_LM_ENTRY=embeds M2M_SEQ=64
./run.sh capture tinyllama_l2_s64 --loader ../workloads/causal_lm_section/loader.py
./run.sh matrix --models tinyllama_l2_s64 --harts 1
```

With network access, `from_pretrained` can fetch the checkpoint directly;
the explicit prefetch above makes offline capture and the checkpoint revision
check reproducible. Check its `config.json` and `model.safetensors` hashes
using Merlin's [source-capture guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/examples/workloads/causal_lm_section/README.md).
A fresh download or recapture has a new identity;
compare its numerical output with its own reference before comparing timings
with another run. The full model can be captured separately, but its memory
and simulation cost are much greater than this bounded section.

## 4. Write and select a VME kernel

Start from [`kernels/vme_gemm_2x2_bias.c`](kernels/vme_gemm_2x2_bias.c).
Add the new C file to [`kernels/kernels.yaml`](kernels/kernels.yaml), with the
`saturn_vme` unit, function name, and any genuine shape constraints. Its ABI is
`void kernel(int32_t *c, const int8_t *at, const int8_t *b, const int32_t
*bias, size_t m, size_t n, size_t k)`: `at` and `b` are K-major signed-int8
arrays; `c` is row-major int32 output, written in full. Merlin packs the left
operand, performs the final requantization after the kernel, and generates
`saturn_vme.h` from the pinned RTL. The template includes
[`kernels/vme_api.h`](kernels/vme_api.h), which provides the VME instruction
helpers. See [the full ABI and header guide](README.md#write-your-own-kernel).

```sh
cp "$IRIS_MLIR/kernels/vme_gemm_2x2_bias.c" "$IRIS_MLIR/kernels/my_gemm.c"
# Edit my_gemm.c and add my_gemm to kernels/kernels.yaml.
./run.sh kernel-test my_gemm --quick
./run.sh kernel-test my_gemm --full
./run.sh op conv2d 16 64 16 3 --matrix-kernel my_gemm --harts 1
./run.sh matrix --models encoder_block --harts 1 --matrix-kernel my_gemm
```

`kernel-test` checks the kernel directly. `op` and `matrix` check that the
**compiler selects it**, links it into the shipped ELF, and preserves the model
result. `--matrix-kernel /absolute/path/to/file.c:function` also selects a C
file without registering it. Commit and publish the Iris change before
updating Merlin's one Iris support pin.

## 5. Build an ELF and interpret the result

For a captured model, `./run.sh matrix --models NAME --harts 1` is the
numerical gate: plain RVV, the VME image's scalar twin, and the shipped VME
image on pinned Zvt Spike must verify. To build an image without running it,
use Merlin's compiler directly from the `merlin` root with the local Iris
checkout selected:

```sh
cd ../..
export MERLIN_TARGET_PATH="$IRIS_MLIR:$IRIS_MLIR/base/rvv-mlir"
export MERLIN_BOARD_CATALOG="$PWD/examples/board-catalog.yaml:$IRIS_MLIR"
.venv/bin/merlin-compile --workload encoder_block --dtype int8 --target rvv \
  --board iris_rtl --harts 1 --run zephyr --matrix-gate build --no-capture \
  --matrix-unit saturn_vme --matrix-config IrisConfig \
  --matrix-support-target iris --matrix-kernel my_gemm --json
```

The JSON result names the shipped `binary` ELF, its scalar `twin_binary`,
the selected kernel SHA-256, and the routing report. `--matrix-gate build`
only builds and audits; it does **not** establish numerical correctness. Copy
the file named by `binary` to your chosen delivery location, for example
`cp /absolute/path/from/result.binary ./encoder_block-iris.elf`, before
removing build scratch. The compile-only command above was exercised with
`encoder_block`; it produced a RISC-V Zephyr ELF and routed 12 contractions.
Use
`--matrix-kernel vme_gemm_2x2_bias` to build with the shipped example instead.

Pinned Zvt Spike gives functional VME instruction semantics. Three shipped
kernel cases have also matched Iris RTL through SerialTL, as recorded in
[the receipt](evidence/zvt_three_case_20261106/receipt.json). The full RTL
suite, an Iris FPGA bitstream, and physical Iris execution are not qualified
by these commands. Spike instruction counts are not Iris cycles or matrix
array utilization.
