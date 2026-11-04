#!/usr/bin/env python3
"""Offline-only supervised rollback bridge. Never mutates Hyprland.

Verifies a snapshot-bound mock transport BEFORE allowing the existing unified
IPC recovery engine to close the simulated transaction. Fail closed otherwise.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    s = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

rehearsal = load('display-physical-rollback-rehearsal-v0600.py', 'supervised_rehearsal')
recovery = load('display-unified-ipc-recovery-v0600.py', 'supervised_recovery')
ipc = recovery.ipc

def outcome(decision, detail=None):
    return dict(version='0.6.0', mode='rollback-supervision-offline', decision=decision,
                detail=detail, physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

def execute(root, capture, monitors, *, now_ns=None):
    root = ipc.root_check(Path(root))
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_ns is None else now_ns
    with ipc.locked(root) as p:
        r = ipc.read(p, 'transaction.json')
        s = ipc.read(p, 'physical-snapshot.json')
        if r.get('state') not in ('APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN', 'ROLLED_BACK'):
            return outcome('BLOCK_STATE')
        if r.get('state') == 'APPLIED' and now < r.get('deadline_boottime_ns', 0):
            return outcome('WAITING')
        if r.get('state') == 'UNCERTAIN' and r.get('phase') != 'ROLLBACK_INTENT':
            return outcome('BLOCK_AMBIGUOUS_APPLY')
        if r.get('state') == 'ROLLED_BACK':
            return outcome('BLOCK_ALREADY_TERMINAL_REQUIRES_PHYSICAL_VERIFICATION')
        if r.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return outcome('BLOCK_NEW_BOOT')
        if s.get('transaction_id') != r.get('id') or s.get('boot_id') != r.get('boot_id'):
            return outcome('BLOCK_IDENTITY')
    # Rehearsal uses same lock and revalidates snapshot/identity.
    transport = rehearsal.MockTransport(monitors)
    try:
        rehearsed = rehearsal.execute(root, capture, transport, require_due=True)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return outcome('BLOCK_REHEARSAL', type(exc).__name__)
    if rehearsed.get('decision') != 'MOCK_ROLLBACK_REHEARSED' or rehearsed.get('after') != 'ORIGINAL':
        return outcome('BLOCK_MOCK_VERIFICATION')
    # A rehearsal is NOT proof of a physical effect. Only close the simulated
    # IPC transaction after the mock has been verified.
    result = recovery.execute(root, now_ns=now)
    if result['decision'] not in ('SIMULATED_ROLLBACK_VERIFIED', 'SIMULATED_RECONCILIATION_VERIFIED'):
        return outcome('BLOCK_RECOVERY', result['decision'])
    with ipc.locked(root) as p:
        final = ipc.read(p, 'transaction.json')
        observed = ipc.read(p, 'simulated-ipc.json')
        if final.get('state') != 'ROLLED_BACK' or final.get('phase') != 'COMPLETED' or observed != {'connector':final['target'], 'mode':ipc.BASE}:
            return outcome('BLOCK_FINAL_VERIFICATION')
    return outcome('MOCK_AND_IPC_RECOVERY_VERIFIED', result['decision'])

def main():
    p = argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('--capture', required=True)
    p.add_argument('--mock-monitors', required=True)
    args = p.parse_args()
    try:
        result = execute(args.root, json.loads(Path(args.capture).read_text()), json.loads(Path(args.mock_monitors).read_text()))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        result = outcome('BLOCK_AND_INVESTIGATE', type(exc).__name__)
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] == 'MOCK_AND_IPC_RECOVERY_VERIFIED' else 2

if __name__ == '__main__':
    raise SystemExit(main())
