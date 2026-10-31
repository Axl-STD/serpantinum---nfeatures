#!/usr/bin/env python3
"""Serpantinum v0.5.39: isolated diagnostic closure, never physical recovery.

A closure records only consistency of existing evidence. A missing systemd unit
is NOT proof of the original worker's identity or successful completion.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
CLOSURE = 'diagnostic-closure-v0539.json'
UNIT_PREFIX = 'serpantinum-v0536-reconcile-'


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit_absent(token):
    """Fail closed if systemd cannot be queried or a unit is loaded/active."""
    name = UNIT_PREFIX + token + '.service'
    result = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
                             '-p', 'LoadState', '-p', 'ActiveState', '-p', 'MainPID',
                             '-p', 'ControlPID'], capture_output=True, text=True, timeout=6)
    if result.returncode:
        raise ValueError('Unable to query systemd unit')
    props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if props.get('LoadState') != 'not-found' or props.get('ActiveState') != 'inactive' or props.get('MainPID') != '0' or props.get('ControlPID') != '0':
        raise ValueError('Systemd unit not demonstrably unloaded')
    return {'unit': name, 'loadState': 'not-found', 'activeState': 'inactive',
            'workerIdentityVerified': False}


def close(root, token, probe=unit_absent):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    audit = load('display-evidence-consolidator.py', 'audit_0539')
    bundle = load('display-recovery-bundle.py', 'bundle_0539')
    folder = Path(root)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Invalid isolated root')
    st = folder.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('Unsafe isolated root')
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        resolved = folder.resolve()
        live = (Path(runtime) / 'serpantinum-display-v0513').resolve()
        if resolved == live or resolved == Path(runtime).resolve() or live in resolved.parents:
            raise ValueError('Live runtime access prohibited')
    # Audit takes the shared lock itself; do not nest flock on another fd.
    # Capture file bytes before audit, then recheck under the exclusive lock.
    import hashlib
    def file_hashes():
        values = {}
        for name in audit.FILES:
            path = folder / name
            if path.is_symlink():
                raise ValueError('Evidence symlink')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fst = os.fstat(fd)
                if not stat.S_ISREG(fst.st_mode) or fst.st_uid != os.getuid() or fst.st_mode & 0o077 or fst.st_size > 1024 * 1024:
                    raise ValueError('Unsafe evidence file')
                values[name] = hashlib.sha256(os.read(fd, 1024 * 1024 + 1)).hexdigest()
            finally:
                os.close(fd)
        return values
    before_hashes = file_hashes()
    report = audit.inspect(folder, token)
    with bundle.locked(folder):
        if file_hashes() != before_hashes:
            raise ValueError('Evidence changed during audit')
        target = folder / CLOSURE
        if target.exists() or target.is_symlink():
            raise ValueError('Diagnostic closure already exists')
        if report.get('consistent') is not True or report.get('journalPhase') != 'rollback-running':
            raise ValueError('Evidence inconsistent or wrong phase')
        evidence = probe(token)
        if not isinstance(evidence, dict) or evidence.get('loadState') != 'not-found' or evidence.get('activeState') != 'inactive' or evidence.get('workerIdentityVerified') is not False:
            raise ValueError('Unsafe worker evidence')
        hashes = before_hashes
        result = {'version': '0.5.39', 'mode': 'isolated-diagnostic-closure',
                  'transactionId': token, 'phase': 'diagnostic-closed',
                  'assessment': report['assessment'], 'evidenceConsistent': True,
                  'evidenceFileSha256': hashes, 'unit': evidence['unit'],
                  'unitUnloadedObserved': True, 'workerIdentityVerified': False,
                  'journalUnchanged': True, 'physicalRollbackAttempted': False,
                  'physicalRollbackVerified': False, 'physicalExecutionEnabled': False,
                  'automaticRetryAuthorized': False, 'commandsExecuted': 0,
                  'note': 'Diagnostic closure only. Unit absence is not proof of worker identity or physical rollback.'}
        bundle.atomic_write(target, result)
        return result


def selftest():
    # Exercise rejection behavior without touching a real transaction or systemd.
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0539-test-') as root:
        os.chmod(root, 0o700)
        token = 'a' * 24
        try:
            close(root, 'wrong', lambda _: {})
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid identity accepted')
        try:
            close(root, token, lambda _: {})
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError('Missing evidence accepted')
        assert not (Path(root) / CLOSURE).exists()
    return {'selftest': 'passed', 'invalidIdentityRejected': True,
            'missingEvidenceRejected': True, 'commandsExecuted': 0,
            'physicalRollbackVerified': False, 'systemdIntegration': 'not-run'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('selftest', 'close'))
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    args = p.parse_args()
    result = selftest() if args.command == 'selftest' else close(args.root, args.token)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
