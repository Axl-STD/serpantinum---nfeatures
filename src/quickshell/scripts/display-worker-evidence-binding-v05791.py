#!/usr/bin/env python3
"""v0.5.79.1: offline characterization of worker-generated evidence binding.

No systemd units, compositor commands, physical execution, or authorization.
A matching receipt is consistency evidence, not trusted historical identity.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

TOKEN = re.compile(r'[0-9a-f]{24}\Z')
INVOCATION = re.compile(r'[0-9a-f]{32}\Z')
SAFE_FALSE = ('historicalIdentityVerified', 'workerIdentityVerified',
              'operationCompletionVerified', 'finalizationAuthorized',
              'executionAuthorized', 'physicalExecutionEnabled',
              'physicalRollbackVerified')


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(payload):
    return hashlib.sha256(canonical(payload)).hexdigest()


def worker_receipt(token, invocation, pid, outcome='test-completed'):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('invalid token')
    if not isinstance(invocation, str) or not INVOCATION.fullmatch(invocation) or int(invocation, 16) == 0:
        raise ValueError('invalid invocation')
    if type(pid) is not int or pid <= 0:
        raise ValueError('invalid pid')
    if outcome != 'test-completed':
        raise ValueError('invalid outcome')
    payload = dict(version='0.5.79.1', transactionId=token, invocationId=invocation,
                   workerPid=pid, outcome=outcome, physicalExecutionEnabled=False,
                   commandsExecuted=0)
    return {'payload': payload, 'sha256': digest(payload)}


def assess(token, metadata, receipt):
    result = {'version': '0.5.79.1', 'mode': 'offline-worker-receipt-binding',
              'assessment': 'rejected', 'receiptMetadataConsistent': False,
              **{k: False for k in SAFE_FALSE}, 'commandsExecuted': 0}
    try:
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('invalid transaction')
        if not isinstance(metadata, dict) or metadata.get('systemdExecutionMetadataConsistent') is not True:
            raise ValueError('systemd metadata unverified')
        if metadata.get('transactionId') != token:
            raise ValueError('metadata token mismatch')
        if not isinstance(receipt, dict) or set(receipt) != {'payload', 'sha256'}:
            raise ValueError('invalid receipt envelope')
        payload = receipt['payload']
        if not isinstance(payload, dict) or set(payload) != {'version', 'transactionId', 'invocationId', 'workerPid', 'outcome', 'physicalExecutionEnabled', 'commandsExecuted'}:
            raise ValueError('invalid receipt payload')
        if receipt['sha256'] != digest(payload):
            raise ValueError('receipt hash mismatch')
        expected = worker_receipt(token, metadata.get('invocationId'), metadata.get('execMainPid'))
        if payload != expected['payload']:
            raise ValueError('worker receipt mismatch')
        if any(metadata.get(k) is not False for k in ('historicalIdentityVerified', 'executionAuthorized', 'physicalRollbackVerified')):
            raise ValueError('unsafe metadata flags')
        result['assessment'] = 'receipt-and-systemd-metadata-consistent-only'
        result['receiptMetadataConsistent'] = True
    except (ValueError, TypeError, KeyError) as exc:
        result['reason'] = str(exc)
    return result


def selftest():
    token = 'a' * 24
    invocation = '1' * 32
    pid = 12345
    metadata = dict(systemdExecutionMetadataConsistent=True, transactionId=token,
                    invocationId=invocation, execMainPid=pid,
                    historicalIdentityVerified=False, executionAuthorized=False,
                    physicalRollbackVerified=False)
    receipt = worker_receipt(token, invocation, pid)
    tests = [
        ('consistent', metadata, receipt, True),
        ('wrong-token', metadata, worker_receipt('b'*24, invocation, pid), False),
        ('wrong-invocation', metadata, worker_receipt(token, '2'*32, pid), False),
        ('wrong-pid', metadata, worker_receipt(token, invocation, pid+1), False),
        ('wrong-metadata-token', {**metadata, 'transactionId': 'b'*24}, receipt, False),
        ('unverified-metadata', {**metadata, 'systemdExecutionMetadataConsistent': False}, receipt, False),
        ('unsafe-metadata', {**metadata, 'executionAuthorized': True}, receipt, False),
        ('missing-receipt', metadata, None, False),
        ('tampered-hash', metadata, {**receipt, 'sha256': '0'*64}, False),
        ('tampered-payload', metadata, {**receipt, 'payload': {**receipt['payload'], 'workerPid': 1}}, False),
        ('duplicate-envelope-field', metadata, {**receipt, 'extra': 1}, False),
        ('missing-payload-field', metadata, {'payload': {k:v for k,v in receipt['payload'].items() if k != 'commandsExecuted'}, 'sha256': receipt['sha256']}, False),
        ('unsafe-receipt', metadata, {'payload': {**receipt['payload'], 'physicalExecutionEnabled': True}, 'sha256': digest({**receipt['payload'], 'physicalExecutionEnabled': True})}, False),
    ]
    results = []
    for name, meta, rec, expected in tests:
        out = assess(token, meta, rec)
        assert out['receiptMetadataConsistent'] is expected, name
        assert out['commandsExecuted'] == 0 and all(out[k] is False for k in SAFE_FALSE), name
        results.append({'scenario': name, 'passed': True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05791-') as root:
        folder = Path(root)
        os.chmod(folder, 0o700)
        target = folder / 'receipt.json'
        with target.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream)
        os.chmod(target, 0o600)
        assert assess(token, metadata, json.loads(target.read_text()))['receiptMetadataConsistent']
        results.append({'scenario': 'private-temporary-receipt-roundtrip', 'passed': True})
    return dict(version='0.5.79.1', mode='offline-worker-receipt-binding',
                selftest='passed', totalScenarios=len(results), results=results,
                productionModified=False, systemdIntegration=False,
                receiptMetadataConsistent=True, historicalIdentityVerified=False,
                workerIdentityVerified=False, operationCompletionVerified=False,
                finalizationAuthorized=False, executionAuthorized=False,
                physicalExecutionEnabled=False, physicalRollbackVerified=False,
                commandsExecuted=0,
                limitations=['Offline fixtures only; no real worker receipt or systemd unit executed',
                             'SHA256 is not authentication against a local writer able to replace receipt and hash',
                             'No crash-durability or independent historical identity proof'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest'])
    p.parse_args()
    print(json.dumps(selftest(), indent=2))


if __name__ == '__main__':
    main()
