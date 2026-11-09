# Explicit Zephyr workspace layout

`iris.workspace_layout` is an opt-in target packaging tool. It reads the chosen
SRAM tuple from a generated, preprocessed Zephyr DTS using the selected SDK's
`dtlib.py`. The DTS describes a software allocation; it does not certify the
memory capacity of a loaded FPGA image.

Select low RAM and workspace budgets explicitly. The tool splits that declared
DRAM into disjoint low RAM and a separate workspace region. Low RAM contains
code, readonly model blobs, ordinary BSS, stacks and kernel end markers. The
workspace base must remain within the guarded `medany` near-address range.
Workspace interiors may extend beyond that range through checked pointers.

```sh
PYTHONPATH=src python -m iris.workspace_layout \
  --dts build/zephyr/zephyr.dts \
  --dtlib "$ZEPHYR_BASE/scripts/dts/python-devicetree/src/devicetree/dtlib.py" \
  --linker-source "$ZEPHYR_BASE/include/zephyr/arch/riscv/common/linker.ld" \
  --low-bytes "$LOW_RAM_BYTES" --reserve-bytes "$WORKSPACE_BYTES" \
  --output generated-workspace
```

Add `workspace.conf` to `EXTRA_CONF_FILE` and the generated overlay to
`EXTRA_DTC_OVERLAY_FILE`, include `workspace.S` in
the application, and install the linker snippet in the target CMake project:

```cmake
zephyr_linker_sources(RAM_SECTIONS ${WORKSPACE_DIR}/workspace.ld)
target_sources(app PRIVATE ${WORKSPACE_DIR}/workspace.S)
target_include_directories(app PRIVATE ${WORKSPACE_DIR})
```

Use `iris_model_workspace_base()` from `workspace.h`, then prove capacities,
alignment, nonaliasing, lifetime, initialization and complete joins using the
ordinary staged-runtime contract. The base accessor prevents large constant
offsets from becoming far direct-symbol relocations. It supplies no allocator,
worker launcher, numerical kernel, sharing permission or automatic routing.
The caller must initialize every required byte before use; NOLOAD is not a
promise that memory starts at zero.

Inspect the actual generated DTS, linker script, ELF/map and *all* boot/runtime
symbol addresses. The linker assertions enforce the selected partition and near
BSS/end anchors, but do not prove physical memory, stack/context sufficiency or
full-model fit. In particular, NOBITS/NOLOAD can still enlarge a PT_LOAD segment's
`p_memsz`; FESVR or another loader may zero that span. Small ELF file size is not
evidence of low loading or initialization cost. Preserve that cost separately
from compute measurements.

The default compiler/provider path and existing board files remain unchanged.

The emitted custom linker script preserves the selected SDK script except for
its build-only `IDT_LIST` memory-region origin. A large DRAM workspace may cross
the SDK's usual ISR-table scratch address. The tool places that scratch just
below declared DRAM; the final executable must discard `.intList` and contain
no load segment at that address. Unknown metadata-region syntax refuses. This
is a linker build resource, not another runtime or physical memory allocation.
