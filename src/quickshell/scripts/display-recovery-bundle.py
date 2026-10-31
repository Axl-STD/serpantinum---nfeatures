#!/usr/bin/env python3
"""Serpantinum v0.5.13: sealed recovery evidence, simulation ONLY.

No monitor commands, no physical rollback, no persistent configuration writes.
The bundle is evidence for a future executor, NOT execution authorization.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def load_preflight():
    path = HERE / 'display-preflight-coordinator.py'
    spec = importlib.util.spec_from_file_location('display_preflight_0513', path)
    if spec is None or spec.loader is None:
        raise ValueError('Missing preflight dependency')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runtime_dir(root=None):
    base = Path(root) if root is not None else Path(os.environ['XDG_RUNTIME_DIR'])
    if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid():
        raise ValueError('Unsafe runtime directory')
    folder = base / 'serpantinum-display-v0513'
    if folder.is_symlink():
        raise ValueError('Unsafe state directory symlink')
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
        raise ValueError('Unsafe state directory permissions')
    return folder


@contextlib.contextmanager
def locked(folder):
    fd = os.open(folder / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(canonical(data))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_bundle(folder):
    path = folder / 'bundle.json'
    if path.is_symlink():
        raise ValueError('Unsafe bundle symlink')
    if not path.exists():
        return None
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('Unsafe bundle permissions')
    envelope = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(envelope, dict) or 'payload' not in envelope or 'sha256' not in envelope:
        raise ValueError('Invalid bundle')
    actual = hashlib.sha256(canonical(envelope['payload'])).hexdigest()
    if actual != envelope['sha256']:
        raise ValueError('Bundle integrity mismatch')
    return envelope['payload']


def prepare(config, root=None, preflight=None, snapshot=None):
    folder = runtime_dir(root)
    with locked(folder):
        # A cleanup receipt must be explicitly archived before reusing this slot.
        # Never silently overwrite evidence of a completed transaction.
        if (folder / 'cleanup-receipt.json').exists() or (folder / 'cleanup-receipt.json').is_symlink():
            raise ValueError('Cleanup receipt exists; archive it before preparing another transaction')
        existing = read_bundle(folder)
        if existing is not None:
            raise ValueError('Bundle already exists; clear explicitly before preparing another')
        preflight = preflight or load_preflight().preflight
        snapshot = snapshot or load_preflight().capture
        result = preflight(config)
        if (result.get('phase') != 'prepared-read-only' or
            result.get('structurallyValid') is not True or
            result.get('nativeConfigParserAccepted') is not True or
            result.get('safeToExecuteAutomatically') is not False):
            raise ValueError('Preflight rejected')
        before = snapshot()
        after = snapshot()
        if not isinstance(before.get('outputs'), list) or not before['outputs'] or before != after:
            raise ValueError('Monitor state changed during preparation')
        payload = {'schemaVersion': 1, 'version': '0.5.13', 'id': secrets.token_hex(12),
                   'phase': 'sealed-read-only', 'snapshot': before,
                   'preflight': result, 'physicalRollbackVerified': False,
                   'safeToExecuteAutomatically': False, 'commandsExecuted': 0}
        atomic_write(folder / 'bundle.json', {
            'payload': payload, 'sha256': hashlib.sha256(canonical(payload)).hexdigest()})
        return summary(payload, True)


def summary(payload, integrity):
    return {'version': '0.5.13', 'phase': payload['phase'], 'transactionId': payload['id'],
            'outputs': [o['name'] for o in payload['snapshot']['outputs']],
            'integrityVerified': integrity, 'safeToExecuteAutomatically': False,
            'commandsExecuted': 0, 'physicalOutputsChanged': False,
            'physicalRollbackVerified': False}


def status(root=None):
    folder = runtime_dir(root)
    with locked(folder):
        payload = read_bundle(folder)
        return summary(payload, True) if payload else {'version': '0.5.13', 'phase': 'idle', 'pending': False}


def clear(root=None):
    folder = runtime_dir(root)
    with locked(folder):
        read_bundle(folder)  # refuse to erase corrupt evidence
        (folder / 'bundle.json').unlink(missing_ok=True)
    return {'version': '0.5.13', 'phase': 'cleared', 'physicalOutputsChanged': False}


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        sample = {'outputs': [{'name': 'eDP-2', 'width': 1920}]}
        approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                    'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
        a = prepare(Path('/unused'), tmp, lambda c: approved, lambda: sample)
        assert a['integrityVerified'] and status(tmp)['phase'] == 'sealed-read-only'
        try:
            prepare(Path('/unused'), tmp, lambda c: approved, lambda: sample)
        except ValueError:
            pass
        else:
            raise AssertionError('Duplicate bundle accepted')
        path = Path(tmp) / 'serpantinum-display-v0513/bundle.json'
        content = json.loads(path.read_text())
        content['payload']['snapshot']['outputs'][0]['width'] = 999
        path.write_text(json.dumps(content))
        try:
            status(tmp)
        except ValueError:
            pass
        else:
            raise AssertionError('Tampered bundle accepted')
    with tempfile.TemporaryDirectory() as tmp:
        try:
            prepare(Path('/unused'), tmp, lambda c: dict(approved, structurallyValid=False), lambda: sample)
        except ValueError:
            pass
        else:
            raise AssertionError('Rejected preflight accepted')
        assert status(tmp)['phase'] == 'idle'
    return {'selftest': 'passed', 'duplicateRejected': True, 'tamperingRejected': True,
            'failedPreflightRejected': True, 'physicalOutputsChanged': False,
            'commandsExecuted': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'prepare', 'status', 'clear'])
    p.add_argument('--config', type=Path, default=Path.home() / '.config/hypr/monitors.conf')
    args = p.parse_args()
    try:
        if args.command == 'selftest':
            result = selftest()
        elif args.command == 'prepare':
            result = prepare(args.config)
        elif args.command == 'status':
            result = status()
        else:
            result = clear()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
