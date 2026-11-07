"""Run a baremetal or Zephyr ELF on the explicitly selected empirical Iris model.

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


def run(elf, manifest, output, timeout, seed):
    elf, manifest, output = [Path(p).resolve() for p in (elf, manifest, output)]
    if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
        raise ValueError('Timeout must be greater than0 and at most86400 seconds')
    if type(seed) is not int or not 0 <= seed < 1 << 64:
        raise ValueError('Scheduling seed must be an unsigned64-bit integer')
    output.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 'iris.empirical_ELF_execution.v1', 'status': 'preflight', 'pass': False,
        'started_at_UTC': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'hardware_contacted': False, 'physical_harts': 3, 'schedule_seed': seed,
        'independent_numeric_validation': None, 'timing_accuracy_certified': False,
        'full_model_certified': False, 'error': None}
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
        receipt.update(exit_status=code, timed_out=timed_out)
        checked_inputs(inputs)
        receipt.update(status='passed_guest_exit' if code == 0 and not timed_out else 'failed_guest_exit',
            pass_=code == 0 and not timed_out, unchanged_inputs_after_execution=True)
        receipt['pass'] = receipt.pop('pass_')
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
        receipt['output_sha256'] = {str(p): sha(p) for p in
            (output / 'stdout', output / 'stderr', output / 'features.jsonl', output / 'native-summary.json')
            if p.is_file()}
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
    args = parser.parse_args()
    try:
        result = run(args.elf, args.model, args.output, args.timeout, args.seed)
    except (OSError, ValueError) as error:
        parser.exit(1, 'iris-empirical-run: ' + str(error) + '\n')
    print(json.dumps({k: result.get(k) for k in
        ('pass', 'status', 'exit_status', 'timed_out', 'host_seconds', 'error')}))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
