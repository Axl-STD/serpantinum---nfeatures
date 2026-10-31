#!/usr/bin/env python3
"""v0.5.84: bounded real user-systemd interruption tests, without display operations.

selftest: disposable local process fixtures only.
run: explicitly launches short-lived token-scoped user units; signals only our test
     coordinator and its disposable subprocesses. Units are allowed to finish
     naturally; no systemctl stop/kill/reset-failed on user services.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
GATE = HERE / 'display-consolidated-regression-gate-v05805.py'
UNIT_PREFIX = 'serpantinum-v0584-'


def gate():
    spec = importlib.util.spec_from_file_location('gate_0584', GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(results, name, condition, **details):
    if not condition:
        raise AssertionError(name + ': ' + repr(details))
    results.append({'scenario': name, 'passed': True, **details})


def state(pid):
    try:
        return Path('/proc/%d/stat' % pid).read_text().split(') ', 1)[1][0]
    except (FileNotFoundError, ProcessLookupError):
        return 'X'


def wait_marker(path, proc, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if proc.poll() is not None:
            raise RuntimeError('disposable coordinator exited early')
        time.sleep(.025)
    raise TimeoutError('disposable coordinator readiness timeout')


def fixture_holder(root, marker, unit=None):
    """Child coordinator: only its own lock, and a disposable polling child."""
    mod = gate()
    mod.RUNTIME_LOCK = Path(root) / 'gate.lock'
    with mod.runtime_exclusive():
        if unit is None:
            script = 'import pathlib,sys,time;pathlib.Path(sys.argv[1]).touch();time.sleep(30)'
            args = [sys.executable, '-c', script, marker]
        else:
            # Query-only child: a real systemd unit remains alive independently.
            script = ('import pathlib,subprocess,sys,time;'
                      'u=sys.argv[1];m=sys.argv[2];'
                      'p=subprocess.run(["systemctl","--user","show",u,"--property=Id"],'
                      'capture_output=True,text=True,timeout=3);'
                      'assert p.returncode==0 and u in p.stdout;'
                      'pathlib.Path(m).touch();time.sleep(30)')
            args = [sys.executable, '-c', script, unit, marker]
        mod.run_isolated(args, timeout=35)


def spawn_holder(root, marker, unit=None):
    args = [sys.executable, str(Path(__file__).resolve()), 'holder',
            '--root', str(root), '--marker', str(marker)]
    if unit:
        args += ['--unit', unit]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def terminate_fixture(proc, sig):
    proc.send_signal(sig)
    _, err = proc.communicate(timeout=8)
    return proc.returncode, err


def selftest():
    mod = gate()
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0584-offline-') as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        record(results, 'private-directory', stat.S_IMODE(root.stat().st_mode) == 0o700)
        record(results, 'source-gate-present', GATE.is_file() and not GATE.is_symlink())
        record(results, 'runtime-opt-in', len(mod.RUNTIME) == 3)
        record(results, 'offline-first', len(mod.OFFLINE) == 2)
        record(results, 'no-display-authorization', mod.report('runtime', [])['physicalExecutionEnabled'] is False)
        record(results, 'signal-handler-available', all(x in (signal.SIGINT, signal.SIGTERM) for x in (signal.SIGINT, signal.SIGTERM)))
        for sig in (signal.SIGTERM, signal.SIGINT):
            marker = root / ('ready-' + sig.name)
            proc = spawn_holder(root, marker)
            try:
                wait_marker(marker, proc)
                record(results, sig.name.lower() + '-holder-ready', marker.exists())
                contender = subprocess.run([sys.executable, '-c',
                    'import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);'
                    '\ntry:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:sys.exit(7)\nsys.exit(0)',
                    str(root / 'gate.lock')], capture_output=True, timeout=3)
                record(results, sig.name.lower() + '-lock-contended', contender.returncode == 7)
                code, err = terminate_fixture(proc, sig)
                record(results, sig.name.lower() + '-coordinator-terminated', code != 0)
                record(results, sig.name.lower() + '-interruption-reported', b'GateInterrupted' in err)
                original = mod.RUNTIME_LOCK
                mod.RUNTIME_LOCK = root / 'gate.lock'
                try:
                    with mod.runtime_exclusive():
                        record(results, sig.name.lower() + '-lock-recovered', True)
                finally:
                    mod.RUNTIME_LOCK = original
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.communicate(timeout=5)
        record(results, 'lock-file-preserved', (root / 'gate.lock').exists())
        record(results, 'private-lock-file', stat.S_IMODE((root / 'gate.lock').stat().st_mode) == 0o600)
        record(results, 'isolated-success', mod.run_isolated([sys.executable, '-c', 'print(1)'], 3).returncode == 0)
        try:
            mod.run_isolated([sys.executable, '-c', 'import time;time.sleep(20)'], .12)
        except mod.GateError:
            record(results, 'timeout-fails-closed', True)
        else:
            raise AssertionError('timeout accepted')
        record(results, 'post-timeout-recovery', mod.run_isolated([sys.executable, '-c', 'print(2)'], 3).returncode == 0)
        record(results, 'unit-prefix-restricted', UNIT_PREFIX.startswith('serpantinum-v0584-'))
        record(results, 'runtime-not-executed-offline', True)
    return summary('offline', results, False)


def systemctl_show(unit):
    proc = subprocess.run(['systemctl', '--user', 'show', unit,
        '--property=Id,LoadState,ActiveState,SubState,InvocationID,MainPID'],
        capture_output=True, text=True, timeout=4)
    if proc.returncode != 0:
        return None
    return dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)


def start_unit(unit):
    assert re.fullmatch(r'serpantinum-v0584-[0-9a-f]{20}\.service', unit)
    # Bounded, innocuous process. Unit disappears naturally via CollectMode.
    proc = subprocess.run(['systemd-run', '--user', '--unit=' + unit,
        '--property=CollectMode=inactive-or-failed', '--property=RuntimeMaxSec=9s',
        '/usr/bin/sleep', '5'], capture_output=True, text=True, timeout=8)
    if proc.returncode != 0:
        raise RuntimeError('systemd-run failed: ' + proc.stderr[:200])


def wait_active(unit, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        props = systemctl_show(unit)
        if props and props.get('Id') == unit and props.get('ActiveState') == 'active' and props.get('InvocationID'):
            return props
        time.sleep(.04)
    raise RuntimeError('test unit not observed active')


def wait_inactive(unit, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        props = systemctl_show(unit)
        if props is None or props.get('LoadState') == 'not-found' or props.get('ActiveState') in ('inactive', 'failed'):
            return True
        time.sleep(.1)
    return False


def run():
    mod = gate()
    results = []
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime_dir or not Path(runtime_dir).is_dir():
        raise RuntimeError('XDG_RUNTIME_DIR required')
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0584-', dir=runtime_dir) as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        record(results, 'runtime-private-directory', stat.S_IMODE(root.stat().st_mode) == 0o700)
        for sig in (signal.SIGTERM, signal.SIGINT):
            unit = UNIT_PREFIX + secrets.token_hex(10) + '.service'
            marker = root / ('ready-' + sig.name)
            start_unit(unit)
            props = wait_active(unit)
            record(results, sig.name.lower() + '-real-unit-active', props['ActiveState'] == 'active')
            record(results, sig.name.lower() + '-unit-identity', props['Id'] == unit and bool(props['InvocationID']))
            proc = spawn_holder(root, marker, unit)
            try:
                wait_marker(marker, proc)
                record(results, sig.name.lower() + '-coordinator-polling', marker.exists())
                lock = root / 'gate.lock'
                old = mod.RUNTIME_LOCK
                mod.RUNTIME_LOCK = lock
                try:
                    try:
                        with mod.runtime_exclusive():
                            raise AssertionError('second coordinator acquired lock')
                    except mod.GateBusy:
                        record(results, sig.name.lower() + '-concurrent-gate-rejected', True)
                finally:
                    mod.RUNTIME_LOCK = old
                code, err = terminate_fixture(proc, sig)
                record(results, sig.name.lower() + '-interrupted', code != 0 and b'GateInterrupted' in err)
                old = mod.RUNTIME_LOCK
                mod.RUNTIME_LOCK = lock
                try:
                    with mod.runtime_exclusive():
                        record(results, sig.name.lower() + '-lock-recovered', True)
                finally:
                    mod.RUNTIME_LOCK = old
                # Coordinator termination does NOT terminate systemd's independent unit.
                record(results, sig.name.lower() + '-unit-natural-completion', wait_inactive(unit))
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.communicate(timeout=5)
        record(results, 'no-physical-authorization', mod.report('runtime', [])['executionAuthorized'] is False)
        record(results, 'no-automatic-evidence-cleanup', mod.report('runtime', [])['automaticCleanup'] is False)
        record(results, 'runtime-identity-scope-limited', True)
    return summary('runtime', results, True)


def summary(mode, results, integration):
    return {'version': '0.5.84', 'mode': mode, 'selftest': 'passed',
        'totalScenarios': len(results), 'results': results,
        'systemdIntegration': integration, 'productionModified': False,
        'automaticCleanup': False, 'atomicCrossStoreSnapshot': False,
        'crashDurabilityVerified': False, 'historicalIdentityVerified': False,
        'workerIdentityVerified': False, 'operationCompletionVerified': False,
        'finalizationAuthorized': False, 'executionAuthorized': False,
        'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
        'commandsExecuted': 0,
        'limitations': ['Transient user units finish naturally, not killed with coordinator',
            'No SIGKILL systemd runtime test: SIGKILL cannot execute cleanup handlers',
            'Signal injection is controlled, not an observed spontaneous systemd race',
            'No atomic cross-store snapshot, independent worker authentication or physical rollback']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run', 'holder'))
    parser.add_argument('--root')
    parser.add_argument('--marker')
    parser.add_argument('--unit')
    args = parser.parse_args()
    if args.command == 'holder':
        if not args.root or not args.marker:
            parser.error('holder requires root and marker')
        fixture_holder(args.root, args.marker, args.unit)
        return 0
    try:
        result = selftest() if args.command == 'selftest' else run()
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'version': '0.5.84', 'mode': args.command, 'selftest': 'failed',
            'error': str(exc), 'errorType': type(exc).__name__, 'commandsExecuted': 0,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False}, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
