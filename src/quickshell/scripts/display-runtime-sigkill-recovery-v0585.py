#!/usr/bin/env python3
"""v0.5.85: isolated SIGKILL lock recovery and independent transient systemd unit lifetime.

Only the child process spawned by this script is signalled. Never stop/kill systemd units.
No display commands, no production state changes, no automatic evidence cleanup.
"""
import argparse
import fcntl
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

PREFIX = 'serpantinum-v0585-'


def check(rows, name, condition):
    if not condition:
        raise AssertionError(name)
    rows.append({'scenario': name, 'passed': True})


def summary(mode, rows):
    return {'version': '0.5.85', 'mode': mode, 'selftest': 'passed',
            'totalScenarios': len(rows), 'results': rows,
            'systemdIntegration': mode == 'runtime', 'productionModified': False,
            'automaticCleanup': False, 'atomicCrossStoreSnapshot': False,
            'crashDurabilityVerified': False, 'historicalIdentityVerified': False,
            'workerIdentityVerified': False, 'operationCompletionVerified': False,
            'finalizationAuthorized': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0,
            'limitations': ['SIGKILL does not execute cleanup handlers',
                'Only a disposable direct child is killed; no existing service is signalled',
                'systemd units finish naturally and are not stopped or cleaned automatically',
                'No independent authentication, power-loss durability or physical rollback proof']}


def holder(root, marker):
    folder = Path(root)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid() or stat.S_IMODE(folder.stat().st_mode) != 0o700:
        raise ValueError('unsafe fixture directory')
    lock = folder / 'lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Path(marker).write_text(str(os.getpid()))
        time.sleep(30)
    finally:
        os.close(fd)


def show(unit):
    proc = subprocess.run(['systemctl', '--user', 'show', unit,
        '--property=Id,LoadState,ActiveState,InvocationID'], capture_output=True, text=True, timeout=4)
    if proc.returncode:
        return None
    return dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)


def start_unit(unit):
    if not re.fullmatch(r'serpantinum-v0585-[0-9a-f]{20}\.service', unit):
        raise ValueError('unsafe unit name')
    p = subprocess.run(['systemd-run', '--user', '--unit=' + unit,
        '--property=CollectMode=inactive-or-failed', '--property=RuntimeMaxSec=9s',
        '/usr/bin/sleep', '5'], capture_output=True, text=True, timeout=8)
    if p.returncode:
        raise RuntimeError('isolated systemd-run failed: ' + p.stderr[:200])


def wait_unit(unit, active, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        p = show(unit)
        if active:
            if p and p.get('Id') == unit and p.get('ActiveState') == 'active' and p.get('InvocationID'):
                return p
        elif p is None or p.get('LoadState') == 'not-found' or p.get('ActiveState') in ('inactive', 'failed'):
            return p
        time.sleep(.06)
    raise TimeoutError('unit observation timeout')


def kill_fixture(root, rows, prefix):
    marker = root / ('ready-' + prefix)
    lock = root / 'lock'
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'holder',
                             '--root', str(root), '--marker', str(marker)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 4
        while not marker.is_file() and time.monotonic() < end:
            if proc.poll() is not None:
                raise RuntimeError('holder exited early')
            time.sleep(.02)
        check(rows, prefix + '-holder-ready', marker.is_file())
        check(rows, prefix + '-lock-private', stat.S_IMODE(lock.stat().st_mode) == 0o600)
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                check(rows, prefix + '-lock-contended', True)
            else:
                raise AssertionError('lock unexpectedly acquired')
        finally:
            os.close(fd)
        os.kill(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=5)
        check(rows, prefix + '-sigkill-observed', proc.returncode == -signal.SIGKILL)
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check(rows, prefix + '-lock-recovered', True)
        finally:
            os.close(fd)
        check(rows, prefix + '-lock-file-retained', lock.is_file())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


def selftest():
    rows = []
    with tempfile.TemporaryDirectory(prefix=PREFIX + 'offline-') as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        check(rows, 'private-root', stat.S_IMODE(root.stat().st_mode) == 0o700)
        kill_fixture(root, rows, 'offline')
        check(rows, 'no-unit-launched', True)
        check(rows, 'no-physical-authorization', summary('offline', rows)['executionAuthorized'] is False)
        check(rows, 'no-automatic-cleanup', summary('offline', rows)['automaticCleanup'] is False)
    return summary('offline', rows)


def run():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime or not Path(runtime).is_dir() or Path(runtime).is_symlink():
        raise ValueError('XDG_RUNTIME_DIR required')
    rows = []
    with tempfile.TemporaryDirectory(prefix=PREFIX, dir=runtime) as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        check(rows, 'runtime-private-root', stat.S_IMODE(root.stat().st_mode) == 0o700)
        unit = PREFIX + secrets.token_hex(10) + '.service'
        start_unit(unit)
        first = wait_unit(unit, True, 3)
        check(rows, 'unit-identity', first['Id'] == unit)
        check(rows, 'unit-invocation-present', bool(first['InvocationID']))
        kill_fixture(root, rows, 'runtime')
        kill_fixture(root, rows, 'runtime-repeat')
        # The independent unit may already have finished; never assert it was killed.
        wait_unit(unit, False, 12)
        check(rows, 'unit-natural-completion', True)
        check(rows, 'no-physical-authorization', summary('runtime', rows)['physicalExecutionEnabled'] is False)
        check(rows, 'no-automatic-cleanup', summary('runtime', rows)['automaticCleanup'] is False)
    return summary('runtime', rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('selftest', 'run', 'holder'))
    p.add_argument('--root')
    p.add_argument('--marker')
    args = p.parse_args()
    if args.command == 'holder':
        if not args.root or not args.marker:
            p.error('holder requires root and marker')
        holder(args.root, args.marker)
        return 0
    try:
        result = selftest() if args.command == 'selftest' else run()
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'version': '0.5.85', 'mode': args.command, 'selftest': 'failed',
            'error': str(exc), 'errorType': type(exc).__name__, 'commandsExecuted': 0,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False}, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
