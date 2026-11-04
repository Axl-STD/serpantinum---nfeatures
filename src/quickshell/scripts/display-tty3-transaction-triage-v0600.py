#!/usr/bin/env python3
"""TTY3 read-only physical transaction triage; never mutates Hyprland or IPC state.

Requires an existing physical snapshot and simulated watchdog obligation. The
obligation is NOT evidence of an armed physical watchdog. No physical authority.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ipc = load('display-ipc-transaction-lab-v0600.py', 'tty3_ipc')
snapshot = load('display-physical-snapshot-binding-v0600.py', 'tty3_snapshot')
obligation = load('display-watchdog-obligation-v0600.py', 'tty3_obligation')

class Denied(RuntimeError):
    pass

def result(decision, **fields):
    return dict(version='0.6.0', mode='tty3-physical-transaction-readonly',
                decision=decision, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **fields)

def capture(instance, runner=subprocess.run):
    if not isinstance(instance, str) or not instance or len(instance) > 160 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in instance):
        raise Denied('invalid instance')
    env = {k:v for k,v in os.environ.items() if k not in ('DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE','PYTHONPATH')}
    env['HYPRLAND_INSTANCE_SIGNATURE'] = instance
    env['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
    proc = runner(['hyprctl','-i',instance,'monitors','-j'],env=env,capture_output=True,text=True,timeout=8,check=True)
    return json.loads(proc.stdout)

def assess(root, monitors, instance, current_boot=None):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip() if current_boot is None else current_boot
    with ipc.locked(root) as path:
        r = ipc.read(path, 'transaction.json')
        s = ipc.read(path, 'physical-snapshot.json')
        w = ipc.read(path, 'watchdog-obligation.json')
        if not isinstance(r, dict) or not isinstance(s, dict) or not isinstance(w, dict):
            raise Denied('missing evidence')
        if r.get('schema') != 1 or s.get('schema') != 1 or r.get('boot_id') != boot or s.get('boot_id') != boot:
            raise Denied('boot or schema mismatch')
        if s.get('transaction_id') != r.get('id') or s.get('target') != r.get('target') or s.get('topology') != r.get('topology'):
            raise Denied('snapshot transaction mismatch')
        if type(r.get('generation')) is not int or type(s.get('generation_at_binding')) is not int or r['generation'] < s['generation_at_binding']:
            raise Denied('generation mismatch')
        if snapshot.digest(s.get('snapshot')) != s.get('snapshot_sha256'):
            raise Denied('snapshot digest mismatch')
        original = snapshot.validate(s['snapshot'], r['target'])
        if original != s['snapshot']:
            raise Denied('noncanonical snapshot')
        identity = s.get('identity')
        if not isinstance(identity,dict) or identity.get('instance') != instance:
            return result('BLOCK_COMPOSITOR_CHANGED', state=r.get('state'))
        # Reuse the exact receipt contract, without invoking simulated recovery.
        obligation._receipt(path, r)
        if not isinstance(monitors,list) or len(monitors) != len(original):
            return result('BLOCK_TOPOLOGY_CHANGED', state=r.get('state'))
        by_name = {}
        for m in monitors:
            if not isinstance(m,dict) or not isinstance(m.get('name'),str) or m['name'] in by_name:
                raise Denied('invalid monitors')
            by_name[m['name']] = m
        if set(by_name) != {m['name'] for m in original}:
            return result('BLOCK_TOPOLOGY_CHANGED', state=r.get('state'))
        target = r['target']
        candidate = False
        for baseline in original:
            current = by_name[baseline['name']]
            for field in snapshot.FIELDS:
                if field not in current:
                    raise Denied('missing monitor field')
                if field == 'refreshRate' and baseline['name'] == target:
                    rate = current[field]
                    if type(rate) not in (int,float):
                        raise Denied('invalid refresh')
                    if abs(rate - baseline[field]) <= 0.12:
                        continue
                    if 143.95 <= rate <= 144.15:
                        candidate = True
                        continue
                    return result('BLOCK_UNEXPECTED_MODE', state=r.get('state'))
                if current[field] != baseline[field]:
                    return result('BLOCK_MONITOR_DRIFT', state=r.get('state'), field=field)
        state, phase = r.get('state'), r.get('phase')
        if candidate:
            decision = 'CANDIDATE_OBSERVED_RECOVERY_REQUIRED' if state in ('APPLIED','ROLLBACK_REQUIRED','UNCERTAIN') else 'BLOCK_CANDIDATE_WITHOUT_PENDING_STATE'
        elif state == 'ROLLED_BACK' and phase == 'COMPLETED':
            decision = 'ORIGINAL_OBSERVED_TERMINAL_UNVERIFIED'
        elif state == 'CONFIRMED':
            decision = 'ORIGINAL_OBSERVED_CONFIRMED'
        else:
            decision = 'ORIGINAL_OBSERVED_TRANSACTION_PENDING'
        return result(decision, target=target, state=state, phase=phase,
                      generation=r['generation'], watchdogReceipt='ARMED_SIMULATED_NOT_PHYSICAL')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--instance',required=True)
    args = ap.parse_args()
    try:
        output = assess(args.root, capture(args.instance), args.instance)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        output = result('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
    print(json.dumps(output, indent=2))
    return 0 if output['decision'].startswith(('ORIGINAL_OBSERVED','CANDIDATE_OBSERVED')) else 2

if __name__ == '__main__':
    raise SystemExit(main())
