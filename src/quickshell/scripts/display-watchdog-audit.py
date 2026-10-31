#!/usr/bin/env python3
"""Serpantinum v0.5.15: READ-ONLY orphaned watchdog and integrity audit.

Never starts/stops services, changes displays, or deletes recovery bundles.
A healthy snapshot comparison is NOT proof of physical rollback.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError('Missing dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(state, bundle_valid, unit_state, cleanup_verified=False):
    """Pure fail-closed state assessment; no side effects."""
    phase = state.get('phase', 'idle')
    pending = state.get('pending') is True
    if state.get('pending') not in (True, False, None):
        return 'invalid-state', False
    if pending:
        if not bundle_valid:
            return 'integrity-failed', False
        if not state.get('watchdogReady'):
            return 'not-ready', False
        if unit_state != 'active':
            return 'orphaned-pending', False
        return 'supervised-pending', True
    # v0.5.28 rehearsal is a second independent fail-closed gate.
    if phase in ('snapshot-equivalent', 'snapshot-different') and state.get('systemdRehearsalIntegration') is True:
        rp = state.get('rollbackRehearsalPhase')
        if rp in ('rollback-rehearsal-failed', 'rollback-rehearsal-incomplete'):
            return rp, False
        rehearsal = state.get('rollbackRehearsal')
        if (rp != 'rollback-rehearsal-verified' or
                state.get('rollbackRehearsalVerified') is not True or
                not isinstance(rehearsal, dict) or
                rehearsal.get('phase') != 'rollback-rehearsal-verified' or
                rehearsal.get('simulatedRollbackVerified') is not True or
                rehearsal.get('physicalRollbackVerified') is not False or
                rehearsal.get('commandsExecuted') != 0):
            return 'rollback-rehearsal-unknown', False
    elif phase in ('snapshot-equivalent', 'snapshot-different') and 'rollbackRehearsalPhase' in state:
        return 'rollback-rehearsal-unknown', False
    # A completed snapshot comparison must never hide a failed simulation.
    if phase in ('snapshot-equivalent', 'snapshot-different'):
        simulation = state.get('rollbackSimulationPhase')
        if simulation == 'rollback-simulated-failed':
            return 'rollback-simulation-failed', False
        if simulation == 'rollback-simulated-incomplete':
            return 'rollback-simulation-incomplete', False
        if (simulation != 'rollback-simulated-verified' or
                state.get('simulatedRollbackVerified') is not True or
                state.get('systemdRollbackIntegration') is not True or
                not isinstance(state.get('rollbackSimulation'), dict) or
                state['rollbackSimulation'].get('simulatedRollbackVerified') is not True or
                state['rollbackSimulation'].get('phase') != 'rollback-verified'):
            return 'rollback-simulation-unknown', False
        if phase != 'snapshot-equivalent' or state.get('equivalentObservableState') is not True:
            return 'rollback-simulation-inconsistent', False
    if phase in ('idle', 'cleared'):
        return 'idle', True
    if phase in ('snapshot-equivalent', 'snapshot-different', 'simulated-confirmed'):
        if cleanup_verified and not bundle_valid and unit_state == 'inactive':
            return 'completed-cleaned', True
        if bundle_valid:
            return "completed-observable", True
        return "completed-without-bundle", False
    if phase in ('startup-failed', 'verification-failed'):
        return 'recorded-failure', False
    return 'unrecognized-terminal-state', False


def query_unit(unit):
    if not isinstance(unit, str) or not unit.startswith('serpantinum-display-v0514-') or not unit.endswith('.service'):
        raise ValueError('Unexpected systemd unit')
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                            capture_output=True, text=True, timeout=6)
    if result.returncode != 0:
        return 'unknown'
    for line in result.stdout.splitlines():
        if line.startswith('ActiveState='):
            return line.partition('=')[2]
    return 'unknown'


def audit():
    bundle = load('bundle_v0515', 'display-recovery-bundle.py')
    watchdog = load('watchdog_v0515', 'display-sealed-watchdog.py')
    cleanup = load('cleanup_v0516', 'display-authorized-cleanup.py')
    folder = bundle.runtime_dir()
    with bundle.locked(folder):
        state = watchdog.read_state(folder)
        try:
            payload = bundle.read_bundle(folder)
            bundle_valid = (payload is not None and payload.get('id') == state.get('transactionId'))
            bundle_error = None
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            bundle_valid = False
            bundle_error = str(exc)
        receipt = cleanup.read_receipt(folder, bundle)
        cleanup_verified = (receipt is not None and receipt['phase'] == 'completed-cleaned'
                            and payload is None and receipt['transactionId'] == state.get('transactionId')
                            and receipt['stateHash'] == cleanup.state_hash(state, bundle))
    unit = state.get('unit')
    unit_state = query_unit(unit) if unit else 'absent'
    assessment, healthy = classify(state, bundle_valid, unit_state, cleanup_verified)
    return {'version': '0.5.15', 'mode': 'read-only-audit', 'phase': state.get('phase', 'idle'),
            'assessment': assessment, 'healthy': healthy, 'pending': state.get('pending') is True,
            'transactionId': state.get('transactionId'), 'bundleIntegrityVerified': bundle_valid,
            'bundleError': bundle_error, 'cleanupEvidenceVerified': cleanup_verified, 'systemdActiveState': unit_state,
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0, 'persistentConfigChanged': False,
            'warning': 'Audit only. An orphaned transaction is NOT automatically recovered.'}


def selftest():
    pending = {'phase': 'simulated-pending', 'pending': True, 'watchdogReady': True}
    assert classify(pending, True, 'active') == ('supervised-pending', True)
    assert classify(pending, True, 'inactive') == ('orphaned-pending', False)
    assert classify(pending, False, 'active') == ('integrity-failed', False)
    verified = {'phase': 'snapshot-equivalent', 'pending': False, 'equivalentObservableState': True,
                'rollbackSimulationPhase': 'rollback-simulated-verified', 'simulatedRollbackVerified': True,
                'systemdRollbackIntegration': True,
                'rollbackSimulation': {'phase': 'rollback-verified', 'simulatedRollbackVerified': True}}
    assert classify(verified, True, 'inactive') == ('completed-observable', True)
    assert classify({'phase': 'verification-failed', 'pending': False}, True, 'inactive') == ('recorded-failure', False)
    assert classify({'phase': 'starting', 'pending': True, 'watchdogReady': False}, True, 'active') == ('not-ready', False)
    assert classify(verified, False, "inactive") == ("completed-without-bundle", False)
    assert classify({"phase": "simulated-pending", "pending": True, "watchdogReady": True}, False, "inactive") == ("integrity-failed", False)
    assert classify(verified, False, 'inactive', True) == ('completed-cleaned', True)
    assert classify(pending, False, 'inactive', True) == ('integrity-failed', False)
    for label, phase in [('rollback-simulation-failed', 'rollback-simulated-failed'),
                         ('rollback-simulation-incomplete', 'rollback-simulated-incomplete')]:
        assert classify(dict(verified, rollbackSimulationPhase=phase), True, 'inactive') == (label, False)
        assert classify(dict(verified, rollbackSimulationPhase=phase), False, 'inactive', True) == (label, False)
    assert classify(dict(verified, rollbackSimulationPhase=None), True, 'inactive') == ('rollback-simulation-unknown', False)
    assert classify(dict(verified, rollbackSimulation={}), True, 'inactive') == ('rollback-simulation-unknown', False)
    assert classify(dict(verified, phase='snapshot-different'), True, 'inactive') == ('rollback-simulation-inconsistent', False)
    assert classify({'phase': 'simulated-confirmed', 'pending': False}, True, 'inactive') == ('completed-observable', True)
    return {'selftest': 'passed', 'scenarios': 17, 'commandsExecuted': 0,
            'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'selftest'])
    args = parser.parse_args()
    result = selftest() if args.command == 'selftest' else audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command == 'selftest' or result['healthy'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'healthy': False, 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
