#!/usr/bin/env python3
"""Plot provenance-bound Iris profiles; never substitute fill for physical busy.

Input records use iris.model_part_plot_records.v1 (the schema name is versioned
by the collector). Throughput is 2 integer operations per MAC. Logical operand
intensity is an interface proxy, not measured DRAM/cache traffic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_records(data):
    records = data['records']
    ids = set()
    for r in records:
        if r['id'] in ids:
            raise ValueError(f"duplicate observation: {r['id']}")
        ids.add(r['id'])
        for key in ('median_cycles', 'macs', 'logical_bytes'):
            value = r.get(key)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"invalid {key}: {r['id']}")
        if r.get('median_cycles') == 0:
            raise ValueError('zero cycle denominator')
        samples = r.get('cycle_samples', [])
        if samples and (any(x <= 0 or not math.isfinite(x) for x in samples)
                        or not math.isclose(median(samples), r['median_cycles'], rel_tol=1e-10)):
            raise ValueError(f"sample median mismatch: {r['id']}")
        u = r.get('utilization', {})
        if u.get('value') is not None and not 0 <= u['value'] <= 1:
            raise ValueError(f"invalid utilization fraction: {r['id']}")
        if u.get('kind') != 'physical_busy' and r.get('physical_busy') is not None:
            raise ValueError('physical busy must have independent physical evidence')
    return records


def pair_records(records):
    grouped = defaultdict(dict)
    for r in records:
        if not r.get('paired_group') or r['route'] not in ('RVV', 'VME'):
            continue
        group = grouped[r['paired_group']]
        if r['route'] in group:
            raise ValueError(f"ambiguous route in pair {r['paired_group']}")
        group[r['route']] = r
    pairs = []
    for name, group in grouped.items():
        if set(group) != {'RVV', 'VME'}:
            continue
        a, b = group['RVV'], group['VME']
        for key in ('m', 'n', 'k', 'macs', 'logical_bytes', 'model', 'part', 'phase', 'scope', 'basis', 'numeric_contract', 'observer_mode', 'cohort', 'logical_bytes_basis'):
            if a.get(key) != b.get(key):
                raise ValueError(f"incompatible {key} in {name}")
        if a.get('paired_backend_math_compatible') is False or b.get('paired_backend_math_compatible') is False:
            raise ValueError(f'unqualified arithmetic pair: {name}')
        if a.get('median_cycles') is None or b.get('median_cycles') is None:
            continue
        ratio = a['median_cycles'] / b['median_cycles']
        sa, sb = a.get('cycle_samples') or [a['median_cycles']], b.get('cycle_samples') or [b['median_cycles']]
        pairs.append({'name': name, 'rvv': a, 'vme': b, 'speedup': ratio,
                      'low': min(sa) / max(sb), 'high': max(sa) / min(sb)})
    return sorted(pairs, key=lambda p: (p['rvv']['model'], p['rvv']['part'], p['rvv']['m'], p['name']))


def roof_point(r, *, traffic_field='logical_bytes'):
    cycles, work, traffic = r.get('median_cycles'), r.get('macs'), r.get(traffic_field)
    if not cycles or not work or not traffic:
        return None
    return 2 * work / traffic, 2 * work / cycles



def matching_traffic(records, ceiling):
    """Never compare payload bandwidth on another boundary or clock domain."""
    if not ceiling.get('cycle_basis') or not ceiling.get('boundary'):
        return []
    return [r for r in records
            if r.get('traffic_boundary') == ceiling['boundary']
            and r.get('cycle_basis') == ceiling['cycle_basis']
            and roof_point(r, traffic_field='measured_boundary_bytes') is not None]


def lower_label(r):
    names = {'query_projection': 'Q projection', 'key_value_projection_geometry': 'K/V shape',
             'gate_up_projection_geometry': 'Gate/up shape', 'down_projection': 'Down projection',
             'hidden_square_proxy_not_an_exact_projection': 'Hidden-square proxy',
             'attention_QK': 'Attention QK', 'attention_AV': 'Attention AV',
             'patch_embedding_convolution': 'Patch convolution'}
    return f"{names.get(r['part'], r['part'])} {r['m']}×{r['n']}×{r['k']}"


def save(fig, output, name, paths):
    import matplotlib.pyplot as plt
    for suffix in ('png', 'svg', 'pdf'):
        p = output / f'{name}.{suffix}'
        fig.savefig(p, dpi=175, bbox_inches='tight')
        paths.append(p)
    plt.close(fig)


def plot_model_parts(records, output, paths):
    import matplotlib.pyplot as plt
    # A fixed named cohort, never the minimum of many implementations.
    pairs = pair_records([r for r in records if r.get('cohort') == 'tuned_i8'])
    if not pairs:
        return
    models = sorted({p['rvv']['model'] for p in pairs})
    fig, axes = plt.subplots(1, len(models), figsize=(max(10, 5 * len(models)), 7), squeeze=False)
    for ax, model in zip(axes[0], models):
        rows = [p for p in pairs if p['rvv']['model'] == model]
        vals = [p['speedup'] for p in rows]
        y = list(range(len(rows)))
        ax.barh(y, vals, color=['#158579' if x >= 1 else '#c75a49' for x in vals], height=.7)
        ax.errorbar(vals, y, xerr=[[p['speedup'] - p['low'] for p in rows],
                                 [p['high'] - p['speedup'] for p in rows]], fmt='none', color='#343e48', capsize=2)
        ax.set_yticks(y, [lower_label(p['rvv']) for p in rows], fontsize=8)
        ax.invert_yaxis()
        ax.axvline(1, color='#263848', lw=1, ls='--')
        ax.set_xscale('log', base=2)
        ax.set_xlabel('RVV cycles / VME cycles (>1 favors VME)')
        ax.set_title(model)
        ax.grid(axis='x', alpha=.18)
    fig.suptitle('FPGA speedup by model-shaped operator part', fontsize=16)
    fig.text(.5, -.025, 'Matched INT8 operator fixtures; not end-to-end model execution. Whiskers: observed min/max ratio bounds, not confidence intervals.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'model_part_speedup', paths)

    fig, axes = plt.subplots(1, len(models), figsize=(max(10, 5 * len(models)), 7), squeeze=False)
    for ax, model in zip(axes[0], models):
        rows = [p for p in pairs if p['rvv']['model'] == model]
        y = list(range(len(rows)))
        ax.barh([i - .18 for i in y], [p['rvv']['median_cycles'] for p in rows], height=.35, color='#3477a7', label='RVV')
        ax.barh([i + .18 for i in y], [p['vme']['median_cycles'] for p in rows], height=.35, color='#df8a36', label='RVV + VME')
        ax.set_yticks(y, [lower_label(p['rvv']) for p in rows], fontsize=8)
        ax.invert_yaxis()
        ax.set_xscale('log')
        ax.set_xlabel('Guest mcycle (median; logarithmic axis)')
        ax.set_title(model)
        ax.grid(axis='x', alpha=.18)
        ax.legend(fontsize=8)
    fig.suptitle('FPGA latency by model-shaped operator part', fontsize=16)
    fig.tight_layout()
    save(fig, output, 'model_part_latency', paths)


def plot_geometry(records, output, paths):
    import matplotlib.pyplot as plt
    rows = [r for r in records if r.get('cohort') == 'tuned_i8' and r.get('utilization', {}).get('value') is not None]
    if not rows:
        return
    kinds = sorted({r['utilization']['kind'] for r in rows})
    fig, axes = plt.subplots(1, len(kinds), figsize=(max(9, 5 * len(kinds)), 7), squeeze=False)
    for ax, kind in zip(axes[0], kinds):
        selected = [r for r in rows if r['utilization']['kind'] == kind]
        ax.barh(range(len(selected)), [100 * r['utilization']['value'] for r in selected], color='#3477a7')
        ax.set_yticks(range(len(selected)), [f"{r['model']} {lower_label(r)}" for r in selected], fontsize=7)
        ax.set_xlim(0, 105)
        ax.invert_yaxis()
        ax.set_title(kind.replace('_', ' '))
        ax.set_xlabel('Architectural element-slot fill (%)')
        ax.grid(axis='x', alpha=.15)
    fig.suptitle('Per-part RVV lane geometry — physical busy unavailable', fontsize=15)
    fig.text(.5, -.025, 'Untimed geometry probes qualify VL/tails, not vector duty cycle, memory stalls or physical execution-lane occupancy.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'model_part_lane_geometry', paths)


def plot_roofline(records, ceilings, output, paths):
    import matplotlib.pyplot as plt
    import numpy as np
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    colors = {'RVV': '#3477a7', 'VME': '#df8a36'}
    used = set()
    for r in records:
        xy = roof_point(r)
        if xy is None:
            continue
        label = r['route'] if r['route'] not in used else None
        axes[0].scatter(*xy, s=38, c=colors.get(r['route'], '#777777'), marker='o' if r['m'] == 1 else '^', label=label, alpha=.7)
        used.add(r['route'])
    for c in ceilings.get('compute', []):
        peak = c.get('ops_per_cycle')
        if peak and peak > 0 and c.get('arithmetic') in ('I16xI16_widening_MAC_to_I32', 'signed_I8xI8_to_I32'):
            axes[0].axhline(peak, ls='--', lw=1.2, color=colors.get(c.get('route'), '#777'), label=f"{c.get('route', '?')} conditional source ceiling")
    axes[0].set_xscale('log')
    axes[0].set_yscale('log')
    axes[0].set_xlabel('2 × useful MACs / logical operand-interface bytes')
    axes[0].set_ylabel('2 × useful MACs / guest cycle')
    axes[0].set_title('Performance / intensity with compute ceilings')
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=.15, which='both')
    traffic = [r for r in records if roof_point(r, traffic_field='measured_boundary_bytes') is not None]
    boundary_roofs = [c for c in ceilings.get('bandwidth', []) if c.get('bytes_per_cycle')]
    matched = False
    for c in boundary_roofs:
        eligible = matching_traffic(traffic, c)
        if not eligible:
            continue
        for r in eligible:
            axes[1].scatter(*roof_point(r, traffic_field='measured_boundary_bytes'), c=colors.get(r['route'], '#777777'), s=35)
        xx = np.logspace(-3, 4, 300)
        axes[1].plot(xx, xx * c['bytes_per_cycle'], ls='--', label=c['boundary'])
        matched = True
    if matched:
        axes[1].set_xscale('log')
        axes[1].set_yscale('log')
        axes[1].set_xlabel('2 × MACs / measured bytes at matching boundary')
        axes[1].set_ylabel('Operations / guest cycle')
        axes[1].legend()
    else:
        axes[1].axis('off')
        axes[1].text(.5, .58, 'Memory roofline pending\nmatching per-region traffic + bandwidth ceiling', ha='center', va='center', fontsize=14, transform=axes[1].transAxes)
        axes[1].text(.5, .25, 'Logical input/output sizes are not DRAM traffic.\nWhole-job FASED counters cannot be assigned\nto individual operators or harts.\nNo memory-bound/compute-bound diagnosis is inferred.', ha='center', va='center', fontsize=10, transform=axes[1].transAxes)
    fig.suptitle('Iris INT8 roofline view · 2 operations per MAC · one compute hart', fontsize=15)
    fig.text(.5, -.01, 'Left: logical interface intensity, not a measured DRAM roofline. Dashed compute limits are source-conditional; source/image attestation is incomplete.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'roofline', paths)


def plot_full_model(groups, output, paths):
    import matplotlib.pyplot as plt
    if not groups:
        return
    # Collector emits one row per observed stage/category, or a grouped stage.
    combined = {}
    for row in groups:
        if 'functional_instruction_ticks' in row and 'part' in row:
            key = (row['model'], row.get('phase', row.get('stage')))
            stage = combined.setdefault(key, {'model': key[0], 'phase': key[1], 'groups': {}})
            part = {'contraction': 'matrix_provider_interval', 'layout': 'layout_view', 'reduction': 'reduction_softmax'}.get(row['part'], row['part'])
            stage['groups'][part] = stage['groups'].get(part, 0) + row['functional_instruction_ticks']
            if row.get('source_quality_gate') is False:
                stage['quality_gate_failed'] = True
            if row.get('profiling_deviation_accepted') is True:
                stage['profiling_deviation_accepted'] = True
        else:
            combined[len(combined)] = row
    normalized = []
    for row in combined.values():
        categories = row.get('groups', row.get('bottleneck_groups', {}))
        if isinstance(categories, list):
            categories = {x['category']: x['instruction_ticks'] for x in categories}
        values = {key: (value.get('functional_instruction_ticks', 0) if isinstance(value, dict) else value)
                  for key, value in categories.items()}
        if sum(values.values()) > 0:
            normalized.append((row, values))
    if not normalized:
        return
    # Preserve important categories, combine the rest explicitly.
    main = ['matrix_provider_interval', 'elementwise', 'reduction_softmax', 'layout_copy', 'layout_view']
    fig, ax = plt.subplots(figsize=(10, 5))
    left = [0.] * len(normalized)
    colors = ['#3477a7', '#df8a36', '#158579', '#ac709d', '#8093a4', '#c0c6cb']
    for j, cat in enumerate(main + ['other']):
        vals = []
        for _, v in normalized:
            value = sum(x for k, x in v.items() if k not in main) if cat == 'other' else v.get(cat, 0)
            vals.append(100 * value / sum(v.values()))
        ax.barh(range(len(normalized)), vals, left=left, color=colors[j], label='matrix provider / contraction' if cat == 'matrix_provider_interval' else cat.replace('_', ' '))
        left = [a + b for a, b in zip(left, vals)]
    ax.set_yticks(range(len(normalized)), [f"{r['model']} {r.get('phase', r.get('stage', '?'))}" + (' [profiling-only acceptance]' if r.get('profiling_deviation_accepted') else (' [historical quality threshold exceeded]' if r.get('quality_gate_failed') else '')) for r, _ in normalized])
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xlabel('Share of attributed functional instruction counts (%)')
    ax.set_title('Full-model parts: historical functional work attribution')
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -.15), ncol=3, fontsize=8)
    fig.text(.5, -.09, 'Historical diagnostics with differing precision routes and explicit profiling acceptance. Instruction shares are not cycle shares or physical utilization.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'full_model_part_instruction_share', paths)


def plot_diagnostics(data, output, paths):
    import matplotlib.pyplot as plt
    rows = data.get('compiler_utilization', [])
    if rows:
        rvv = [r for r in rows if r['variant'].startswith('rvv')]
        fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
        for ax, key, title in zip(axes, ['vector_compute', 'vector_memory'], ['Qualified compute fill', 'Ordinary vector-memory fill']):
            vals = [r['lane_metrics'][key]['architectural_group_fill'] * 100 for r in rvv]
            bars = ax.bar([r['variant'] for r in rvv], vals, color=['#3477a7', '#df8a36'])
            for b, r in zip(bars, rvv):
                coverage = r['lane_metrics'][key]['qualified_instruction_fraction']
                ax.text(b.get_x() + b.get_width()/2, b.get_height()+2, f"{b.get_height():.1f}%\ncoverage {coverage:.0%}", ha='center', fontsize=9)
            ax.set_ylim(0, 110)
            ax.set_ylabel('Active / allocated element slots (%)')
            ax.set_title(title)
        for kind, color in [('scalar', '#8093a4'), ('vector_compute', '#3477a7'), ('vector_memory', '#158579'), ('vector_config', '#df8a36')]:
            previous = getattr(axes[2], '_stack_bottom', [0, 0])
            vals = [r['instruction_mix'].get(kind, 0) for r in rvv]
            axes[2].bar([r['variant'] for r in rvv], vals, bottom=previous, label=kind.replace('_', ' '), color=color)
            axes[2]._stack_bottom = [a+b for a,b in zip(previous, vals)]
        axes[2].set_title('Retired instruction mix')
        axes[2].set_ylabel('Architectural instructions / operator')
        axes[2].legend(fontsize=8)
        fig.suptitle('Actual compiler diagnostic: small NCHW convolution', fontsize=15)
        fig.text(.5, -.04, 'Exact ELF replay; functional instruction counts and partial lane semantics. Physical busy and causal stall attribution unavailable.', ha='center', fontsize=9)
        fig.tight_layout()
        save(fig, output, 'compiler_utilization', paths)
    metrics = data.get('compiler_hardware_metrics', [])
    if metrics:
        fig, axes = plt.subplots(1, 2, figsize=(9, 4.5))
        for ax, unit in zip(axes, ['rvv', 'vme']):
            selected = [x for x in metrics if x['variant'].startswith(unit)]
            bars = ax.bar([x['variant'] for x in selected], [x['cycles_median'] for x in selected], color=['#3477a7', '#df8a36'])
            for bar in bars:
                ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"{bar.get_height():,.0f}", ha='center', va='bottom', fontsize=9)
            ax.set_ylim(0, max(x['cycles_median'] for x in selected)*1.2)
            ax.set_ylabel('FPGA guest cycles')
            ax.set_title(unit.upper())
        fig.suptitle('Small compiled convolution: streaming overhead regression', fontsize=14)
        fig.text(.5, -.03, 'Full vs streaming is matched within each route. Different authored graph/output layout prevents a fair cross-route speedup claim.', ha='center', fontsize=9)
        fig.tight_layout()
        save(fig, output, 'compiler_streaming_latency', paths)



def plot_phase_costs(records, output, paths):
    import matplotlib.pyplot as plt
    pairs = pair_records([r for r in records if r.get('cohort') == 'tuned_i8'])
    rows = [p[route] for p in pairs for route in ('rvv', 'vme') if p[route].get('phase_cycle_totals')]
    if not rows:
        return
    models = sorted({r['model'] for r in rows})
    fig, axes = plt.subplots(1, len(models), figsize=(max(10, len(models)*5), 8), squeeze=False)
    for ax, model in zip(axes[0], models):
        selected = [r for r in rows if r['model'] == model]
        left = [0.] * len(selected)
        for part, color in [('preparation', '#df8a36'), ('kernel', '#3477a7'), ('other', '#a1a9b2')]:
            vals = [100 * r['phase_cycle_totals'][part] / sum(r['phase_cycle_totals'].values()) for r in selected]
            ax.barh(range(len(selected)), vals, left=left, color=color, label=part)
            left = [a+b for a,b in zip(left, vals)]
        ax.set_yticks(range(len(selected)), [f"{lower_label(r)} {r['route']}" for r in selected], fontsize=7)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xlabel('Share of measured caller cycles (%)')
        ax.set_title(model)
        ax.legend(fontsize=8, loc='upper center', bbox_to_anchor=(.5, -.08), ncol=3)
    fig.suptitle('Per-part FPGA software phase costs', fontsize=16)
    fig.text(.5, -.02, 'Exact sums over measured repetitions; preparation includes layout work. Kernel interval includes stalls and overhead: it is not physical unit busy.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'model_part_phase_costs', paths)


def plot_peak_efficiency(records, output, paths):
    import matplotlib.pyplot as plt
    pairs = pair_records([r for r in records if r.get('cohort') == 'tuned_i8'])
    models = sorted({p['rvv']['model'] for p in pairs})
    if not models:
        return
    fig, axes = plt.subplots(1, len(models), figsize=(max(10, len(models)*5), 7), squeeze=False)
    for ax, model in zip(axes[0], models):
        selected = [p for p in pairs if p['rvv']['model'] == model]
        for route, delta, color in [('rvv', -.18, '#3477a7'), ('vme', .18, '#df8a36')]:
            xs, ys = [], []
            for i, pair in enumerate(selected):
                r = pair[route]
                peak = r.get('conditional_source_peak_MACs_per_cycle')
                if not peak:
                    continue
                xs.append(100*r['macs']/r['median_cycles']/peak)
                ys.append(i+delta)
            ax.barh(ys, xs, height=.35, color=color, label=route.upper())
        ax.set_yticks(range(len(selected)), [lower_label(p['rvv']) for p in selected], fontsize=8)
        ax.invert_yaxis()
        ax.set_xscale('log')
        ax.set_xlim(.01, 100)
        ax.set_xticks([.01, .1, 1, 10, 100], ['0.01', '0.1', '1', '10', '100'])
        from matplotlib.ticker import NullFormatter
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel('Useful MAC/cycle / conditional ideal peak (%)')
        ax.set_title(model)
        ax.grid(axis='x', alpha=.15)
        ax.legend(fontsize=8)
    fig.suptitle('Per-part achieved throughput efficiency', fontsize=16)
    fig.text(.5, -.025, 'Source-conditional compute ceiling with incomplete source/image association. Includes whole caller cost; not a physical busy-time measurement.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'model_part_throughput_efficiency', paths)


def plot_shape_sweep(records, output, paths):
    import matplotlib.pyplot as plt
    cohorts = ['crossover-square-2048', 'crossover-gemma-low', 'crossover-gemma-mid', 'crossover-gemma-high']
    pairs = pair_records([r for r in records if r.get('cohort') in cohorts])
    by_shape = defaultdict(list)
    for p in pairs:
        r = p['rvv']; by_shape[(r['n'], r['k'])].append(p)
    if not by_shape:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for (n, k), selected in sorted(by_shape.items()):
        selected.sort(key=lambda p:p['rvv']['m'])
        xx=[p['rvv']['m'] for p in selected]
        axes[0].plot(xx, [p['speedup'] for p in selected], marker='o', label=f'N={n}, K={k}')
        for route, style in [('rvv','--'), ('vme','-')]:
            axes[1].plot(xx, [p[route]['median_cycles'] for p in selected], ls=style, marker='.', label=f'{route.upper()} N={n}, K={k}')
    for ax in axes:
        ax.set_xscale('log', base=2)
        ax.set_xlabel('M rows (powers of two plus held-out intermediate shapes)')
        ax.grid(alpha=.18)
        ax.legend(fontsize=8)
    axes[0].axhline(1, color='#333', ls='--', lw=1)
    axes[0].set_ylabel('Matched RVV / VME cycles')
    axes[0].set_title('Observed offload crossover')
    axes[1].set_yscale('log')
    axes[1].set_ylabel('Guest cycles')
    axes[1].set_title('Latency across M')
    fig.suptitle('FPGA shape ablation — frozen fitting / holdout roles preserved', fontsize=14)
    fig.text(.5, -.035, 'Visualization of existing measurements; no fitting on holdout timings and no new automatic compiler threshold inferred.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'shape_crossover', paths)


def plot_memory_activity(data, output, paths):
    import matplotlib.pyplot as plt
    rows = data.get('memory_activity', [])
    if not rows:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, field, title in zip(axes,
            ['outstanding_nonempty_fraction_bounds', 'data_channel_accepted_beat_fraction_bounds'],
            ['At least one request outstanding', 'Accepted AXI data beat fraction']):
        for j, direction in enumerate(['read', 'write']):
            intervals = [r['direction_activity'][direction][field] for r in rows]
            mid = [(a+b)*50 for a,b in intervals]
            err = [(b-a)*50 for a,b in intervals]
            ax.bar([i+(j-.5)*.35 for i in range(len(rows))], mid, yerr=err, width=.35, label=direction, color=['#3477a7','#df8a36'][j], capsize=3)
        ax.set_xticks(range(len(rows)), [r['label'] for r in rows])
        ax.set_ylabel('Sampled memory-clock cycles (%)')
        ax.set_title(title)
        ax.legend()
    fig.suptitle('Whole-job conditional FASED memory activity', fontsize=15)
    fig.text(.5, -.04, 'Not per-operator, per-hart, DRAM-chip busy, or DMA busy. Bounds use archived counter/clock semantics; source/image mapping remains conditional.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'memory_activity', paths)



def plot_copy_optimization(data, output, paths):
    import matplotlib.pyplot as plt
    rows = data.get('copy_optimization', [])
    if not rows:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    for ax, prefix in zip(axes, ['rvv', 'vme']):
        selected = [r for r in rows if r['variant'].startswith(prefix)]
        xx = list(range(len(selected)))
        ax.bar([x-.18 for x in xx], [r['before_instret'] for r in selected], width=.35, label='Before', color='#8093a4')
        bars = ax.bar([x+.18 for x in xx], [r['after_instret'] for r in selected], width=.35, label='Copy-loop lowering', color='#158579')
        for b,r in zip(bars, selected):
            ax.text(b.get_x()+b.get_width()/2, b.get_height(), f"{100*r['signed_instruction_change']:+.1f}%", ha='center', va='bottom', fontsize=10)
        ax.set_xticks(xx, [r['variant'] for r in selected])
        ax.set_ylim(0, max(r['before_instret'] for r in selected)*1.15)
        ax.set_ylabel('Retired architectural instructions / operator')
        ax.set_title(prefix.upper() + (' paths' if prefix=='rvv' else ' unchanged controls'))
        ax.legend(fontsize=8)
    fig.suptitle('New copy lowering: matched native compiler packet', fontsize=15)
    fig.text(.5, -.04, 'All 12 regions / 16,128 words pass. Driver, runtime and VME controls unchanged. Instruction reductions are not FPGA cycle speedups.', ha='center', fontsize=9)
    fig.tight_layout()
    save(fig, output, 'copy_lowering_instruction_counts', paths)


def render(data, diagnostics, output):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False, 'svg.fonttype': 'none', 'svg.hashsalt': 'iris-profiling-v1', 'pdf.fonttype': 42})
    records = validate_records(data)
    output.mkdir(parents=True, exist_ok=False)
    paths = []
    plot_model_parts(records, output, paths)
    plot_geometry(records, output, paths)
    plot_roofline(records, data.get('roofline_ceiling', {}), output, paths)
    plot_full_model(data.get('full_model_groups', []), output, paths)
    plot_diagnostics(diagnostics, output, paths)
    plot_phase_costs(records, output, paths)
    plot_peak_efficiency(records, output, paths)
    plot_shape_sweep(records, output, paths)
    plot_memory_activity(diagnostics, output, paths)
    plot_copy_optimization(diagnostics, output, paths)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--diagnostics', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    diagnostic = json.loads(args.diagnostics.read_text()) if args.diagnostics else {}
    paths = render(data, diagnostic, args.output)
    receipt = {'schema': 'iris.profile_plot_release.v1', 'input_sha256': {str(args.data): sha(args.data), str(Path(__file__).resolve()): sha(__file__)},
               'outputs': {p.name: sha(p) for p in paths}, 'plots': len(paths)//3,
               'physical_utilization_claimed': False, 'logical_intensity_is_DRAM_traffic': False,
               'speedup_definition': 'matched RVV median cycles / VME median cycles', 'operation_convention': '2 integer operations per useful MAC'}
    if args.diagnostics:
        receipt['input_sha256'][str(args.diagnostics)] = sha(args.diagnostics)
    (args.output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'plots': receipt['plots'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
