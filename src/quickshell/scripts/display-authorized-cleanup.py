#!/usr/bin/env python3
"""Serpantinum v0.5.16: authorized cleanup of completed SIMULATION evidence.

Does not change displays or certify physical rollback. Use this command instead
of legacy bundle clear for future completed watchdog transactions.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
TERMINAL = {'snapshot-equivalent', 'snapshot-different', 'simulated-confirmed'}


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def receipt_path(folder):
    return folder / 'cleanup-receipt.json'


def read_receipt(folder, bundle):
    path = receipt_path(folder)
    if path.is_symlink():
        raise ValueError('Unsafe receipt symlink')
    if not path.exists():
        return None
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('Unsafe receipt permissions')
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict) or receipt.get('version') != '0.5.16':
        raise ValueError('Invalid receipt')
    if receipt.get('phase') not in ('cleanup-prepared', 'completed-cleaned'):
        raise ValueError('Invalid receipt phase')
    if not isinstance(receipt.get('transactionId'), str) or not TOKEN.fullmatch(receipt['transactionId']):
        raise ValueError('Invalid receipt identity')
    if not isinstance(receipt.get('stateHash'), str) or not re.fullmatch('[0-9a-f]{64}', receipt['stateHash']):
        raise ValueError('Invalid state digest')
    if not isinstance(receipt.get('bundleHash'), str) or not re.fullmatch('[0-9a-f]{64}', receipt['bundleHash']):
        raise ValueError('Invalid bundle digest')
    return receipt


def state_hash(state, bundle):
    return hashlib.sha256(bundle.canonical(state)).hexdigest()


def unit_inactive(state, root=None):
    if root is not None:
        return True  # test-only injection; never used for actual cleanup
    unit = state.get('unit')
    token = state.get('transactionId')
    if unit != 'serpantinum-display-v0514-' + token + '.service':
        raise ValueError('Unexpected unit identity')
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                            capture_output=True, text=True, timeout=6)
    if result.returncode != 0 or 'ActiveState=inactive' not in result.stdout.splitlines():
        raise ValueError('Supervisor not verifiably inactive')
    return True


def cleanup(root=None):
    bundle = load('display-recovery-bundle.py', 'bundle_0516')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_0516')
    folder = bundle.runtime_dir(root)
    with bundle.locked(folder):
        state = watchdog.read_state(folder)
        receipt = read_receipt(folder, bundle)
        payload = bundle.read_bundle(folder)
        if state.get('pending') is not False or state.get('phase') not in TERMINAL:
            raise ValueError('Only completed successful simulation states may be cleaned')
        if state.get('phase') in ('snapshot-equivalent', 'snapshot-different'):
            sim = state.get('rollbackSimulation')
            if (state.get('phase') != 'snapshot-equivalent' or
                    state.get('equivalentObservableState') is not True or
                    state.get('rollbackSimulationPhase') != 'rollback-simulated-verified' or
                    state.get('simulatedRollbackVerified') is not True or
                    state.get('systemdRollbackIntegration') is not True or
                    not isinstance(sim, dict) or
                    sim.get('phase') != 'rollback-verified' or
                    sim.get('simulatedRollbackVerified') is not True):
                raise ValueError('Rollback simulation not verified: preserve recovery evidence')
        if state.get('phase') in ('snapshot-equivalent', 'snapshot-different'):
            if state.get('systemdRehearsalIntegration') is True:
                rehearsal = state.get('rollbackRehearsal')
                if (state.get('rollbackRehearsalPhase') != 'rollback-rehearsal-verified' or
                        state.get('rollbackRehearsalVerified') is not True or
                        not isinstance(rehearsal, dict) or
                        rehearsal.get('phase') != 'rollback-rehearsal-verified' or
                        rehearsal.get('simulatedRollbackVerified') is not True or
                        rehearsal.get('physicalRollbackVerified') is not False or
                        rehearsal.get('commandsExecuted') != 0):
                    raise ValueError('Rehearsal not verified: preserve evidence')
            elif 'rollbackRehearsalPhase' in state:
                raise ValueError('Inconsistent rehearsal evidence')
        token = state.get('transactionId')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction ID')
        unit_inactive(state, root)
        digest = state_hash(state, bundle)
        if receipt:
            if receipt['transactionId'] != token or receipt['stateHash'] != digest:
                raise ValueError('Receipt and watchdog mismatch')
            if receipt['phase'] == 'completed-cleaned':
                if payload is not None:
                    raise ValueError('Committed receipt but bundle remains')
                return result(receipt)
            if payload is None:
                raise ValueError('Interrupted cleanup: bundle absent before commit')
            if hashlib.sha256(bundle.canonical(payload)).hexdigest() != receipt['bundleHash']:
                raise ValueError('Prepared receipt bundle mismatch')
        else:
            if payload is None or payload.get('id') != token:
                raise ValueError('Missing or mismatched bundle: cannot authorize cleanup retroactively')
            receipt = {'version': '0.5.16', 'phase': 'cleanup-prepared',
                       'transactionId': token, 'stateHash': digest,
                       'bundleHash': hashlib.sha256(bundle.canonical(payload)).hexdigest(),
                       'physicalRollbackVerified': False, 'createdAt': time.time()}
            bundle.atomic_write(receipt_path(folder), receipt)
        # Crash between unlink and committed receipt is deliberately fail-closed.
        (folder / 'bundle.json').unlink()
        receipt['phase'] = 'completed-cleaned'
        bundle.atomic_write(receipt_path(folder), receipt)
        return result(receipt)


def result(receipt):
    return {'version': '0.5.16', 'phase': receipt['phase'],
            'transactionId': receipt['transactionId'], 'cleanupEvidenceVerified': True,
            'physicalRollbackVerified': False, 'physicalOutputsChanged': False}


def selftest():
    bundle = load('display-recovery-bundle.py', 'bundle_test_0516')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_test_0516')
    sample = {'outputs': [{'name': 'eDP-2', 'width': 1920}]}
    approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
    with tempfile.TemporaryDirectory() as root:
        bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)
        folder = bundle.runtime_dir(root)
        with bundle.locked(folder):
            token = bundle.read_bundle(folder)['id']
            state = {'transactionId': token, 'phase': 'snapshot-equivalent',
                     'pending': False, 'unit': 'serpantinum-display-v0514-' + token + '.service',
                     'equivalentObservableState': True, 'rollbackSimulationPhase': 'rollback-simulated-verified',
                     'simulatedRollbackVerified': True, 'systemdRollbackIntegration': True,
                     'rollbackSimulation': {'phase': 'rollback-verified', 'simulatedRollbackVerified': True}}
            bundle.atomic_write(watchdog.state_path(folder), state)
        assert cleanup(root)['phase'] == 'completed-cleaned'
        assert cleanup(root)['phase'] == 'completed-cleaned'
        assert bundle.read_bundle(folder) is None
        with bundle.locked(folder):
            receipt = read_receipt(folder, bundle)
            assert receipt['transactionId'] == token
            receipt['stateHash'] = '0' * 64
            bundle.atomic_write(receipt_path(folder), receipt)
        try:
            cleanup(root)
        except ValueError:
            pass
        else:
            raise AssertionError('Tampered receipt accepted')
    with tempfile.TemporaryDirectory() as root:
        bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)
        folder = bundle.runtime_dir(root)
        with bundle.locked(folder):
            token = bundle.read_bundle(folder)['id']
            bundle.atomic_write(watchdog.state_path(folder), {
                'transactionId': token, 'phase': 'simulated-pending', 'pending': True})
        try:
            cleanup(root)
        except ValueError:
            pass
        else:
            raise AssertionError('Pending transaction cleaned')
    for bad in ('rollback-simulated-failed', 'rollback-simulated-incomplete', None):
        with tempfile.TemporaryDirectory() as root:
            bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)
            folder = bundle.runtime_dir(root)
            with bundle.locked(folder):
                token = bundle.read_bundle(folder)['id']
                state = {'transactionId': token, 'phase': 'snapshot-equivalent', 'pending': False,
                         'unit': 'serpantinum-display-v0514-' + token + '.service',
                         'equivalentObservableState': True, 'rollbackSimulationPhase': bad,
                         'simulatedRollbackVerified': True, 'systemdRollbackIntegration': True,
                         'rollbackSimulation': {'phase': 'rollback-verified', 'simulatedRollbackVerified': True}}
                bundle.atomic_write(watchdog.state_path(folder), state)
            try:
                cleanup(root)
            except ValueError:
                pass
            else:
                raise AssertionError('Unsafe simulation cleanup accepted')
            assert bundle.read_bundle(folder) is not None
    return {'selftest': 'passed', 'authorizedCleanup': True, 'duplicateIdempotent': True,
            'tamperingRejected': True, 'pendingRejected': True, 'physicalOutputsChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['cleanup', 'selftest'])
    args = parser.parse_args()
    try:
        data = selftest() if args.command == 'selftest' else cleanup()
        print(json.dumps(data, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
