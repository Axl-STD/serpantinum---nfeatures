#!/usr/bin/env python3
"""Fail-closed physical watchdog integration boundary.

Only observes real Hyprland. Recovery is delegated to the existing offline IPC
engine, and is NEVER represented as a physical rollback.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ipc = load('display-ipc-transaction-lab-v0600.py', 'physical_watchdog_ipc')
worker = load('display-physical-rollback-worker-v0600.py', 'physical_watchdog_reader')
recovery = load('display-unified-ipc-recovery-v0600.py', 'physical_watchdog_offline')

def outcome(decision, **fields):
    return dict(version='0.6.0', mode='physical-watchdog-boundary', decision=decision,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False, **fields)

def inspect(root, instance, fixture=None):
    with ipc.locked(root) as path:
        record = ipc.read(path, 'transaction.json')
        effect = ipc.read(path, 'simulated-ipc.json')
        if record.get('target') not in ('eDP-1', 'eDP-2') or effect.get('connector') != record['target']:
            return outcome('BLOCK_IDENTITY')
        if record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return outcome('BLOCK_NEW_BOOT')
        target = record['target']
        transaction_id = record.get('id')
        generation = record.get('generation')
        state = record.get('state')
        phase = record.get('phase')
    observed = worker.execute(target, instance, fixture=fixture)
    if observed.get('target') != target:
        return outcome('BLOCK_MONITOR_IDENTITY')
    return outcome('PHYSICAL_OBSERVATION_READONLY', target=target,
                   transactionId=transaction_id, generation=generation,
                   state=state, phase=phase, observedMode=observed.get('observedMode'),
                   topology=observed.get('topology'), simulatedMode=effect.get('mode'),
                   physicalStateNotProven=True)

def simulate_recovery(root, instance, fixture=None, now_ns=None):
    before = inspect(root, instance, fixture)
    if before['decision'] != 'PHYSICAL_OBSERVATION_READONLY':
        return before
    result = recovery.execute(root, now_ns=now_ns)
    after = inspect(root, instance, fixture)
    if after['decision'] != 'PHYSICAL_OBSERVATION_READONLY':
        return outcome('BLOCK_POST_OBSERVATION', recoveryDecision=result['decision'])
    if after['transactionId'] != before['transactionId'] or after['target'] != before['target']:
        return outcome('BLOCK_TRANSACTION_CHANGED')
    return outcome('SIMULATED_RECOVERY_ONLY', recoveryDecision=result['decision'],
                   state=after['state'], observedMode=after['observedMode'],
                   physicalStateNotProven=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('inspect', 'recover-simulated'))
    parser.add_argument('root')
    parser.add_argument('--instance', required=True)
    parser.add_argument('--fixture')
    args = parser.parse_args()
    try:
        result = (inspect(args.root, args.instance, args.fixture) if args.action == 'inspect'
                  else simulate_recovery(args.root, args.instance, args.fixture))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result = outcome('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('PHYSICAL_OBSERVATION_READONLY', 'SIMULATED_RECOVERY_ONLY') else 2

if __name__ == '__main__':
    sys.exit(main())
