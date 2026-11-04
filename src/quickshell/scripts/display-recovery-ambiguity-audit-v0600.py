#!/usr/bin/env python3
"""Read-only, fail-closed ambiguity audit for simulated recovery v0.6.0.

Does not mutate state, invoke display APIs, or authorize physical operations.
UNCERTAIN has no persisted phase marker in schema 1, so even BASE is ambiguous.
"""
import argparse
import importlib.util
import json
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

sim = load('display-recovery-simulator-v0600.py', 'ambiguity_sim')
temporal = load('display-temporal-schema3-v0600.py', 'ambiguity_temporal')

def audit(root):
    c = sim.controller
    with c._locked(root) as locked:
        record = c._read(locked)
        observed = sim._observed(locked)
        side = temporal._read(locked)
        if record is None or side is None:
            decision = 'BLOCK_MISSING_STATE'
        elif side['transaction_id'] != record['id'] or side['deadline_realtime_ns'] != record['deadline_ns']:
            decision = 'BLOCK_IDENTITY_MISMATCH'
        elif side['boot_id'] != temporal.boot_id():
            decision = 'BLOCK_NEW_BOOT'
        elif record['snapshot'] != sim.BASE:
            decision = 'BLOCK_SNAPSHOT_MISMATCH'
        elif record['state'] == 'UNCERTAIN':
            decision = 'RECONCILIATION_REQUIRED_PHASE_UNKNOWN'
        elif record['state'] == 'ROLLED_BACK' and observed == sim.BASE:
            decision = 'TERMINAL_SIMULATED_VERIFIED'
        elif record['state'] == 'CONFIRMED' and observed == sim.TARGET:
            decision = 'TERMINAL_CONFIRMED'
        elif record['state'] in ('ROLLED_BACK', 'CONFIRMED'):
            decision = 'BLOCK_TERMINAL_EFFECT_MISMATCH'
        elif record['state'] == 'ROLLBACK_REQUIRED':
            decision = 'RECOVERY_PENDING_NOT_EXECUTED'
        else:
            decision = 'NONTERMINAL_NO_RECONCILIATION'
        return {'version':'0.6.0','mode':'simulated-recovery-ambiguity-audit',
                'decision':decision, 'state':record['state'] if record else None,
                'generation':record['generation'] if record else None,
                'observedMode':'BASE' if observed == sim.BASE else 'TARGET' if observed == sim.TARGET else 'UNKNOWN',
                'phasePersisted':False,'physicalExecutionEnabled':False,
                'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args()
    try: out=audit(Path(a.root))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        out={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,
             'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
    print(json.dumps(out,indent=2))
    return 0 if out['decision'] in ('TERMINAL_SIMULATED_VERIFIED','TERMINAL_CONFIRMED','RECOVERY_PENDING_NOT_EXECUTED','NONTERMINAL_NO_RECONCILIATION','RECONCILIATION_REQUIRED_PHASE_UNKNOWN') else 2

if __name__=='__main__':raise SystemExit(main())
