#!/usr/bin/env python3
"""v0.5.79.9.3: offline same-read historical receipt identity regression."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
SAFE_FALSE = ('historicalIdentityVerified', 'workerIdentityVerified', 'operationCompletionVerified',
              'finalizationAuthorized', 'executionAuthorized', 'physicalExecutionEnabled',
              'physicalRollbackVerified')

def load(filename):
    spec = importlib.util.spec_from_file_location('test_' + filename.replace('-', '_'), HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def selftest():
    adapter = load('display-worker-historical-state-binding-v057991.py')
    binding = load('display-worker-evidence-binding-v05791.py')
    reconcile = load('display-systemd-reconciliation.py')
    token = 'a' * 24
    meta = dict(Id=reconcile.unit_name(token), Description=reconcile.MARKER + token,
                LoadState='loaded', ActiveState='active', SubState='exited', MainPID='0',
                ControlPID='0', Result='success', Type='oneshot', RemainAfterExit='yes',
                InvocationID='1' * 32, ExecMainPID='12345', ExecMainCode='1',
                ExecMainStatus='0', NRestarts='0',
                ExecMainStartTimestamp='Sat 2026-10-31 12:58:10 CST',
                ExecMainExitTimestamp='Sat 2026-10-31 12:58:10 CST')
    good = binding.canonical(binding.worker_receipt(token, '1'*32, 12345))
    other_inv = binding.canonical(binding.worker_receipt(token, '2'*32, 12345))
    other_pid = binding.canonical(binding.worker_receipt(token, '1'*32, 12346))
    other_token = binding.canonical(binding.worker_receipt('b'*24, '1'*32, 12345))
    cases = [
        ('matching-staged', {'.receipt-staged-a':good}, meta, 'unpublished-staged-evidence', True),
        ('matching-final', {'receipt.json':good}, meta, 'final-only-unverified', False),
        ('matching-both', {'.receipt-staged-a':good,'receipt.json':good}, meta, 'published-with-staged-remnant', True),
        ('wrong-invocation', {'.receipt-staged-a':other_inv}, meta, 'conflicting-evidence', False),
        ('wrong-pid', {'.receipt-staged-a':other_pid}, meta, 'conflicting-evidence', False),
        ('wrong-token', {'.receipt-staged-a':other_token}, meta, 'incomplete-staged-evidence', False),
        ('wrong-final-invocation', {'.receipt-staged-a':good,'receipt.json':other_inv}, meta, 'conflicting-evidence', False),
        ('mixed-staged-identities', {'.receipt-staged-a':good,'.receipt-staged-b':other_pid}, meta, 'conflicting-evidence', False),
        ('partial-staged', {'.receipt-staged-a':good[:9]}, meta, 'incomplete-staged-evidence', False),
        ('running-worker', {'.receipt-staged-a':good}, {**meta,'SubState':'running'}, 'worker-state-uncertain', False),
        ('wrong-systemd-invocation', {'.receipt-staged-a':good}, {**meta,'InvocationID':'2'*32}, 'conflicting-evidence', False),
        ('wrong-systemd-pid', {'.receipt-staged-a':good}, {**meta,'ExecMainPID':'12346'}, 'conflicting-evidence', False),
    ]
    results=[]
    with tempfile.TemporaryDirectory(prefix='serpantinum-v057993-') as tmp:
        for name, files, props, expected, consistent in cases:
            folder=Path(tmp)/name
            folder.mkdir(mode=0o700)
            for filename, data in files.items():
                target=folder/filename
                target.write_bytes(data)
                target.chmod(0o600)
            before={filename:(folder/filename).read_bytes() for filename in files}
            out=adapter.inspect(folder, token, props)
            assert out['assessment']==expected, (name,out)
            assert out['receiptMetadataConsistent'] is consistent, (name,out)
            assert all(out[k] is False for k in SAFE_FALSE), (name,out)
            assert out['commandsExecuted']==0 and out['automaticCleanup'] is False
            assert before=={filename:(folder/filename).read_bytes() for filename in files}
            results.append({'scenario':name,'passed':True})
    return dict(version='0.5.79.9.3',mode='offline-historical-receipt-identity',
                selftest='passed',totalScenarios=len(results),results=results,
                productionModified=False,systemdIntegration=False,automaticCleanup=False,
                historicalIdentityVerified=False,workerIdentityVerified=False,
                operationCompletionVerified=False,finalizationAuthorized=False,
                executionAuthorized=False,physicalExecutionEnabled=False,
                physicalRollbackVerified=False,commandsExecuted=0,
                limitations=['No real systemd unit executed',
                             'Matching metadata is not independent worker authentication',
                             'No atomic cross-store snapshot or physical rollback proof'])

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))
