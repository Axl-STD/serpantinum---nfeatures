#!/usr/bin/env python3
"""v0.5.79.9.4: isolated systemd historical identity integration, no display operations."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from importlib.util import module_from_spec, spec_from_file_location
HERE = Path(__file__).resolve().parent
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified', 'operationCompletionVerified',
        'finalizationAuthorized', 'executionAuthorized', 'physicalExecutionEnabled',
        'physicalRollbackVerified')


def load(name):
    spec = spec_from_file_location('v057994_' + name.replace('-', '_').replace('.', '_'), HERE / name)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(out):
    return (isinstance(out, dict) and out.get('commandsExecuted') == 0
            and all(out.get(k) is False for k in SAFE))


def scenarios(token, meta, receipt, *, actual_root=None):
    adapter = load('display-worker-historical-state-binding-v057991.py')
    binding = load('display-worker-evidence-binding-v05791.py')
    results = []
    def record(name, ok):
        results.append({'scenario': name, 'passed': bool(ok)})
    if actual_root is not None:
        out = adapter.inspect(actual_root, token, metadata_probe=lambda _: meta)
        record('real-final-only-unverified', check(out) and out['assessment'] == 'final-only-unverified'
               and out['receiptMetadataConsistent'] is False and out['systemdExecutionMetadataConsistent'] is True)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v057994-negative-') as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        staged = root / '.receipt-staged-fixture'
        final = root / 'receipt.json'
        def inspect(candidate, *, final_only=False, metadata=meta):
            staged.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            target = final if final_only else staged
            if candidate is not None:
                target.write_bytes(binding.canonical(candidate))
                target.chmod(0o600)
            return adapter.inspect(root, token, metadata_probe=lambda _: metadata)
        out = inspect(receipt)
        record('matching-staged-unpublished', check(out) and out['assessment'] == 'unpublished-staged-evidence'
               and out['receiptMetadataConsistent'] is True)
        out = inspect(receipt, final_only=True)
        record('matching-final-unverified', check(out) and out['assessment'] == 'final-only-unverified'
               and out['receiptMetadataConsistent'] is False)
        p = receipt['payload']
        inv = 'f' * 32 if p['invocationId'] != 'f' * 32 else 'e' * 32
        for name, candidate in (
            ('wrong-invocation', binding.worker_receipt(token, inv, p['workerPid'])),
            ('wrong-pid', binding.worker_receipt(token, p['invocationId'], p['workerPid'] + 1)),
            ('wrong-token', binding.worker_receipt('b' * 24 if token != 'b' * 24 else 'c' * 24,
                                                 p['invocationId'], p['workerPid']))):
            out = inspect(candidate)
            record(name, check(out) and out['receiptMetadataConsistent'] is False
                   and out['assessment'] in ('conflicting-evidence', 'incomplete-staged-evidence'))
        out = inspect(receipt)
        staged.write_bytes(binding.canonical(binding.worker_receipt(token, inv, p['workerPid'])))
        staged.chmod(0o600)
        # A second staged file with a contradictory identity.
        second = root / '.receipt-staged-second'
        second.write_bytes(binding.canonical(receipt))
        second.chmod(0o600)
        mixed = adapter.inspect(root, token, metadata_probe=lambda _: meta)
        record('mixed-staged-identities', check(mixed) and mixed['receiptMetadataConsistent'] is False
               and mixed['assessment'] == 'conflicting-evidence')
        second.unlink()
        out = inspect(receipt)
        staged.write_bytes(binding.canonical(receipt)[:16])
        out = adapter.inspect(root, token, metadata_probe=lambda _: meta)
        record('partial-staged', check(out) and out['receiptMetadataConsistent'] is False
               and out['assessment'] == 'incomplete-staged-evidence')
        out = inspect(receipt, metadata={**meta, 'systemdExecutionMetadataConsistent': False})
        record('unverified-metadata', check(out) and out['assessment'] == 'worker-state-uncertain')
        out = inspect(receipt, metadata={**meta, 'executionAuthorized': True})
        record('unsafe-metadata', check(out) and out['assessment'] == 'worker-state-uncertain')
        out = inspect(None)
        record('missing-evidence', check(out) and out['assessment'] == 'no-receipt-evidence')
    return results


def summary(results, runtime):
    ok = all(item['passed'] for item in results) and len(results) == (11 if runtime else 10)
    return {'version': '0.5.79.9.4', 'mode': 'isolated-real-historical-identity' if runtime else 'offline-historical-identity-fixtures',
            'selftest': 'passed' if ok else 'failed', 'totalScenarios': len(results), 'results': results,
            'systemdIntegration': runtime, 'productionModified': False, 'automaticCleanup': False,
            'receiptMetadataConsistent': False, **{k: False for k in SAFE}, 'commandsExecuted': 0,
            'limitations': ['A matching receipt is not independent worker authentication',
                            'Final-only evidence remains unverified by orphan classifier',
                            'No atomic cross-store snapshot, power-loss durability, or physical rollback proof']}


def selftest():
    token = 'a' * 24
    binding = load('display-worker-evidence-binding-v05791.py')
    receipt = binding.worker_receipt(token, '1' * 32, 12345)
    meta = dict(transactionId=token, assessment='systemd-execution-metadata-consistent-only',
                systemdExecutionMetadataConsistent=True, invocationId='1' * 32, execMainPid=12345,
                commandsExecuted=0, **{k: False for k in SAFE})
    return summary(scenarios(token, meta, receipt), False)


def run():
    historical = load('display-historical-worker-attestation-v05789.py')
    reconciliation = load('display-systemd-reconciliation.py')
    binding = load('display-worker-evidence-binding-v05791.py')
    token = secrets.token_hex(12)
    unit = reconciliation.unit_name(token)
    started = False
    with tempfile.TemporaryDirectory(prefix='serpantinum-v057994-real-') as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        try:
            command = ['systemd-run', '--user', '--unit=' + unit, '--property=Type=oneshot',
                       '--property=RemainAfterExit=yes', '--description=' + reconciliation.MARKER + token,
                       sys.executable, str(HERE / 'display-worker-receipt-runtime-v05792.py'),
                       'worker', '--token', token, '--root', str(root)]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=12)
            if proc.returncode:
                raise RuntimeError('systemd-run failed: ' + proc.stderr[:200])
            started = True
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                meta = historical.probe(token)
                if meta.get('systemdExecutionMetadataConsistent') is True:
                    break
                if meta.get('assessment') == 'rejected':
                    raise RuntimeError('systemd metadata rejected')
                time.sleep(0.1)
            else:
                raise RuntimeError('Retained systemd metadata not available')
            path = root / 'receipt.json'
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise RuntimeError('Missing or unsafe receipt')
            receipt = json.loads(path.read_text(encoding='utf-8'))
            linked = binding.assess(token, meta, receipt)
            if linked.get('receiptMetadataConsistent') is not True:
                raise RuntimeError('Real receipt and historical metadata inconsistent')
            return summary(scenarios(token, meta, receipt, actual_root=root), True)
        finally:
            if started:
                subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, text=True, timeout=8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    try:
        out = selftest() if args.command == 'selftest' else run()
    except Exception as exc:
        out = {'version': '0.5.79.9.4', 'selftest': 'failed', 'error': str(exc),
               'executionAuthorized': False, 'physicalExecutionEnabled': False,
               'physicalRollbackVerified': False, 'commandsExecuted': 0}
    print(json.dumps(out, indent=2))
    return 0 if out.get('selftest') == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())
