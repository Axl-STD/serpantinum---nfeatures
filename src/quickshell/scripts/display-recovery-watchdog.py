#!/usr/bin/env python3
"""Serpantinum v0.5.8: systemd-backed snapshot verification simulation.

READ ONLY for display hardware. This does not apply or roll back monitor rules.
A matching snapshot is NEVER reported as a physical rollback.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
PREFIX = 'serpantinum-display-v058-'


def dependency(filename, name):
    path = HERE / filename
    if not path.is_file():
        raise RuntimeError('Missing dependency: ' + filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def directory():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base:
        raise RuntimeError('XDG_RUNTIME_DIR is required')
    root = Path(base)
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe runtime directory')
    path = root / 'serpantinum-display-v058'
    if path.is_symlink():
        raise RuntimeError('Unsafe state directory symlink')
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe state directory permissions')
    return path


@contextlib.contextmanager
def locked(path):
    fd = os.open(path / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_state(path):
    target = path / 'state.json'
    if target.is_symlink():
        raise RuntimeError('Unsafe state symlink')
    return json.loads(target.read_text()) if target.exists() else {'phase': 'idle', 'pending': False}


def atomic_json(target, data):
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def snapshot():
    proc = subprocess.run([sys.executable, str(HERE / 'display-transaction.py'), 'snapshot'],
                          capture_output=True, text=True, timeout=8, check=True)
    result = json.loads(proc.stdout)
    if not isinstance(result.get('outputs'), list) or not result['outputs']:
        raise RuntimeError('Empty or invalid monitor snapshot')
    return result


def service_active(unit):
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                            capture_output=True, text=True, timeout=6)
    return result.returncode == 0 and 'ActiveState=active' in result.stdout.splitlines()


def begin(path, seconds):
    if not 5 <= seconds <= 120:
        raise ValueError('seconds must be between 5 and 120')
    before = snapshot()
    with locked(path):
        state = read_state(path)
        if state.get('pending'):
            raise RuntimeError('Transaction already pending')
        token = secrets.token_hex(12)
        unit = PREFIX + token + '.service'
        state = dict(schemaVersion=1, version='0.5.8', mode='simulation-only',
                     id=token, unit=unit, phase='starting', pending=True,
                     deadline=time.time() + seconds, timeoutSeconds=seconds,
                     watchdogReady=False, physicalOutputsChanged=False,
                     persistentConfigChanged=False, physicalRollbackAttempted=False,
                     physicalRollbackVerified=False)
        atomic_json(path / 'snapshot.json', before)
        atomic_json(path / 'state.json', state)
    try:
        subprocess.run(['systemd-run', '--user', '--unit=' + unit[:-8], '--collect', '--quiet',
                        sys.executable, str(Path(__file__).resolve()), '_watch', '--id', token],
                       capture_output=True, text=True, timeout=8, check=True)
        limit = time.monotonic() + 6
        while time.monotonic() < limit:
            with locked(path):
                current = read_state(path)
                ready = current.get('id') == token and current.get('watchdogReady')
            if ready and service_active(unit):
                return current
            time.sleep(0.1)
        raise RuntimeError('Watchdog readiness not confirmed')
    except Exception:
        with locked(path):
            current = read_state(path)
            if current.get('id') == token and current.get('pending'):
                current.update(phase='startup-failed', pending=False, watchdogReady=False)
                atomic_json(path / 'state.json', current)
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=6)
        raise


def evaluate(before, after=None, fail=False):
    if fail:
        return dict(phase='rollback-failed', equivalentObservableState=None,
                    differences=[], error='Injected verification failure')
    report = dependency('display-restore-verify.py', 'display_v058_verify').compare(before['outputs'], after['outputs'])
    return dict(phase='snapshot-equivalent' if report['equivalentObservableState'] else 'rollback-incomplete',
                equivalentObservableState=report['equivalentObservableState'],
                differences=report['differences'])


def watch(path, token):
    with locked(path):
        state = read_state(path)
        if state.get('id') != token or not state.get('pending'):
            return
        state.update(phase='simulated-pending', watchdogReady=True)
        atomic_json(path / 'state.json', state)
    while True:
        with locked(path):
            state = read_state(path)
            if state.get('id') != token or not state.get('pending'):
                return
            remaining = state['deadline'] - time.time()
            if remaining <= 0:
                state.update(phase='verification-running')
                atomic_json(path / 'state.json', state)
                break
        time.sleep(min(0.25, max(0.01, remaining)))
    try:
        before = json.loads((path / 'snapshot.json').read_text())
        outcome = evaluate(before, snapshot())
    except Exception as exc:
        outcome = dict(phase='rollback-failed', equivalentObservableState=None,
                       differences=[], error=str(exc))
    with locked(path):
        state = read_state(path)
        if state.get('id') == token and state.get('phase') == 'verification-running':
            state.update(outcome)
            state.update(pending=False, finishedAt=time.time(),
                         note='Snapshot comparison ONLY; no physical rollback performed')
            atomic_json(path / 'state.json', state)


def confirm(path, token):
    with locked(path):
        state = read_state(path)
        if not state.get('pending') or not state.get('watchdogReady') or state.get('id') != token:
            raise RuntimeError('No matching ready transaction')
        if time.time() >= state['deadline']:
            raise RuntimeError('Deadline expired')
        state.update(phase='simulated-confirmed', pending=False, finishedAt=time.time())
        atomic_json(path / 'state.json', state)
        return state


def selftest():
    sample = {'outputs': [{'name':'eDP-2','width':1920,'height':1080,'refreshRate':60.061,
                           'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'mirrorOf':'none'}]}
    ok = evaluate(sample, json.loads(json.dumps(sample)))
    incomplete = evaluate(sample, {'outputs':[dict(sample['outputs'][0], x=100)]})
    failed = evaluate(sample, fail=True)
    assert ok['phase'] == 'snapshot-equivalent'
    assert incomplete['phase'] == 'rollback-incomplete' and incomplete['differences']
    assert failed['phase'] == 'rollback-failed'
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)
        atomic_json(path / 'state.json', {'id':'abc', 'pending':True, 'watchdogReady':True,
                                           'deadline':time.time()+20, 'phase':'simulated-pending'})
        assert confirm(path,'abc')['phase'] == 'simulated-confirmed'
        assert not read_state(path)['pending']
    return {'selftest':'passed', 'scenarios':['snapshot-equivalent','rollback-incomplete',
            'rollback-failed','simulated-confirmed'], 'systemdIntegration':'not-run',
            'physicalRollbackAttempted':False,'physicalRollbackVerified':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['selftest','start','status','confirm','_watch'])
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--id')
    args = parser.parse_args()
    if args.action == 'selftest':
        result = selftest()
    else:
        path = directory()
        if args.action == 'start':
            result = begin(path, args.seconds)
        elif args.action == 'status':
            with locked(path):
                result = read_state(path)
            if result.get('unit'):
                result = dict(result, systemdActive=service_active(result['unit']))
        elif args.action == 'confirm':
            if not args.id:
                parser.error('--id is required')
            result = confirm(path, args.id)
        else:
            if not args.id:
                parser.error('--id is required')
            watch(path, args.id)
            return 0
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, AssertionError) as exc:
        print(json.dumps({'error':str(exc),'physicalRollbackVerified':False}), file=sys.stderr)
        sys.exit(1)
