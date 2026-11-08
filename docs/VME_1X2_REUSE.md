# Optional signed-INT8 1x2 reuse kernel

Select `vme_gemm_1x2_reuse` with the existing `--matrix-kernel` option. The
generated kernel remains the default. This is a candidate to measure, not a
universal replacement or an automatic cost-model rule.

For partial-M blocks with at least two complete N tiles, one A panel feeds two
B panels and accumulator tiles. Paired readout uses v16/v20. TE is read from the
target; the fast path additionally requires actual e32/LMUL4 readout with VL=TE.
It does not assume VLEN=256 or derive DLEN from VLEN. Other register geometry,
larger M, narrow N and N tails retain the original kernel path. NULL bias is the
only advertised contract. OpenMP builds compile out the new path and preserve
the original partitioned implementation; no multicore speedup is established.

The unguarded scheduling prototype passed complete managed FireSim runs on
compute hart0 at VLEN256/DLEN128, with independent INT8-to-wrapping-INT32
goldens. Representative clean caller medians (two measured repetitions after
one warmup) were:

| M x N x K | Original VME cycles | Prototype reuse cycles | Ratio |
|---|---:|---:|---:|
| 32 x 256 x 2048 | 6,988,357.5 | 4,853,785 | 1.440 |
| 32 x 2048 x 256 | 7,798,219.5 | 6,246,196 | 1.248 |
| 32 x 9216 x 2304 | 320,902,710 | 180,030,197 | 1.782 |
| 8 x 256 x 256 | 546,873 | 577,399.5 | 0.947 |

Pure paired readout without the partial-M reuse showed no meaningful gain in
these cases. The regressions require shape-aware selection. Fewer instructions
are also insufficient by themselves: the last case retired about33% fewer
instructions but took more cycles. Physical VME busy and causal stalls were
not directly observed.

Those measurements describe the source-bound prototype, not the exact binary
of this guarded, renamed package kernel. The package version adds explicit
register/bias/parallel admission. Its fresh native control gate passed32regions
with all74,928 independently checked words and guards. Hardware performance of
the exact package binary, other VLENs, non-NULL bias and multicore reuse remain
unqualified. Changing the kernel/object identity invalidates old cost lookup.

Local immutable evidence is recorded under
`model-shapes-20261108/scheduling-hw-v1/` and
`model-shapes-20261108/vme-1x2-guarded-native-v1/`; full input/output digests,
image/driver identity and source pins accompany those receipts. The archived
image's full association with the source checkout is not certified.
