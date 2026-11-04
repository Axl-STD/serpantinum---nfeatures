#!/usr/bin/env python3
"""Fail-closed reconciliation of *simulated* rollback phase records only.

Does not invoke Hyprland, perform physical operations, or infer physical recovery.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(name, alias):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

phase = load('display-phase-journal-v0600.py', 'reconcile_phase')
temporal = load('display-temporal-schema3-v0600.py', 'reconcile_temporal')
c = phase.c
sim = phase.sim

def outcome(decision, record=None):
    return dict(version='0.6.0', mode='phase-reconciliation-simulated', decision=decision,
                state=record['state'] if record else None,
                generation=record['generation'] if record else None,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

def execute(root, sample=None, current_boot=None, expected_token=None):
    with c._locked(Path(root)) as path:
        r = c._read(path)
        side = temporal._read(path)
        j = phase.read(path)
        if r is None or side is None or j is None:
            return outcome('BLOCK_INCOMPLETE', r)
        boot = temporal.boot_id() if current_boot is None else current_boot
        if (side['boot_id'] != boot or j['boot_id'] != boot):
            return outcome('BLOCK_NEW_BOOT', r)
        if (side['transaction_id'] != r['id'] or j['transaction_id'] != r['id']
                or side['deadline_realtime_ns'] != r['deadline_ns']
                or r['generation'] < side['generation_at_binding']
                or side['recovery_required'] is not True
                or (expected_token is not None and expected_token != r['id'])
                or r['snapshot'] != sim.BASE):
            return outcome('BLOCK_IDENTITY', r)
        if j['phase'] not in ('ROLLBACK_INTENT', 'ROLLBACK_EFFECT_OBSERVED', 'COMPLETED'):
            return outcome('BLOCK_WRONG_PHASE', r)
        if r['state'] not in ('UNCERTAIN', 'ROLLED_BACK'):
            return outcome('BLOCK_STATE', r)
        observed = sim._observed(path)
        if observed != sim.BASE:
            return outcome('BLOCK_EFFECT_NOT_RESTORED', r)
        if j['phase'] == 'ROLLBACK_INTENT':
            # The intent must prove that TARGET was present before rollback began.
            if j['observed_digest'] != phase.digest(sim.TARGET):
                return outcome('BLOCK_INTENT_EVIDENCE', r)
        else:
            if j['observed_digest'] != phase.digest(sim.BASE):
                return outcome('BLOCK_EFFECT_EVIDENCE', r)
        if r['state'] == 'UNCERTAIN':
            if j['generation'] != r['generation'] - 1 and not (j['phase'] == 'ROLLBACK_EFFECT_OBSERVED' and j['generation'] == r['generation']):
                return outcome('BLOCK_GENERATION', r)
            # This can only complete a simulated rollback. No physical authority is granted.
            phase.write(path, r, 'ROLLBACK_EFFECT_OBSERVED', observed)
            if sim._observed(path) != sim.BASE:
                return outcome('BLOCK_VERIFICATION', r)
            final = dict(r, state='ROLLED_BACK', generation=r['generation'] + 1)
            c._write(path, final)
            phase.write(path, final, 'COMPLETED', sim.BASE)
            return outcome('SIMULATED_RECONCILIATION_VERIFIED', final)
        if j['phase'] == 'COMPLETED':
            if j['generation'] != r['generation']:
                return outcome('BLOCK_GENERATION', r)
            return outcome('TERMINAL_VERIFIED', r)
        if j['phase'] == 'ROLLBACK_EFFECT_OBSERVED' and j['generation'] == r['generation'] - 1:
            phase.write(path, r, 'COMPLETED', sim.BASE)
            return outcome('TERMINAL_JOURNAL_REPAIRED', r)
        return outcome('BLOCK_GENERATION', r)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    args = parser.parse_args()
    try:
        result = execute(args.root)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result = outcome('BLOCK_AND_INVESTIGATE')
        result['errorType'] = type(exc).__name__
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('SIMULATED_RECONCILIATION_VERIFIED', 'TERMINAL_VERIFIED', 'TERMINAL_JOURNAL_REPAIRED') else 2

if __name__ == '__main__':
    sys.exit(main())
