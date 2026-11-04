#!/usr/bin/env python3
"""Offline-only portable recovery decision gate. Never schedules or executes display changes.

Combines the validated Hyprland read-only plan and temporal schema-3 decision.
This is NOT an atomic transaction coordinator, service backend, or physical authorization.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

class CoordinatorDenied(RuntimeError):
    pass

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file():
        raise CoordinatorDenied('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def decide(plan, temporal, backend='none'):
    """Fail closed; return a decision, never a physical execution token."""
    if backend not in ('none', 'systemd', 'runit'):
        raise CoordinatorDenied('unknown backend')
    if not isinstance(plan, dict) or not isinstance(temporal, dict):
        raise CoordinatorDenied('invalid evidence')
    if plan.get('physicalExecutionEnabled') is not False or plan.get('executionAuthorized') is not False or type(plan.get('commandsExecuted')) is not int or plan['commandsExecuted'] != 0:
        raise CoordinatorDenied('unsafe plan evidence')
    if temporal.get('physicalExecutionEnabled') is not False or temporal.get('executionAuthorized') is not False or type(temporal.get('commandsExecuted')) is not int or temporal['commandsExecuted'] != 0:
        raise CoordinatorDenied('unsafe temporal evidence')
    if plan.get('decision') != 'READ_ONLY_PLAN_READY' or plan.get('target') != 'eDP-1' or plan.get('protected') != 'HDMI-A-1' or plan.get('original') != '1920x1080@60.06Hz' or plan.get('candidate') != '1920x1080@144.06Hz' or plan.get('configurationAdapter') != 'hyprland':
        decision = 'BLOCK_PLAN'
    elif temporal.get('clock_schema') != 3 or temporal.get('recoveryRequired') is not True:
        decision = 'BLOCK_TEMPORAL_CONTRACT'
    elif temporal.get('decision') == 'WAITING' and temporal.get('transactionState') in ('PREPARED', 'ARMED', 'APPLIED'):
        decision = 'OFFLINE_WAITING'
    elif temporal.get('decision') == 'RECOVERY_DUE' and temporal.get('transactionState') in ('PREPARED', 'ARMED', 'APPLIED', 'ROLLBACK_REQUIRED'):
        decision = 'OFFLINE_RECOVERY_DUE'
    elif temporal.get('decision') == 'TERMINAL_STATE_OBSERVED' and temporal.get('transactionState') in ('CONFIRMED', 'ROLLED_BACK'):
        decision = 'OFFLINE_TERMINAL'
    else:
        decision = 'BLOCK_RECONCILIATION'
    return {'version':'0.6.0','mode':'portable-recovery-offline','decision':decision,
            'configurationAdapter':'hyprland','supervisionBackend':backend,
            'physicalExecutionEnabled':False,'executionAuthorized':False,
            'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan-json', required=True)
    parser.add_argument('--temporal-json', required=True)
    parser.add_argument('--backend', choices=['none','systemd','runit'], default='none')
    args = parser.parse_args()
    try:
        # CLI accepts explicit evidence files; caller is responsible for trusted capture.
        # No evidence is allowed to authorize physical execution.
        def read(path):
            p=Path(path)
            if p.is_symlink() or not p.is_file() or p.stat().st_size > 65536:
                raise CoordinatorDenied('unsafe evidence file')
            return json.loads(p.read_text())
        result=decide(read(args.plan_json),read(args.temporal_json),args.backend)
        status=0 if result['decision'].startswith('OFFLINE_') else 2
    except (OSError,ValueError,TypeError,CoordinatorDenied) as exc:
        result={'version':'0.6.0','mode':'portable-recovery-offline','decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,
                'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}
        status=2
    print(json.dumps(result,indent=2))
    return status

if __name__=='__main__':
    sys.exit(main())
