"""Explicit, signed-INT8 streaming window contractions for the Iris provider.

The RVV component owns panel construction. Iris owns the consumer ABI, selected
unit geometry and dispatch record. No floating-point or quantization operation
is moved across a panel boundary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class StreamingOptions:
    """Software budgets plus independently derived architectural facts.

    ``workspace_bytes`` covers the logical payload of two possible gather panels AND the shim's
    reusable A-transpose buffer. It is not an assertion about cache capacity.
    ``panel_rows`` is an optional upper bound, never a universal measured tile.
    """

    unit: str
    config: str
    vlen_bits: int
    dlen_bits: int
    tile_edge: int
    workspace_bytes: int
    panel_rows: int | None = None

    def __post_init__(self):
        if not self.unit or not self.config:
            raise ValueError("selected unit and configuration are required")
        if self.vlen_bits < 128 or self.vlen_bits & (self.vlen_bits - 1):
            raise ValueError("VLEN must be a power of two >= 128")
        if self.dlen_bits <= 0 or self.dlen_bits % 8 or self.tile_edge <= 0:
            raise ValueError("independent positive DLEN and RTL tile edge required")
        if self.workspace_bytes <= 0 or (
            self.panel_rows is not None and self.panel_rows <= 0
        ):
            raise ValueError("positive explicit software budgets required")

    def width(self, *, k: int, ow: int) -> int:
        # Full-panel + tail lifetimes may overlap; the descriptor shim also
        # reserves one packed A. Reserve all three, even when a tail is absent.
        capacity = self.workspace_bytes // (3 * k)
        width = min(capacity, ow, self.panel_rows or ow)
        if width < 1:
            raise ValueError("workspace cannot hold gather/tail/packed-A for one row")
        if width >= self.tile_edge:
            width = width // self.tile_edge * self.tile_edge
        return width

    def to_dict(self):
        return {
            **asdict(self),
            "numeric_contract": "signed_i8_i8_i32_modular_zero_accumulator",
            "policy_basis": "RTL_geometry_and_explicit_software_workspace_budget",
            "physical_lane_utilization": None,
            "cache_capacity_bytes": None,
            "measured_performance_claim": False,
            "parallel_panels": False,
            "workspace_bound_scope": "panel_payload_excludes_allocator_alignment_and_metadata",
        }


@dataclass(frozen=True)
class StreamingSelector:
    base: object
    streaming: StreamingOptions

    def __call__(self, shape):
        return self.base(shape)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def describe(self):
        describe = getattr(self.base, "describe", None)
        return {
            "base": describe() if describe else "explicit selector",
            "iris_streaming_im2col": self.streaming.to_dict(),
        }


def _shared():
    """Load the pinned inherited component, without modifying sys.path."""
    from merlin.targetgen.plugins import load_module
    from merlin.targetgen.provider_compose import resolve_base
    from merlin.targetgen.providers import read_provider

    root = Path(__file__).resolve().parents[3]
    base = resolve_base(read_provider(root))
    return load_module(
        base.root, "src/rvv/packing/im2col_stream.py", package_name="rvv"
    )


def _signed_zero_mac(op):
    """A closed typed DAG, accepting either legal add operand order."""
    from xdsl.dialects import arith
    from xdsl.dialects.builtin import i8, i32
    from xdsl.dialects.linalg.ops import FillOp, YieldOp

    if not op.regions or len(op.regions[0].blocks) != 1:
        return False
    block = op.regions[0].block
    if [a.type for a in block.args] != [i8, i8, i32]:
        return False
    ops = list(block.ops)
    if len(ops) != 5 or not all(
        isinstance(o, cls)
        for o, cls in zip(
            ops, (arith.ExtSIOp, arith.ExtSIOp, arith.MuliOp, arith.AddiOp, YieldOp)
        )
    ):
        return False
    x, y, mul, add, end = ops
    if list(x.operands) != [block.args[0]] or list(y.operands) != [block.args[1]]:
        return False
    if x.results[0].type != i32 or y.results[0].type != i32:
        return False
    if list(mul.operands) != [x.results[0], y.results[0]]:
        return False
    for arithmetic in (mul, add):
        flags = arithmetic.properties.get("overflowFlags")
        if flags is not None and flags.data:
            return False
    if tuple(add.operands) not in (
        (mul.results[0], block.args[2]),
        (block.args[2], mul.results[0]),
    ):
        return False
    if list(end.operands) != [add.results[0]]:
        return False
    fill = op.operands[2].owner
    return (
        isinstance(fill, FillOp)
        and len(fill.inputs) == 1
        and isinstance(fill.inputs[0].owner, arith.ConstantOp)
        and fill.inputs[0].owner.value.value.data == 0
    )


def _window_spec(nf, shared):
    """Decode only static rank-four conv maps, with no implicit padding."""
    from xdsl.ir.affine import AffineDimExpr, AffineExpr

    if (
        nf.windowed != 0
        or nf.batch
        or len(nf.m) != 3
        or len(nf.n) != 1
        or len(nf.k) != 3
    ):
        raise ValueError("streaming requires a left rank-four window and unshared B")
    source = nf.op.operands[0]
    if len(source.type.get_shape()) != 4:
        raise ValueError("streaming requires a rank-four window input")
    exprs = nf.window_map
    pure = [i for i, e in enumerate(exprs) if isinstance(e, AffineDimExpr)]
    if pure == [0, 3]:
        layout, bdim, cdim = "NHWC", exprs[0].position, exprs[3].position
        h, w = exprs[1], exprs[2]
    elif pure == [0, 1]:
        layout, bdim, cdim = "NCHW", exprs[0].position, exprs[1].position
        h, w = exprs[2], exprs[3]
    else:
        raise ValueError("window must have explicit batch/channel projections")

    def pair(expr):
        free = [d for d in nf.m if d in expr.used_dims()]
        red = [d for d in nf.k if d in expr.used_dims()]
        if len(free) != 1 or len(red) != 1:
            raise ValueError("window needs one spatial and one kernel dim per axis")
        f, r = free[0], red[0]
        zero = [0] * len(nf.extents)
        one = list(zero)
        one[f] = 1
        sh = expr.eval(one, [])
        one = list(zero)
        one[r] = 1
        dh = expr.eval(one, [])
        if (
            sh <= 0
            or dh <= 0
            or expr != AffineExpr.dimension(f) * sh + AffineExpr.dimension(r) * dh
        ):
            raise TypeError(
                "only positive linear stride/dilation windows are supported"
            )
        return f, r, sh, dh

    hd, kh, sh, dh = pair(h)
    wd, kw, sw, dw = pair(w)
    if tuple(nf.m) != (bdim, hd, wd) or cdim not in nf.k:
        raise ValueError("normalized M order must be batch/height/width")
    names = {cdim: "c", kh: "kh", kw: "kw"}
    return shared.WindowSpec(
        n=nf.extents[bdim],
        oh=nf.extents[hd],
        ow=nf.extents[wd],
        channels=nf.extents[cdim],
        kh=nf.extents[kh],
        kw=nf.extents[kw],
        sh=sh,
        sw=sw,
        dh=dh,
        dw=dw,
        input_layout=layout,
        reduction_order=tuple(names[d] for d in nf.k),
    )


def _declare_destination_wrapper(module, symbol, signature):
    """Expose the existing C ABI through an explicit mutable buffer boundary.

    A bodyless *tensor* function may return an alias of ANY tensor argument in
    MLIR's module bufferization. That pessimism prevents a reusable producer
    workspace in a loop. This wrapper states the actual existing shim contract:
    read A/B, mutate C, return the post-call C buffer. ``to_buffer`` makes the mutation visible to
    bufferization, without inventing alias attributes or disabling checks.
    The external function keeps the original flattened memref-descriptor ABI.
    """
    from merlin.llvmlower import int8_contractions as ic
    from xdsl.dialects import bufferization, func
    from xdsl.dialects.builtin import (
        ArrayAttr,
        DictionaryAttr,
        MemRefType,
        StringAttr,
        UnitAttr,
    )
    from xdsl.ir import Block, Region

    tensors = ic._signature_types(module, signature)
    buffers = tuple(MemRefType(t.get_element_type(), t.get_shape()) for t in tensors)
    block = Block(arg_types=tensors)
    views = [
        bufferization.ToBufferOp.build(
            operands=[arg],
            result_types=[typ],
            properties={"read_only": UnitAttr()} if index < 2 else {},
        )
        for index, (arg, typ) in enumerate(zip(block.args, buffers))
    ]
    call = func.CallOp(symbol, [view.memref for view in views], [buffers[2]])
    result = bufferization.ToTensorOp(call.results[0], restrict=True, writable=True)
    block.add_ops([*views, call, result, func.ReturnOp(result.tensor)])
    read = DictionaryAttr({"bufferization.access": StringAttr("read")})
    write = DictionaryAttr({"bufferization.access": StringAttr("write")})
    module.body.block.add_ops(
        [
            func.FuncOp(
                symbol + "_tensor",
                (tensors, (tensors[2],)),
                Region(block),
                visibility="private",
            ),
            func.FuncOp(
                symbol,
                (buffers, (buffers[2],)),
                Region(),
                visibility="private",
                arg_attrs=ArrayAttr([read, read, write]),
            ),
        ]
    )


def rewrite(module, *, select, tile_edge, symbol_prefix):
    """Stream selected windows, then use the unchanged route for remaining ops."""
    from merlin.common import mlir_query as mq
    from merlin.llvmlower import contraction_normal_form as cnf
    from merlin.llvmlower import int8_contractions as ic
    from xdsl.dialects import func
    from xdsl.rewriter import InsertPoint, Rewriter

    options = select.streaming
    if options.tile_edge != tile_edge:
        raise ValueError("streaming and selected VME tile geometry disagree")
    shared = _shared()
    routed, signatures, entries = [], {}, []
    # Validate every chosen window before mutating anything. Unsupported windows
    # never silently fall through to an unbounded full-im2col route.
    chosen = []
    for op in list(mq.walk(module, "linalg.generic")):
        nf = cnf.analyze(op)
        if isinstance(nf, str):
            # The ordinary INT8 route can normalize some unsigned grammars.
            # This opt-in has not proved those window transformations: refuse
            # rather than accidentally taking that unbounded normalization.
            maps = cnf._maps(op)
            from xdsl.ir.affine import AffineDimExpr

            window = maps and any(
                not isinstance(e, AffineDimExpr) for m in maps[:2] for e in m
            )
            if window and any(o.name == "arith.extui" for o in op.walk()):
                raise ValueError(
                    "unsigned window arithmetic is outside the signed streaming contract"
                )
            continue
        if nf.windowed is None:
            continue
        if not select(nf.shape()):
            continue
        if callable(getattr(select.base, "decide", None)):
            raise TypeError(
                "streaming needs an explicit as-is selector, not a priced orientation policy"
            )
        if not _signed_zero_mac(op):
            raise ValueError(
                "streaming requires the exact signed I8/I32 zero-accumulator DAG"
            )
        spec = _window_spec(nf, shared)
        width = options.width(k=spec.k, ow=spec.ow)
        chosen.append((nf, spec, width))
    for index, (nf, spec, width) in enumerate(chosen):
        source = nf.op.operands[0]
        fqn, site = ic._fqn(nf.op), cnf.site_of(nf.op)
        n = nf.size(nf.n)
        call_holder = []
        temporary = f"{symbol_prefix}_temporary_{index}"

        def make_call(a, b, c, result_type, symbol=temporary, holder=call_holder):
            call = func.CallOp(symbol, [a, b, c], [result_type])
            holder.append(call)
            return call

        normalized = cnf.emit(nf, cnf.AS_IS, make_call)
        call = call_holder[0]
        _, b, initial = call.operands
        panel_signatures = {}

        def consume(
            panel,
            tile,
            actual_width,
            source_index=index,
            sigs=panel_signatures,
            columns=n,
            reduction=spec.k,
            weight=b,
        ):
            symbol = f"{symbol_prefix}_stream_{source_index}_m{actual_width}"
            sigs[symbol] = (actual_width, columns, reduction)
            inner = func.CallOp(symbol + "_tensor", [panel, weight, tile], [tile.type])
            return [inner], inner.results[0]

        ops, result = shared.build_stream(
            source, initial, spec, width, consume, panel_layout="mk", output_axis=0
        )
        Rewriter.insert_op(ops, InsertPoint.before(call))
        call.results[0].replace_all_uses_with(result)
        Rewriter.erase_op(call)
        # The full A gather and collapse are dead. B normalization and output
        # restoration remain outside the sequential panel loops.
        for obsolete in reversed(normalized):
            if (
                obsolete.parent is not None
                and obsolete.results
                and all(not value.uses for value in obsolete.results)
            ):
                Rewriter.erase_op(obsolete)
        signatures.update(panel_signatures)
        full, tail = divmod(spec.ow, width)
        widths = [width] * (spec.n * spec.oh * full) + (
            [tail] * (spec.n * spec.oh) if tail else []
        )
        for actual_width in widths:
            symbol = f"{symbol_prefix}_stream_{index}_m{actual_width}"
            routed.append(
                ic.Routed(
                    symbol,
                    (actual_width, n),
                    (spec.k,),
                    fqn,
                    site,
                    steps=("streaming_im2col_a", "shared_b_normalization"),
                    decision={
                        "reason": "explicit bounded sequential row panels",
                        "source_contraction": index,
                    },
                )
            )
        gather_bytes = spec.k * (width + tail)
        packed_bytes = spec.k * max(width, tail)
        entries.append(
            {
                "source_contraction": index,
                "window": asdict(spec),
                "panel_width": width,
                "row_tail": tail,
                "logical_panel_invocations": len(widths),
                "static_call_sites": len(panel_signatures),
                "full_activation_bytes_avoided": spec.m * spec.k,
                "gather_workspace_bytes": gather_bytes,
                "shim_packed_A_bytes": packed_bytes,
                "combined_workspace_bound_bytes": gather_bytes + packed_bytes,
                "extra_A_to_At_copy": True,
                "B_normalization": "once_outside_panels",
            }
        )
    for symbol, signature in signatures.items():
        _declare_destination_wrapper(module, symbol, signature)
    rest = ic.rewrite_contractions(
        module,
        select=select.base,
        tile_edge=tile_edge,
        symbol_prefix=symbol_prefix + "_plain",
    )
    combined = replace(
        rest,
        routed=tuple(routed) + rest.routed,
        signatures={**signatures, **rest.signatures},
        policy=select.describe(),
    )
    return combined, {
        "schema": "iris.streaming_im2col.v1",
        "options": options.to_dict(),
        "source_contractions": len(chosen),
        "entries": entries,
        "routing_count_semantics": "declared_logical_panel_invocations_plus_plain_contractions",
    }


def rewrite_prepared_file(
    prepared, work, *, select, tile_edge, symbol_prefix, sidecar_name
):
    from merlin.frontends.linalg_mlir import parse_mlir_file
    from merlin.llvmlower.declaration_access import (
        patch_declaration_arg_attrs,
        unpatched_declarations,
    )
    from merlin.xdsl_dialects._common import text

    prepared, work = Path(prepared), Path(work)
    module = parse_mlir_file(prepared)
    result, schedule = rewrite(
        module, select=select, tile_edge=tile_edge, symbol_prefix=symbol_prefix
    )
    module.verify()
    if result.count:
        output = patch_declaration_arg_attrs(
            text(module), result.signatures, argument_access=("read", "read", "write")
        )
        if unpatched_declarations(output, result.signatures):
            raise ValueError(
                "streaming external declarations lost bufferization access policy"
            )
        prepared.write_text(output)
    work.mkdir(parents=True, exist_ok=True)
    (work / sidecar_name).write_text(
        json.dumps({**result.to_dict(), "streaming_im2col": schedule}, indent=2) + "\n"
    )
    (work / "streaming_im2col.json").write_text(json.dumps(schedule, indent=2) + "\n")
    return result


def build_schedule_provenance(work, *, signatures, unit, config, parallel_tiles):
    """Bind a fresh object's streaming schedule, or leave ordinary builds alone."""
    import hashlib

    work = Path(work)
    candidates = [work / "streaming_im2col.json", work.parent / "streaming_im2col.json"]
    found = [path for path in candidates if path.is_file()]
    if not found:
        return None
    if len(found) != 1:
        raise ValueError("ambiguous streaming schedule beside object build")
    path = found[0]
    schedule = json.loads(path.read_text())
    if schedule.get("schema") != "iris.streaming_im2col.v1":
        raise ValueError("unknown streaming schedule schema")
    options = schedule["options"]
    if (options["unit"], options["config"]) != (unit, config):
        raise ValueError("streaming schedule selected a different unit/configuration")
    if parallel_tiles:
        raise ValueError("streaming panels currently require one compute hart")
    sidecar = json.loads((path.parent / "vme_signatures.json").read_text())
    expected = {name: list(shape) for name, shape in signatures.items()}
    if sidecar.get("signatures") != expected:
        raise ValueError("streaming schedule signatures differ from object build")
    from ..lower import vme_shim

    facts = vme_shim.load_contract(unit).vector_params(config)
    edge, _ = vme_shim.load_contract(unit).geometry(config)
    if (options["vlen_bits"], options["dlen_bits"], options["tile_edge"]) != (
        facts["vLen"],
        facts["dLen"],
        edge,
    ):
        raise ValueError("streaming schedule geometry differs from selected unit")
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schedule": schedule,
        "hardware_performance_certified": False,
    }
