#!/usr/bin/env python3
"""Physical recovery runtime boundary, v0.6.0.

Read-only against live Hyprland. The injected transport path exercises an
actual recovery request and independent readback in memory, but never updates
transaction.json and NEVER authorizes a physical command. Fail closed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ipc = load('display-ipc-transaction-lab-v0600.py', 'physical_runtime_ipc')
snapshot = load('display-physical-snapshot-binding-v0600.py', 'physical_runtime_snapshot')
obligation = load('display-watchdog-obligation-v0600.py', 'physical_runtime_obligation')
tty = load('display-tty3-transaction-triage-v0600.py', 'physical_runtime_tty')

class Denied(RuntimeError):
    pass

def output(decision, **extra):
    return dict(version='0.6.0', mode='physical-recovery-runtime-readonly', decision=decision,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False, **extra)

def evidence(root, instance):
    with ipc.locked(root) as p:
        r = ipc.read(p, 'transaction.json')
        s = ipc.read(p, 'physical-snapshot.json')
        if not isinstance(r, dict) or not isinstance(s, dict):
            raise Denied('invalid records')
        if s.get('transaction_id') != r.get('id') or s.get('boot_id') != r.get('boot_id'):
            raise Denied('transaction mismatch')
        if s.get('target') != r.get('target') or s.get('topology') != r.get('topology'):
            raise Denied('topology mismatch')
        if type(r.get('generation')) is not int or type(s.get('generation_at_binding')) is not int or r['generation'] < s['generation_at_binding']:
            raise Denied('generation mismatch')
        if snapshot.digest(s.get('snapshot')) != s.get('snapshot_sha256'):
            raise Denied('snapshot digest mismatch')
        if snapshot.validate(s['snapshot'], r['target']) != s['snapshot']:
            raise Denied('invalid baseline')
        ident = s.get('identity')
        if not isinstance(ident, dict) or ident.get('instance') != instance:
            raise Denied('compositor changed')
        obligation._receipt(p, r)  # simulated obligation, NOT physical watchdog
        return r, s

def compare(baseline, observed, target):
    if not isinstance(observed, list) or len(observed) != len(baseline):
        return 'BLOCK_TOPOLOGY_CHANGED'
    by_name = {}
    for m in observed:
        if not isinstance(m, dict) or not isinstance(m.get('name'), str) or m['name'] in by_name:
            return 'BLOCK_TOPOLOGY_CHANGED'
        by_name[m['name']] = m
    if set(by_name) != {m['name'] for m in baseline}:
        return 'BLOCK_TOPOLOGY_CHANGED'
    candidate = False
    for b in baseline:
        m = by_name[b['name']]
        for field in snapshot.FIELDS:
            if field not in m:
                return 'BLOCK_INCOMPLETE_OBSERVATION'
            if field == 'refreshRate' and b['name'] == target:
                hz = m[field]
                if type(hz) not in (float, int):
                    return 'BLOCK_INVALID_RATE'
                if abs(hz - b[field]) <= 0.12:
                    continue
                if 143.95 <= hz <= 144.15:
                    candidate = True
                    continue
                return 'BLOCK_UNEXPECTED_MODE'
            if m[field] != b[field]:
                return 'BLOCK_MONITOR_DRIFT'
    return 'CANDIDATE' if candidate else 'ORIGINAL'

def inspect(root, instance, observer=None):
    r, s = evidence(root, instance)
    monitors = tty.capture(instance) if observer is None else observer()
    mode = compare(s['snapshot'], monitors, r['target'])
    if mode.startswith('BLOCK_'):
        return output(mode, state=r['state'])
    if r['state'] in ('APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'):
        decision = 'CANDIDATE_RECOVERY_REQUIRED' if mode == 'CANDIDATE' else 'ORIGINAL_PRESENT_PENDING_RECONCILIATION'
    elif r['state'] == 'ROLLED_BACK' and r.get('phase') == 'COMPLETED' and mode == 'ORIGINAL':
        decision = 'TERMINAL_ORIGINAL_OBSERVED_UNVERIFIED'
    elif mode == 'CANDIDATE':
        decision = 'BLOCK_UNAUTHORIZED_CANDIDATE'
    else:
        decision = 'BASELINE_OBSERVED_NO_ACTION'
    return output(decision, state=r['state'], phase=r.get('phase'), target=r['target'],
                  watchdog='ARMED_SIMULATED_NOT_PHYSICAL', transactionId=r['id'])

def rehearse(root, instance, observer, restore):
    """Injectable test transport. No real command path, no persisted state changes."""
    r, s = evidence(root, instance)
    if r.get('state') not in ('APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'):
        return output('BLOCK_STATE')
    if r['state'] == 'UNCERTAIN' and r.get('phase') != 'ROLLBACK_INTENT':
        return output('BLOCK_UNCERTAIN_APPLY')
    before = compare(s['snapshot'], observer(), r['target'])
    if before.startswith('BLOCK_'):
        return output(before)
    if before == 'ORIGINAL':
        return output('SIMULATED_ORIGINAL_OBSERVED_PENDING')
    # Only the injected callable can change a fake transport. No CLI exposes it.
    restore(r['target'], s['snapshot'])
    after = compare(s['snapshot'], observer(), r['target'])
    if after == 'ORIGINAL':
        return output('SIMULATED_RESTORE_READBACK_PASS_NOT_COMMITTED')
    return output('BLOCK_RESTORE_READBACK', observed=after)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--instance', required=True)
    a = ap.parse_args()
    try:
        result = inspect(a.root, a.instance)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result = output('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('CANDIDATE_RECOVERY_REQUIRED','ORIGINAL_PRESENT_PENDING_RECONCILIATION','TERMINAL_ORIGINAL_OBSERVED_UNVERIFIED','BASELINE_OBSERVED_NO_ACTION') else 2

if __name__ == '__main__':
    sys.exit(main())
