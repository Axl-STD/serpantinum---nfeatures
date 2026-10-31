#!/usr/bin/env python3
"""v0.5.79.2: isolated real systemd worker receipt/metadata binding.

Only executes this script as an unprivileged transient user service. No display
commands, physical rollback, or authorization. The worker writes its own receipt.
"""
import argparse
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


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    if spec is None or spec.loader is None:
        raise RuntimeError('Missing module: ' + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(token, root):
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_worker_05792')
    invocation = os.environ.get('INVOCATION_ID', '').lower()
    receipt = binding.worker_receipt(token, invocation, os.getpid())
    folder = Path(root)
    if folder.is_symlink() or not folder.is_dir() or folder.stat().st_mode & 0o077:
        raise ValueError('Unsafe receipt directory')
    # Stage privately, then publish exclusively. Never expose partial receipt.json.
    path = folder / 'receipt.json'
    fd, staged = tempfile.mkstemp(prefix='.receipt-staged-', dir=folder)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(binding.canonical(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-replace publication on the same filesystem.
        # Unlike os.replace, os.link fails if the final name already exists.
        os.link(staged, path, follow_symlinks=False)
        dirfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.lexists(staged):
            os.unlink(staged)
    return 0


def verify(token, metadata, receipt):
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_verify_05792')
    return binding.assess(token, metadata, receipt)


def negative_tests(token, metadata, receipt):
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_negative_05792')
    payload = receipt['payload']
    cases = [
        ('wrong-token', binding.worker_receipt('b' * 24, payload['invocationId'], payload['workerPid'])),
        ('wrong-invocation', binding.worker_receipt(token, 'f' * 32 if payload['invocationId'] != 'f' * 32 else 'e' * 32, payload['workerPid'])),
        ('wrong-pid', binding.worker_receipt(token, payload['invocationId'], payload['workerPid'] + 1)),
        ('missing-receipt', None),
        ('tampered-hash', {**receipt, 'sha256': '0' * 64}),
        ('tampered-payload', {**receipt, 'payload': {**payload, 'workerPid': payload['workerPid'] + 1}}),
        ('unsafe-receipt', {'payload': {**payload, 'physicalExecutionEnabled': True},
                            'sha256': binding.digest({**payload, 'physicalExecutionEnabled': True})}),
    ]
    results = []
    for name, candidate in cases:
        result = binding.assess(token, metadata, candidate)
        ok = result['receiptMetadataConsistent'] is False and result['executionAuthorized'] is False
        results.append({'scenario': name, 'passed': ok})
    for name, altered in [
        ('unverified-metadata', {**metadata, 'systemdExecutionMetadataConsistent': False}),
        ('wrong-metadata-token', {**metadata, 'transactionId': 'b' * 24}),
        ('unsafe-metadata', {**metadata, 'executionAuthorized': True}),
    ]:
        result = binding.assess(token, altered, receipt)
        results.append({'scenario': name, 'passed': result['receiptMetadataConsistent'] is False})
    return results


def run():
    historical = load('display-historical-worker-attestation-v05789.py', 'historical_05792')
    reconciliation = load('display-systemd-reconciliation.py', 'reconcile_05792')
    token = secrets.token_hex(12)
    unit = reconciliation.unit_name(token)
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05792-') as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        started = False
        try:
            cmd = ['systemd-run', '--user', '--unit=' + unit,
                   '--property=Type=oneshot', '--property=RemainAfterExit=yes',
                   '--description=' + reconciliation.MARKER + token,
                   sys.executable, str(Path(__file__).resolve()), 'worker', '--token', token,
                   '--root', str(root)]
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if process.returncode != 0:
                raise RuntimeError('systemd-run failed: ' + process.stderr[:300])
            started = True
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                meta = historical.probe(token)
                if meta.get('systemdExecutionMetadataConsistent') is True:
                    break
                if meta.get('assessment') == 'rejected':
                    raise RuntimeError('systemd metadata rejected: ' + str(meta.get('reason')))
                time.sleep(0.1)
            else:
                raise RuntimeError('Worker did not reach verified retained state')
            path = root / 'receipt.json'
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise RuntimeError('Missing or unsafe worker receipt')
            receipt = json.loads(path.read_text(encoding='utf-8'))
            positive = verify(token, meta, receipt)
            results.append({'scenario': 'real-worker-receipt',
                            'passed': positive['receiptMetadataConsistent'] is True,
                            'assessment': positive['assessment']})
            results.extend(negative_tests(token, meta, receipt))
        finally:
            if started:
                subprocess.run(['systemctl', '--user', 'stop', unit],
                               capture_output=True, text=True, timeout=8)
    passed = all(item['passed'] for item in results) and len(results) == 11
    return {'version': '0.5.79.2', 'mode': 'isolated-real-systemd-worker-receipt',
            'selftest': 'passed' if passed else 'failed', 'totalScenarios': len(results),
            'results': results, 'productionModified': False, 'systemdIntegration': True,
            'receiptMetadataConsistent': bool(results and results[0]['passed']),
            'historicalIdentityVerified': False, 'workerIdentityVerified': False,
            'operationCompletionVerified': False, 'finalizationAuthorized': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'limitations': ['Real worker generated the receipt, but local SHA256 is not authentication',
                            'No physical rollback, crash durability, or privileged identity proof']}


def selftest():
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_selftest_05792')
    token = 'a' * 24
    metadata = {'systemdExecutionMetadataConsistent': True, 'transactionId': token,
                'invocationId': '1' * 32, 'execMainPid': 12345,
                'historicalIdentityVerified': False, 'executionAuthorized': False,
                'physicalRollbackVerified': False}
    receipt = binding.worker_receipt(token, '1' * 32, 12345)
    cases = [{'scenario': 'consistent-offline', 'passed': verify(token, metadata, receipt)['receiptMetadataConsistent']}]
    cases.extend(negative_tests(token, metadata, receipt))
    return {'version': '0.5.79.2', 'mode': 'offline-runtime-harness-selftest',
            'selftest': 'passed' if len(cases) == 11 and all(c['passed'] for c in cases) else 'failed',
            'totalScenarios': len(cases), 'results': cases, 'systemdIntegration': False,
            'productionModified': False, 'historicalIdentityVerified': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'run', 'worker'])
    parser.add_argument('--token')
    parser.add_argument('--root')
    args = parser.parse_args()
    if args.command == 'worker':
        if not args.token or not args.root:
            parser.error('worker requires --token and --root')
        return worker(args.token, args.root)
    try:
        result = selftest() if args.command == 'selftest' else run()
    except Exception as exc:
        result = {'version': '0.5.79.2', 'selftest': 'failed', 'error': str(exc),
                  'physicalExecutionEnabled': False, 'executionAuthorized': False,
                  'physicalRollbackVerified': False, 'commandsExecuted': 0}
    print(json.dumps(result, indent=2))
    return 0 if result.get('selftest') == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())
