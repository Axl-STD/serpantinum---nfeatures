#!/usr/bin/env python3
"""Offline-only watchdog adapter. No monitor commands, no rollback claims."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / 'display-physical-recovery-controller-v0600.py'
if p.is_symlink() or not p.is_file():
    raise RuntimeError('unsafe controller dependency')
spec = importlib.util.spec_from_file_location('recovery_controller_v0600', p)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def decision(root, now_ns=None):
    """Fail closed: never performs a physical action."""
    try:
        state = controller.expire(root, now_ns=now_ns)
        record = controller.inspect(root)
        if state == 'NO_STATE':
            outcome = 'BLOCK_NO_RECOVERY_STATE'
        elif state == 'ROLLBACK_REQUIRED':
            outcome = 'BLOCK_PHYSICAL_ROLLBACK_NOT_IMPLEMENTED'
        elif state == 'UNCERTAIN':
            outcome = 'BLOCK_AND_INVESTIGATE'
        elif state in ('CONFIRMED', 'ROLLED_BACK'):
            outcome = 'TERMINAL_REQUIRES_EXTERNAL_VERIFICATION'
        else:
            outcome = 'WAIT_OR_REQUIRE_OPERATOR'
        return dict(version='0.6.0', mode='offline-recovery-watchdog', decision=outcome,
                    state=state, generation=record['generation'] if record else None,
                    executionAuthorized=False, physicalExecutionEnabled=False,
                    commandsExecuted=0, logicalRecoveryVerified=False)
    except (OSError, ValueError, controller.RecoveryDenied) as exc:
        return dict(version='0.6.0', mode='offline-recovery-watchdog',
                    decision='BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__,
                    executionAuthorized=False, physicalExecutionEnabled=False,
                    commandsExecuted=0, logicalRecoveryVerified=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    args = parser.parse_args()
    result = decision(args.root)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if 'errorType' not in result else 2)
