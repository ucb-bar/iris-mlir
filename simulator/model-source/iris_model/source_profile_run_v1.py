"""Additive source-event profiling frontend for an explicitly selected model.

This is hardware-free. The model descriptor owns all three hart ISAs, memory
regions, boot devices and fitted timing arguments. An exit status is recorded
separately from independent output validation and timing certification.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .empirical_profile import sha
from .source_event_profile_v1 import profile as source_profile

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    def refuse(value):
        raise ValueError('Non-finite JSON value: ' + value)
    return json.loads(Path(path).read_text(), parse_constant=refuse)


def checked_inputs(values):
    if not isinstance(values, dict) or not values:
        raise ValueError('Nonempty execution input closure required')
    for name, expected in values.items():
        path = Path(name)
        if (not path.is_absolute() or not isinstance(expected, str)
                or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected)
                or not path.is_file() or sha(path) != expected):
            raise ValueError('Execution input changed or missing: ' + name)


def descriptor(manifest):
    record = load(manifest)
    if not isinstance(record, dict) or record.get('schema') != 'iris.empirical_simulator.v1':
        raise ValueError('An explicit empirical model manifest is required')
    provider = Path(record['model_provider'])
    expected = record.get('input_sha256', {}).get(str(provider))
    checked_inputs({str(provider): expected})
    spec = importlib.util.spec_from_file_location('iris_selected_empirical_provider', provider)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.experimental_empirical_simulator(manifest)
    if not isinstance(result, dict) or result.get('available') is not True:
        reason = result.get('reason', 'unavailable') if isinstance(result, dict) else 'invalid descriptor'
        raise ValueError('Selected empirical model refused: ' + str(reason))
    return result


def run(elf, manifest, output, timeout, seed, source_events=None, region_map=None, region_format="iris-json"):
    elf, manifest, output = [Path(p).resolve() for p in (elf, manifest, output)]
    if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
        raise ValueError('Timeout must be greater than0 and at most86400 seconds')
    if type(seed) is not int or not 0 <= seed < 1 << 64:
        raise ValueError('Scheduling seed must be an unsigned64-bit integer')
    output.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 'iris.source_profile_ELF_execution.v1', 'status': 'preflight', 'pass': False,
        'started_at_UTC': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'hardware_contacted': False, 'physical_harts': 3, 'schedule_seed': seed,
        'independent_numeric_validation': None, 'timing_accuracy_certified': False,
        'full_model_certified': False, 'error': None, 'profile_pass': None, 'source_events_requested': source_events is not None, 'source_profile': None}
    receipt_path = output / 'run.json'
    def save():
        temporary = output / '.run.json.tmp'
        temporary.write_text(json.dumps(receipt, indent=2) + '\n')
        temporary.replace(receipt_path)
    save()
    started = time.monotonic()
    proc = None
    try:
        elf_identity, manifest_identity = sha(elf), sha(manifest)
        model = descriptor(manifest)
        journal=None
        if source_events is not None:
            selected=Path(source_events)
            journal=(selected if selected.is_absolute()else output/selected).resolve()
            if not journal.is_relative_to(output) or journal.exists():
                raise ValueError('Source event output must be a new path inside the fresh output directory')
            journal.parent.mkdir(parents=True,exist_ok=True)
        if region_map is not None:region_map=Path(region_map).resolve()
        if model.get('automatic_fatal_handler_resolution') is not True:
            raise ValueError('Select a package with the verified automatic fatal-handler launcher')
        per_hart = model.get('per_hart_isa', {})
        if set(per_hart) != {'0', '1', '2'} or any(not isinstance(v, str) or not v for v in per_hart.values()):
            raise ValueError('Exactly three heterogeneous hart ISA declarations required')
        memory = model.get('memory_regions')
        if (not isinstance(memory, list) or not memory
                or any(not isinstance(r, (list, tuple)) or len(r) != 2
                       or any(type(v) is not int for v in r) or r[0] < 0 or r[1] <= 0 for r in memory)):
            raise ValueError('Explicit real Iris memory regions required')
        inputs = dict(model.get('execution_input_sha256', {}))
        for path, expected in ((manifest, manifest_identity), (elf, elf_identity),
                               (Path(__file__).resolve(), sha(__file__)),
                               (Path(model['binary']), model['binary_sha256'])):
            if str(path) in inputs and inputs[str(path)] != expected:
                raise ValueError('Conflicting execution identity: ' + str(path))
            inputs[str(path)] = expected
        inputs[str(Path(__file__).with_name('source_event_profile_v1.py'))]=sha(Path(__file__).with_name('source_event_profile_v1.py'))
        for dependency in ('empirical_run.py','guest_regions.py','report.py','empirical_profile.py'):
            p=ROOT/'iris_model'/dependency;inputs[str(p)]=sha(p)
        nm=Path('/scratch/eygao/iris-cy/.conda-env/riscv-tools/bin/riscv64-unknown-elf-nm')
        if source_events is not None:inputs[str(nm)]=sha(nm)
        if region_map is not None:inputs[str(region_map)]=sha(region_map)
        checked_inputs(inputs)
        argv = [model['binary'], '--isa=' + per_hart['2'], '-p3',
            '-m' + ','.join(f'{base:#x}:{size:#x}' for base, size in memory), *model['launch_args'],
            *('--hart-isa=' + str(h) + ':' + per_hart[str(h)] for h in range(3)),
            '--iris-schedule-seed=' + str(seed),
            '--iris-timing-trace=' + str(output / 'native-summary.json'), str(elf)]
        if any(not isinstance(arg, str) for arg in argv):
            raise ValueError('Invalid model launch arguments')
        fixture = [arg.split('=', 1)[1] for arg in argv if arg.startswith('--iris-timing-fixture=')]
        if len(fixture) != 1 or str(Path(fixture[0]).resolve()) not in inputs:
            raise ValueError('Unique fitted timing fixture in model input closure required')
        environment = dict(os.environ)
        environment.pop('IRIS_EMPIRICAL_ASYNC_TRACE', None)
        if journal is not None:environment['IRIS_EMPIRICAL_ASYNC_TRACE']=str(journal)
        environment['IRIS_EMPIRICAL_FEATURES'] = str(output / 'features.jsonl')
        receipt.update(status='running', argv=argv, input_sha256=inputs, ELF_sha256=elf_identity,
            fixture_sha256=sha(fixture[0]), calibration_identity=load(manifest)['calibration_identity'],
            simulator_provenance=model.get('provenance'),
            environment={k: v for k, v in environment.items() if k.startswith('IRIS_') or k.startswith('LD_')},
            timeout_seconds=timeout)
        with (output / 'stdout').open('wb') as stdout, (output / 'stderr').open('wb') as stderr:
            proc = subprocess.Popen(argv, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
            receipt['pid'] = proc.pid
            save()
            timed_out = False
            try:
                code = proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                timed_out = True
                # Do not reap the group leader before the second signal: this
                # prevents its group ID being reused during the grace period.
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                time.sleep(.2)
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                code = proc.wait()
        receipt.update(exit_status=code, timed_out=timed_out, source_event_journal=str(journal)if journal is not None else None)
        checked_inputs(inputs)
        receipt.update(status='passed_guest_exit' if code == 0 and not timed_out else 'failed_guest_exit',
            pass_=code == 0 and not timed_out, unchanged_inputs_after_execution=True)
        receipt['pass'] = receipt.pop('pass_')
        if journal is not None:
            try:
                if not journal.exists():raise ValueError('Native did not produce a source journal')
                rom=next((a.split('=',1)[1]for a in model['launch_args']if a.startswith('--iris-bootrom=')),None)
                if not rom:raise ValueError('Source profiling requires the exact bound ROM')
                result=source_profile(journal,output/'features.jsonl',output/'stdout',output/'native-summary.json',elf,rom,output/'source-profile',nm,region_map,region_format,receipt['pass'])
                receipt['profile_pass']=True
                receipt['source_profile']={'available':True,'summary':str(output/'source-profile/summary.json'),'trace':str(output/'source-profile/trace.json'),'journal_complete':result['journal_complete'],'execution_complete':receipt['pass'],'FADD_completed_EG_writes':result['FADD']['completed_EG_writes'],'regions':len(result['regions'])}
            except (OSError,ValueError,KeyError,TypeError)as e:
                receipt['profile_pass']=False
                receipt['source_profile']={'available':False,'reason':str(e)}
    except (Exception, KeyboardInterrupt) as error:
        receipt.update(status='refused_or_failed', error=str(error), **{'pass': False})
    finally:
        if proc is not None and proc.returncode is None:
            # A receipt-write error or signal must not abandon an owned guest.
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
        receipt['host_seconds'] = time.monotonic() - started
        receipt['finished_at_UTC'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt['output_sha256'] = {str(p): sha(p)for p in output.rglob('*')if p.is_file()and p!=receipt_path and p.name!='.run.json.tmp'}
        save()
    return receipt


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=ROOT / 'spike/empirical-current/manifest.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--source-events',nargs='?',const='source-events.jsonl',type=Path,help='Explicit native journal filename inside new outputdir; also exports JSON and Perfetto source timelines')
    parser.add_argument('--region-map',type=Path,help='iris.source_event_region_map.v1 with actual hart/start_cycle/end_cycle CSR values')
    parser.add_argument('--region-format',choices=['none','iris-json','merlin-kernel-v1'],default='iris-json',help='Only explicit supported markers are joined; no guessed counter pairs')
    args = parser.parse_args()
    try:
        result = run(args.elf, args.model, args.output, args.timeout, args.seed,args.source_events,args.region_map,args.region_format)
    except (OSError, ValueError) as error:
        parser.exit(1, 'iris-source-profile-run: ' + str(error) + '\n')
    print(json.dumps({k: result.get(k) for k in
        ('pass', 'profile_pass', 'status', 'exit_status', 'timed_out', 'host_seconds', 'error','source_profile')}))
    return 0 if result['pass'] and result.get('profile_pass')is not False else 1


if __name__ == '__main__':
    raise SystemExit(main())
