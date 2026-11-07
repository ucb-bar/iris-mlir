"""Bounded-history empirical profiles joined to actual guest CSR read values.

The prefix stream contains model observations, not FPGA counters. Resource
occupancy is a union over the global model timeline; completed service sums
are deliberately kept separate. Work counters are architectural retirement
features, not measured completed traffic at a physical memory level.
"""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path

MASK64 = (1 << 64) - 1


def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError('Nonnegative integer required: ' + label)
    return value


def load(text):
    def reject(value):
        raise ValueError('Nonfinite JSON: ' + value)
    def finite(value):
        result = float(value)
        if not math.isfinite(result): reject(value)
        return result
    return json.loads(text, parse_constant=reject, parse_float=finite)


def counters(value, label):
    if not isinstance(value, dict):
        raise ValueError('Counter object required: ' + label)
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError('Named counter required')
        if isinstance(item, dict):
            counters(item, label + '.' + key)
        else:
            integer(item, label + '.' + key)


def difference(first, last, label):
    counters(first, label); counters(last, label)
    result = {}
    for key in sorted(set(first) | set(last)):
        a, b = first.get(key, 0), last.get(key, 0)
        if isinstance(a, dict) or isinstance(b, dict):
            if a == 0: a = {}
            if b == 0: b = {}
            if not isinstance(a, dict) or not isinstance(b, dict):
                raise ValueError('Counter type changed: ' + label + '.' + key)
            result[key] = difference(a, b, label + '.' + key)
        else:
            if b < a:
                raise ValueError('Counter decreased: ' + label + '.' + key)
            result[key] = b - a
    return result


def regions(console, legacy_case_id=None):
    result = []; seen = set()
    for line in console.splitlines():
        if line.startswith('IRIS_REGION '):
            row = load(line.split(' ', 1)[1])
        elif line.startswith('REGION '):
            if not isinstance(legacy_case_id, str) or not legacy_case_id:
                raise ValueError('Merlin REGION records require an explicit case ID')
            fields = line.split()
            if len(fields) != 5: raise ValueError('Invalid Merlin REGION record')
            hart, start, end, retired = map(int, fields[1:])
            row = {'hart': hart, 'start_cycle': start, 'end_cycle': end,
                   'instret_delta': integer(retired, 'instret delta'),
                   'case_id': legacy_case_id, 'region': 'kernel'}
        else: continue
        hart = integer(row['hart'], 'hart')
        if hart > 2: raise ValueError('Unknown Iris hart')
        for name in ('case_id', 'region'):
            if not isinstance(row.get(name), str) or not row[name]:
                raise ValueError('Nonempty region identity required')
        key = hart, row['case_id'], row['region']
        if key in seen: raise ValueError('Duplicate guest region')
        seen.add(key)
        for name in ('start_cycle', 'end_cycle'):
            if integer(row[name], name) > MASK64: raise ValueError('Counter overflow')
        result.append(row)
    if not result: raise ValueError('No guest regions')
    return result


def observations(lines, wanted, instructions=None):
    selected = {}; previous = {}; identities = set(); count = 0
    for line in lines:
        if not line.strip(): continue
        row = load(line)
        if row.get('schema') != 'iris.empirical.prefix.v1':
            raise ValueError('Unknown empirical prefix schema')
        hart = integer(row['hart'], 'prefix hart')
        if hart > 2: raise ValueError('Unknown prefix hart')
        for name in ('sequence', 'value', 'read_cycle', 'retired'):
            integer(row[name], name)
        if row['value'] > MASK64: raise ValueError('Counter overflow')
        if row.get('endpoint') != 'get_csr_before_current_instruction_retirement':
            raise ValueError('Unknown CSR observation endpoint')
        identity = row.get('calibration_identity')
        if not isinstance(identity, str) or not identity:
            raise ValueError('Calibration identity required')
        identities.add(identity)
        if len(identities) > 1: raise ValueError('Mixed calibration identities')
        for key in ('features', 'memory', 'completed_resource_busy_cycles', 'resource_occupancy_prefix'):
            counters(row.get(key, {}), key)
        if ('resource_occupancy_prefix' in row and
                row.get('resource_occupancy_scope') != 'global_shared_resources'):
            raise ValueError('Explicit global resource occupancy scope required')
        if 'async_compute_pending' in row and type(row['async_compute_pending']) is not bool:
            raise ValueError('Boolean async owner observation required')
        if 'async_pending_count' in row:
            owners = integer(row['async_pending_count'], 'async owner count')
            if owners > 2 or row.get('async_compute_pending') != (owners != 0):
                raise ValueError('Bounded async owner count disagrees with pending state')
        if 'instruction_fetch_observation_scope' in row:
            scope = row['instruction_fetch_observation_scope']
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError('Explicit nonempty instruction fetch observation scope required')
        fetch = row.get('pending_instruction_fetch')
        if fetch is not None:
            if not isinstance(fetch, dict): raise ValueError('Pending instruction fetch must be an object or null')
            for name in ('owner', 'node', 'address', 'bytes', 'l1misses', 'l2misses', 'submitted'):
                integer(fetch[name], 'pending fetch ' + name)
            if not fetch['bytes'] or fetch['submitted'] > row['read_cycle'] or type(fetch['issued']) is not bool:
                raise ValueError('Invalid pending fetch lifecycle')
            if fetch['issued']:
                integer(fetch['start'], 'pending fetch start'); integer(fetch['end'], 'pending fetch end')
                # A same-edge CSR callback may precede this owner's completion
                # callback; the scheduled end can equal the observation time.
                if not fetch['submitted'] <= fetch['start'] <= row['read_cycle'] <= fetch['end']:
                    raise ValueError('Pending fetch span disagrees with captured read time')
            elif fetch['start'] is not None or fetch['end'] is not None:
                raise ValueError('Unissued pending fetch has invented start/end')
        before = previous.get(hart)
        if before is not None:
            if (row['sequence'] <= before['sequence'] or row['read_cycle'] < before['read_cycle']
                    or row['retired'] < before['retired']):
                raise ValueError('Unordered prefix sequence/time/retirement')
            for key in ('features', 'memory', 'completed_resource_busy_cycles', 'resource_occupancy_prefix'):
                difference(before.get(key, {}), row.get(key, {}), key)
        if instructions is not None and ('pc' not in row or 'raw' not in row):
            raise ValueError('ELF-qualified profiling requires captured PC/instruction metadata')
        discarded = row.get('result_discarded', False)
        if type(discarded) is not bool: raise ValueError('Boolean counter discard state required')
        if 'pc' in row:
            raw = integer(row['raw'], 'CSR instruction'); pc = integer(row['pc'], 'CSR PC')
            csr = raw >> 20; rd = (raw >> 7) & 31
            if (pc & 1 or raw & 127 != 0x73 or (raw >> 12) & 7 not in (1, 2, 3, 5, 6, 7)
                    or csr not in (0xb00, 0xc00)):
                raise ValueError('Prefix must encode mcycle CSR read')
            discarded = row.get('result_discarded', rd == 0)
            if (row.get('csr', csr) != csr or row.get('destination', rd) != rd
                    or discarded != (rd == 0)):
                raise ValueError('CSR metadata disagrees with encoded instruction')
            if instructions is not None and instructions.read(pc) != raw:
                raise ValueError('CSR instruction differs from ELF/ROM')
        key = hart, row['value']
        if key in wanted and not discarded:
            if key in selected: raise ValueError('Ambiguous captured mcycle boundary')
            selected[key] = row
        previous[hart] = row; count += 1
    if set(selected) != wanted: raise ValueError('Missing captured mcycle boundary')
    return selected, count, next(iter(identities))


def profile(lines, console, base_hz, instructions=None, symbols=(), legacy_case_id=None):
    integer(base_hz, 'base_hz')
    if not base_hz: raise ValueError('Positive model base clock required')
    rows = regions(console, legacy_case_id)
    wanted = {(r['hart'], r[k]) for r in rows for k in ('start_cycle', 'end_cycle')}
    snapshots, count, identity = observations(lines, wanted, instructions)
    result = []; trace = []; starts = [s[0] for s in symbols]
    for row in rows:
        hart = row['hart']; a = snapshots[hart, row['start_cycle']]; b = snapshots[hart, row['end_cycle']]
        duration = b['read_cycle'] - a['read_cycle']
        if b['sequence'] <= a['sequence'] or duration <= 0:
            raise ValueError('Reversed or zero-duration observed region')
        delta = (b['value'] - a['value']) & MASK64
        if 'cycles' in row and row['cycles'] != delta:
            raise ValueError('Guest cycle delta disagrees with boundary values')
        units = {}
        if ('resource_occupancy_prefix' in a) != ('resource_occupancy_prefix' in b):
            raise ValueError('Partial occupancy instrumentation')
        for resource, values in difference(a.get('resource_occupancy_prefix', {}),
                                            b.get('resource_occupancy_prefix', {}), 'occupancy').items():
            busy = values['union_cycles']
            if busy > duration: raise ValueError('Resource union exceeds region timeline')
            units[resource] = {'modeled_busy_cycles': busy, 'modeled_busy_fraction': busy / duration,
                'lane_service_cycles_sum': values['lane_service_cycles'],
                'scope': 'global resource, all harts, intersected with this region timeline'}
        boundary_functions = []
        for read in (a, b):
            pc = read.get('pc'); symbol = None
            if pc is not None:
                index = bisect.bisect_right(starts, pc) - 1
                if index >= 0 and pc < symbols[index][1]: symbol = symbols[index][2]
            boundary_functions.append(symbol)
        work = difference(a['features'], b['features'], 'features')
        memory = difference(a.get('memory', {}), b.get('memory', {}), 'memory')
        retired = b['retired'] - a['retired']
        # mcycle can be written/inhibited by software. Throughput uses the
        # elapsed model timeline, while preserving the guest counter separately.
        retirement_rates = {
            'instructions_per_model_cycle': retired / duration,
            'architectural_MACs_per_model_cycle': sum(v.get('macs', 0) for v in work.values()) / duration,
            'by_feature': {name: {
                'instructions_per_model_cycle': values.get('instructions', 0) / duration,
                'architectural_MACs_per_model_cycle': values.get('macs', 0) / duration,
                'requested_bytes_per_model_cycle': values.get('bytes', 0) / duration,
                'work_units_per_model_cycle': values.get('work', 0) / duration,
                'dlen_beats_per_model_cycle': values['dlen_beats'] / duration if 'dlen_beats' in values else None,
                'atl_beats_per_model_cycle': values['atl_beats'] / duration if 'atl_beats' in values else None,
            } for name, values in work.items()},
            'semantics': 'Architecturally retired work on this hart; backend completion may cross either ROI boundary.',
            'peak_efficiency': None,
        }
        memory_rates = {name: {
            'accepted_bytes': values.get('bytes', 0),
            'accepted_bytes_per_model_cycle': values.get('bytes', 0) / duration,
            'accepted_bytes_per_model_second': values.get('bytes', 0) * base_hz / duration,
            'semantics': 'Accepted memory timing intents on this hart, not physical completed level traffic.',
        } for name, values in memory.items()}
        item = {'hart': hart, 'case_id': row['case_id'], 'region': row['region'],
            'guest_mcycle_delta': delta, 'timeline_start': a['read_cycle'], 'timeline_end': b['read_cycle'],
            'timeline_cycles': duration, 'guest_instret_delta': row.get('instret_delta'),
            'guest_reported_pass': row.get('pass'),
            'retired_between_mcycle_read_endpoints': retired,
            'async_owner_present_at_boundaries': [a.get('async_compute_pending'), b.get('async_compute_pending')],
            'async_owner_count_at_boundaries': [a.get('async_pending_count'), b.get('async_pending_count')],
            'pending_instruction_fetch_at_boundaries': [a.get('pending_instruction_fetch'), b.get('pending_instruction_fetch')],
            'instruction_fetch_observation_scope_at_boundaries': [
                a.get('instruction_fetch_observation_scope'), b.get('instruction_fetch_observation_scope')],
            'instruction_fetch_observation_limit':
                'A null owner without explicit scope does not establish an empty instruction-fetch pipeline; '
                'legacy prefixes do not cover the source Shuttle Icache refill owner.',
            'architectural_work_features': work,
            'architectural_retirement_rates': retirement_rates,
            'accepted_memory_timing_intent_features': memory,
            'accepted_memory_timing_intent_rates': memory_rates,
            'completed_service_cycles_sum': difference(a.get('completed_resource_busy_cycles', {}),
                                                       b.get('completed_resource_busy_cycles', {}), 'service'),
            'modeled_resource_occupancy': units or None, 'boundary_functions': boundary_functions,
            'function_work_attribution': None, 'stall_breakdown': None, 'roofline': None}
        result.append(item)
        trace.append({'name': row['case_id'] + '::' + row['region'], 'ph': 'X', 'pid': 1, 'tid': hart,
            'ts': a['read_cycle'] * 1e6 / base_hz, 'dur': duration * 1e6 / base_hz,
            'args': {'guest_mcycle_delta': delta, 'model_timeline_cycles': duration,
                     'calibration_identity': identity, 'source': 'captured modeled mcycle read endpoints'}})
    summary = {'schema': 'iris.empirical_profile.v1', 'status': 'experimental_model_observations',
        'execution_identity_verified': False,
        'counter_instruction_elf_binding_verified': instructions is not None,
        'base_hz': base_hz, 'calibration_identity': identity, 'prefix_records': count,
        'region_count': len(result), 'regions': result, 'accuracy_2percent_certified': False,
        'semantics': {'cycles': 'guest-returned CSR values; separate global modeled read timestamps',
            'work': 'architectural retirement feature deltas, not physical completed MAC/traffic counters',
            'occupancy': 'prefix union delta, includes in-flight intervals at each endpoint',
            'instruction_fetch': 'Reported pending owner only, with explicit scope when supplied. '
                'Missing scope means coverage is unknown; null does not prove all frontend work is drained.',
            'completed_service_cycles_sum': 'durations completed between endpoints; overlapping and boundary tails can exceed region time',
            'functions': 'CSR boundary symbols only; instruction-level attribution requires optional full graph',
            'numerical_validation': 'This profiler joins observations; an independent guest/output validator is required separately.',
            'unavailable': ['physical per-level completed traffic', 'validated peak utilization', 'stall breakdown', 'roofline']}}
    return summary, {'traceEvents': trace, 'displayTimeUnit': 'ns'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def verify_execution(receipt_path, elf, fixture, console, prefix):
    """Accept known run-receipt layouts, never infer execution from nearby files."""
    receipt_path = Path(receipt_path).resolve(); receipt = load(receipt_path.read_text())
    if receipt.get('pass') is not True:
        raise ValueError('Successful execution receipt with explicit byte bindings required')
    elf, fixture, console, prefix = [Path(p).resolve() for p in (elf, fixture, console, prefix)]
    aggregate = 'runs' in receipt; records = receipt['runs'] if aggregate else [receipt]
    matched = []
    for index, record in enumerate(records):
        argv = record.get('argv', [])
        if not isinstance(argv, list) or str(elf) not in argv: continue
        selected = [arg.split('=', 1)[1] for arg in argv if arg.startswith('--iris-timing-fixture=')]
        if selected != [str(fixture)]: continue
        if record.get('pass', receipt['pass']) is not True: continue
        expected = dict(receipt.get('input_sha256', {}))
        expected.update(record.get('input_sha256', {}))
        # Aggregate evidence may include BOTH selected and sensitivity runs.
        # Output hashes must come from this exact child, never its siblings.
        output = dict(record.get('evidence_sha256', {}))
        output.update(record.get('output_sha256', {}))
        for name, digest in record.get('artifact_sha256', {}).items():
            output[str((receipt_path.parent / name).resolve())] = digest
        valid = True
        for path, alternatives in ((elf, ('ELF_sha256', 'elf_sha256')),
                                    (fixture, ('fixture_sha256',)),
                                    (console, ('stdout_sha256',)),
                                    (prefix, ('feature_prefix_sha256',))):
            wanted = (expected if path in (elf, fixture) else output).get(str(path))
            if wanted is None:
                declared = [record[name] for name in alternatives if record.get(name) is not None]
                wanted = declared[0] if len(set(declared)) == 1 else None
            if wanted is None or sha(path) != wanted: valid = False; break
        if valid: matched.append((index, record))
    if len(matched) != 1:
        raise ValueError('Execution inputs/outputs must match one unique run and its exact ELF/fixture argv')
    index, record = matched[0]
    return {'receipt': str(receipt_path), 'receipt_sha256': sha(receipt_path),
            'selected_child_index': index if aggregate else None, 'selected_argv': record['argv']}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--console', type=Path, required=True)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--rom', type=Path)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--nm', default='nm')
    parser.add_argument('--case-id', help='Explicit workload name for legacy Merlin REGION records')
    parser.add_argument('--run-receipt', type=Path, help='Verify ELF, fixture, stdout and prefix against their actual execution receipt')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Fresh output directory required')
    from .guest_regions import Instructions
    from .report import symbols
    clocks = [int(line.split()[1]) for line in args.fixture.read_text().splitlines()
              if line.split() and line.split()[0] == 'base_hz']
    if len(clocks) != 1: raise ValueError('Unique fixture base clock required')
    profiles = [line.split('#', 1)[0].split() for line in args.fixture.read_text().splitlines()]
    profiles = [row for row in profiles if row and row[0] == 'empirical_profile']
    if len(profiles) != 1 or len(profiles[0]) != 4:
        raise ValueError('Unique empirical fixture identity required')
    execution = (verify_execution(args.run_receipt, args.elf, args.fixture, args.console, args.prefix)
                 if args.run_receipt else None)
    with args.prefix.open() as stream:
        summary, trace = profile(stream, args.console.read_text(), clocks[0],
            Instructions(args.elf, args.rom), symbols(args.elf, args.nm), args.case_id)
    if summary['calibration_identity'] != profiles[0][2]:
        raise ValueError('Prefix calibration identity differs from selected fixture')
    paths = [args.prefix, args.console, args.elf, args.fixture, Path(__file__)]
    if args.rom: paths.append(args.rom)
    if args.run_receipt:
        if execution != verify_execution(args.run_receipt, args.elf, args.fixture, args.console, args.prefix):
            raise ValueError('Run receipt changed during profiling')
        summary['execution_identity_verified'] = True
        summary['execution_provenance'] = execution
        paths.append(args.run_receipt)
    summary['input_sha256'] = {str(p.resolve()): sha(p) for p in paths}
    args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (args.output / 'trace.json').write_text(json.dumps(trace) + '\n')


if __name__ == '__main__': main()
