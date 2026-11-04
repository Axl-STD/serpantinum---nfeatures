#!/usr/bin/env python3
"""Unified physical recovery guard: cross-checks snapshot, watchdog, IPC and live readback.

Read-only physical path. Never executes monitor mutations or closes physical transactions.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

snap = load('display-physical-snapshot-binding-v0600.py', 'integrated_snapshot')
wd = load('display-watchdog-obligation-v0600.py', 'integrated_watchdog')
rehearsal = load('display-physical-rollback-rehearsal-v0600.py', 'integrated_rehearsal')
transport = load('display-restricted-hyprland-transport-v0600.py', 'integrated_transport')
ipc = snap.ipc


def output(decision, **kw):
    return dict(version='0.6.0', decision=decision, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0, rollbackPhysicalVerified=False, **kw)


def inspect(root, monitors, *, instance=None):
    """Monitors are a fresh caller-provided observation; no physical authority follows."""
    with ipc.locked(root) as p:
        r = wd._read(p)
        wd._receipt(p, r)
        s = ipc.read(p, 'physical-snapshot.json')
        if not isinstance(s, dict) or s.get('schema') != 1:
            raise RuntimeError('missing snapshot')
        if (s.get('transaction_id') != r['id'] or s.get('boot_id') != r['boot_id'] or
            s.get('target') != r['target'] or s.get('topology') != r['topology'] or
            type(r.get('generation')) is not int or
            r['generation'] < s.get('generation_at_binding', 10**9) or
            snap.digest(s.get('snapshot')) != s.get('snapshot_sha256')):
            raise RuntimeError('snapshot identity mismatch')
        baseline = snap.validate(s['snapshot'], r['target'])
        if baseline != s['snapshot']:
            raise RuntimeError('invalid baseline')
        ident = s.get('identity')
        if not isinstance(ident, dict) or not isinstance(ident.get('instance'), str):
            raise RuntimeError('invalid identity')
        if instance is not None and instance != ident['instance']:
            return output('BLOCK_COMPOSITOR_CHANGED', state=r['state'])
        phase = r.get('phase')
        state = r.get('state')
        mode = rehearsal.compare(baseline, monitors, r['target'])
        if state == 'ROLLED_BACK':
            decision = 'TERMINAL_ORIGINAL_OBSERVED_UNVERIFIED' if phase == 'COMPLETED' and mode == 'ORIGINAL' else 'BLOCK_TERMINAL_MISMATCH'
        elif state == 'CONFIRMED':
            decision = 'CONFIRMED_NO_ACTION'
        elif state == 'UNCERTAIN':
            decision = 'BLOCK_UNCERTAIN_PHYSICAL'
        elif state in ('APPLIED', 'ROLLBACK_REQUIRED') and phase == 'APPLY_VERIFIED':
            decision = 'PHYSICAL_ORIGINAL_PENDING' if mode == 'ORIGINAL' else 'PHYSICAL_CANDIDATE_PENDING'
        elif state in ('PREPARED', 'ARMED') and phase == 'NONE' and mode == 'ORIGINAL':
            decision = 'BASELINE_OBSERVED'
        else:
            decision = 'BLOCK_INCONSISTENT_PHASE'
        return output(decision, state=state, phase=phase, observed=mode, target=r['target'],
                      transactionId=r['id'], generation=r['generation'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--instance', required=True)
    ap.add_argument('--fixture', help='Offline JSON monitor observation; omit for live readonly IPC')
    args = ap.parse_args()
    try:
        if args.fixture:
            p = Path(args.fixture)
            if p.is_symlink() or not p.is_file():
                raise RuntimeError('unsafe fixture')
            monitors = json.loads(p.read_text())
        else:
            import subprocess
            proc = subprocess.run(['hyprctl', '-i', args.instance, 'monitors', '-j'],
                                  capture_output=True, text=True, timeout=8, check=True)
            monitors = json.loads(proc.stdout)
        result = inspect(Path(args.root), monitors, instance=args.instance)
        rc = 0 if not result['decision'].startswith('BLOCK_') else 2
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = output('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
        rc = 2
    print(json.dumps(result, indent=2))
    return rc

if __name__ == '__main__':
    raise SystemExit(main())
