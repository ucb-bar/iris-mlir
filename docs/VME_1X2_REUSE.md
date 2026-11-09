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

The package version adds explicit register, bias and parallel admission. Use
fresh native and hardware measurements for the exact selected binary before
choosing this schedule. Shape-specific performance observations and campaign
receipts are maintained privately; no universal offload threshold is implied.
Changing the kernel/object identity invalidates any previous cost lookup.
