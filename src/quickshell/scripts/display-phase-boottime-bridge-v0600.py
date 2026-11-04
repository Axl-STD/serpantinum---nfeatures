#!/usr/bin/env python3
"""Offline-only BOOTTIME recovery with durable rollback phase journal.

Never invokes Hyprland or authorizes physical display changes.
UNCERTAIN is always fail-closed: this module does not reconcile it.
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
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

phase = load('display-phase-journal-v0600.py', 'phase_bridge_journal')
temporal = load('display-temporal-schema3-v0600.py', 'phase_bridge_temporal')
c = phase.c
sim = phase.sim

def outcome(decision, record=None):
    return dict(version='0.6.0', mode='phase-boottime-bridge-offline', decision=decision,
                state=record['state'] if record else None,
                generation=record['generation'] if record else None,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

def execute(root, sample=None, boot=None, expected_token=None, expected_generation=None):
    root = Path(root)
    sample = temporal.clocks() if sample is None else sample
    boot = temporal.boot_id() if boot is None else boot
    if not isinstance(sample, dict) or set(sample) != {'realtime_ns', 'boottime_ns'} or any(type(v) is not int or v <= 0 for v in sample.values()):
        raise ValueError('invalid clock sample')
    with c._locked(root) as path:
        r = c._read(path)
        side = temporal._read(path)
        if r is None or side is None:
            return outcome('BLOCK_INCOMPLETE', r)
        if (side['transaction_id'] != r['id'] or side['deadline_realtime_ns'] != r['deadline_ns']
                or r['generation'] < side['generation_at_binding'] or side['recovery_required'] is not True):
            return outcome('BLOCK_IDENTITY', r)
        if expected_token is not None and expected_token != r['id']:
            return outcome('BLOCK_IDENTITY', r)
        if expected_generation is not None and expected_generation != r['generation']:
            return outcome('BLOCK_STALE_GENERATION', r)
        if side['boot_id'] != boot:
            return outcome('BLOCK_NEW_BOOT', r)
        if r['state'] in ('CONFIRMED', 'ROLLED_BACK'):
            return outcome('TERMINAL_NO_ACTION', r)
        if r['state'] == 'UNCERTAIN':
            return outcome('BLOCK_UNCERTAIN', r)
        if r['snapshot'] != sim.BASE:
            return outcome('BLOCK_SNAPSHOT', r)
        existing = phase.read(path)
        if existing is not None:
            return outcome('BLOCK_EXISTING_PHASE_JOURNAL', r)
        if r['state'] not in ('APPLIED', 'ROLLBACK_REQUIRED'):
            return outcome('BLOCK_STATE', r)
        if sample['boottime_ns'] < side['deadline_boottime_ns']:
            if abs((side['deadline_realtime_ns'] - sample['realtime_ns']) - (side['deadline_boottime_ns'] - sample['boottime_ns'])) > 2_000_000_000:
                return outcome('BLOCK_CLOCK_DIVERGENCE', r)
            return outcome('WAITING', r)
        observed = sim._observed(path)
        if observed not in (sim.BASE, sim.TARGET):
            return outcome('BLOCK_OBSERVATION', r)
        if r['state'] == 'APPLIED':
            r = dict(r, state='ROLLBACK_REQUIRED', generation=r['generation'] + 1)
            c._write(path, r)
        # Intent is durable before UNCERTAIN and before the simulated effect.
        phase.write(path, r, 'ROLLBACK_INTENT', observed)
        uncertain = dict(r, state='UNCERTAIN', generation=r['generation'] + 1)
        c._write(path, uncertain)
        sim._write_observed(path, sim.BASE)
        if sim._observed(path) != sim.BASE:
            return outcome('BLOCK_VERIFICATION', uncertain)
        phase.write(path, uncertain, 'ROLLBACK_EFFECT_OBSERVED', sim.BASE)
        final = dict(uncertain, state='ROLLED_BACK', generation=uncertain['generation'] + 1)
        c._write(path, final)
        phase.write(path, final, 'COMPLETED', sim.BASE)
        return outcome('SIMULATED_ROLLBACK_VERIFIED', final)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    args = parser.parse_args()
    try:
        result = execute(Path(args.root))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result = outcome('BLOCK_AND_INVESTIGATE')
        result['errorType'] = type(exc).__name__
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('SIMULATED_ROLLBACK_VERIFIED', 'TERMINAL_NO_ACTION') else 2

if __name__ == '__main__':
    sys.exit(main())
