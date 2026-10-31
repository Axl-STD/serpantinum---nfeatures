#!/usr/bin/env python3
"""Serpantinum v0.5.23: systemd watchdog with automatic SIMULATED rollback.

Never applies or restores physical outputs. Snapshot equivalence is not rollback.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
PREFIX = 'serpantinum-display-v0514-'
TOKEN = re.compile(r'[0-9a-f]{24}\Z')


def dependency():
    source = HERE / 'display-recovery-bundle.py'
    if not source.is_file():
        raise RuntimeError('Missing v0.5.13 bundle controller')
    spec = importlib.util.spec_from_file_location('display_bundle_0514', source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state_path(folder):
    return folder / 'watchdog.json'


def read_state(folder):
    path = state_path(folder)
    if path.is_symlink():
        raise RuntimeError('Unsafe watchdog state symlink')
    if not path.exists():
        return {'version': '0.5.14', 'phase': 'idle', 'pending': False}
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe watchdog state permissions')
    return json.loads(path.read_text(encoding='utf-8'))


def active(unit):
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                            capture_output=True, text=True, timeout=6)
    return result.returncode == 0 and 'ActiveState=active' in result.stdout.splitlines()


def start(seconds, root=None, launcher=None, readiness=None):
    if not 5 <= seconds <= 120:
        raise ValueError('Timeout must be 5..120 seconds')
    mod = dependency()
    folder = mod.runtime_dir(root)
    with mod.locked(folder):
        payload = mod.read_bundle(folder)
        if payload is None or payload.get('phase') != 'sealed-read-only':
            raise ValueError('A valid sealed bundle is required')
        state = read_state(folder)
        if state.get('pending'):
            raise ValueError('A watchdog transaction is already pending')
        token = payload['id']
        if not TOKEN.fullmatch(token):
            raise ValueError('Invalid bundle identity')
        unit = PREFIX + token + '.service'
        state = {'version': '0.5.14', 'mode': 'simulation-only', 'phase': 'starting',
                 'transactionId': token, 'unit': unit, 'pending': True,
                 'deadline': time.time() + seconds, 'timeoutSeconds': seconds,
                 'watchdogReady': False, 'integrityVerified': True,
                 'physicalOutputsChanged': False, 'persistentConfigChanged': False,
                 'physicalRollbackAttempted': False, 'physicalRollbackVerified': False}
        mod.atomic_write(state_path(folder), state)
    try:
        if launcher is None:
            cmd = ['systemd-run', '--user', '--unit=' + unit[:-8], '--collect', '--quiet',
                   sys.executable, str(Path(__file__).resolve()), '_watch', '--id', token]
            subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=True)
        else:
            launcher(token)
        until = time.monotonic() + 6
        while time.monotonic() < until:
            with mod.locked(folder):
                observed = read_state(folder)
                ready = observed.get('transactionId') == token and observed.get('watchdogReady')
            if ready and (readiness(unit) if readiness else active(unit)):
                return observed
            time.sleep(0.1)
        raise RuntimeError('Watchdog readiness not confirmed')
    except Exception:
        with mod.locked(folder):
            observed = read_state(folder)
            if observed.get('transactionId') == token and observed.get('pending'):
                observed.update(phase='startup-failed', pending=False, watchdogReady=False)
                mod.atomic_write(state_path(folder), observed)
        if launcher is None:
            subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=6)
        raise


def simulate_rollback(saved, current, runner=None, observer=None):
    """Run the injected simulation engine; never execute plan argv."""
    source = HERE / 'display-rollback-engine.py'
    if not source.is_file():
        raise RuntimeError('Missing v0.5.21 simulation engine')
    spec = importlib.util.spec_from_file_location('display_rollback_0523', source)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    result = engine.rehearse(saved, current, runner or (lambda name: True),
                             observer or (lambda: current))
    if result.get('physicalRollbackVerified') is not False or result.get('commandsExecuted') != 0:
        raise RuntimeError('Simulation safety contract violated')
    return result


def simulation_fields(result):
    phase = result.get('phase')
    if phase not in ('rollback-verified', 'rollback-incomplete', 'rollback-failed'):
        raise ValueError('Unexpected simulated rollback phase')
    return {'rollbackSimulationPhase': 'rollback-simulated-' + phase.split('-', 1)[1],
            'rollbackSimulation': result, 'systemdRollbackIntegration': True,
            'simulatedRollbackVerified': result.get('simulatedRollbackVerified') is True,
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0}


def rehearse_rollback(saved, current, runner=None, observer=None):
    """Injected-only v0.5.27 rehearsal. No command execution."""
    source = HERE / 'display-physical-rollback-engine.py'
    spec = importlib.util.spec_from_file_location('display_rehearsal_0528', source)
    if spec is None or spec.loader is None:
        raise RuntimeError('Missing rehearsal engine')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    result = engine.rehearse(saved, current, runner or (lambda _: True),
                             observer or (lambda: current))
    if (result.get('mode') != 'injected-rehearsal-only' or
            result.get('physicalRollbackVerified') is not False or
            result.get('physicalExecutionEnabled') is not False or
            result.get('commandsExecuted') != 0):
        raise RuntimeError('Rehearsal safety contract violated')
    return result


def rehearsal_fields(result):
    phases = {'rollback-rehearsal-verified': 'rollback-rehearsal-verified',
              'rollback-rehearsal-incomplete': 'rollback-rehearsal-incomplete',
              'rollback-rehearsal-failed': 'rollback-rehearsal-failed'}
    phase = phases.get(result.get('phase'))
    if phase is None:
        raise ValueError('Unknown rehearsal result')
    verified = (phase == 'rollback-rehearsal-verified' and
                result.get('simulatedRollbackVerified') is True)
    return {'rollbackRehearsalPhase': phase, 'rollbackRehearsal': result,
            'rollbackRehearsalVerified': verified,
            'systemdRehearsalIntegration': True,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def watch(token, root=None, capture=None):
    if not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction ID')
    mod = dependency()
    folder = mod.runtime_dir(root)
    with mod.locked(folder):
        state = read_state(folder)
        if state.get('transactionId') != token or not state.get('pending') or state.get('phase') != 'starting':
            raise ValueError('No matching starting transaction')
        payload = mod.read_bundle(folder)
        if payload is None or payload.get('id') != token:
            raise ValueError('Sealed bundle missing or mismatched')
        state.update(phase='simulated-pending', watchdogReady=True)
        mod.atomic_write(state_path(folder), state)
    while True:
        with mod.locked(folder):
            state = read_state(folder)
            if state.get('transactionId') != token or not state.get('pending'):
                return
            remaining = state['deadline'] - time.time()
            if remaining <= 0:
                state.update(phase='verification-running')
                mod.atomic_write(state_path(folder), state)
                break
        time.sleep(min(0.25, max(0.01, remaining)))
    try:
        with mod.locked(folder):
            payload = mod.read_bundle(folder)
            if payload is None or payload.get('id') != token:
                raise ValueError('Sealed bundle identity changed')
        if capture is None:
            import importlib.util
            source = HERE / 'display-transaction.py'
            spec = importlib.util.spec_from_file_location('display_snapshot_0514', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            current = module.snapshot() if hasattr(module, 'snapshot') else None
            if current is None:
                result = subprocess.run([sys.executable, str(source), 'snapshot'], capture_output=True,
                                        text=True, timeout=8, check=True)
                current = json.loads(result.stdout)
        else:
            current = capture()
        verifier = HERE / 'display-restore-verify.py'
        with tempfile.TemporaryDirectory() as tmp:
            before = Path(tmp) / 'before.json'
            after = Path(tmp) / 'after.json'
            before.write_text(json.dumps(payload['snapshot']))
            after.write_text(json.dumps(current))
            result = subprocess.run([sys.executable, str(verifier), 'verify', '--snapshot', str(before),
                                     '--current', str(after)], capture_output=True, text=True, timeout=10)
            report = json.loads(result.stdout)
            if result.returncode not in (0, 2):
                raise RuntimeError('Verifier failed: ' + result.stderr[:300])
        equivalent = report.get('equivalentObservableState') is True
        outcome = {'phase': 'snapshot-equivalent' if equivalent else 'snapshot-different',
                   'equivalentObservableState': equivalent, 'differences': report.get('differences', []),
                   'integrityVerified': True}
        # Automatic simulation happens in the independent systemd worker.
        # Keep the existing terminal phase for v0.5.16 cleanup compatibility.
        try:
            simulation = simulate_rollback(payload['snapshot']['outputs'], current['outputs'])
            outcome.update(simulation_fields(simulation))
        except Exception as sim_exc:
            outcome.update(rollbackSimulationPhase='rollback-simulated-failed',
                           simulatedRollbackVerified=False, systemdRollbackIntegration=True,
                           rollbackSimulationError=str(sim_exc), commandsExecuted=0,
                           physicalRollbackAttempted=False, physicalRollbackVerified=False)
        try:
            rehearsal = rehearse_rollback(payload['snapshot']['outputs'], current['outputs'])
            outcome.update(rehearsal_fields(rehearsal))
        except Exception as rehearsal_exc:
            outcome.update(rollbackRehearsalPhase='rollback-rehearsal-failed',
                           rollbackRehearsalVerified=False, systemdRehearsalIntegration=True,
                           rollbackRehearsalError=str(rehearsal_exc)[:300],
                           physicalRollbackVerified=False, commandsExecuted=0)
    except Exception as exc:
        outcome = {'phase': 'verification-failed', 'equivalentObservableState': None,
                   'integrityVerified': False, 'error': str(exc),
                   'rollbackSimulationPhase': 'rollback-simulated-failed',
                   'simulatedRollbackVerified': False, 'systemdRollbackIntegration': True,
                   'physicalRollbackVerified': False, 'commandsExecuted': 0,
                   'rollbackRehearsalPhase': 'rollback-rehearsal-failed',
                   'rollbackRehearsalVerified': False, 'systemdRehearsalIntegration': True}
    with mod.locked(folder):
        state = read_state(folder)
        if state.get('transactionId') == token and state.get('phase') == 'verification-running':
            state.update(outcome)
            state.update(pending=False, finishedAt=time.time(),
                         note='Read-only snapshot comparison; no physical rollback')
            mod.atomic_write(state_path(folder), state)


def confirm(token, root=None):
    if not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction ID')
    mod = dependency()
    folder = mod.runtime_dir(root)
    with mod.locked(folder):
        state = read_state(folder)
        if (state.get('transactionId') != token or not state.get('pending') or
                not state.get('watchdogReady') or time.time() >= state.get('deadline', 0)):
            raise ValueError('No matching active transaction')
        payload = mod.read_bundle(folder)
        if payload is None or payload.get('id') != token:
            raise ValueError('Sealed bundle mismatch')
        state.update(phase='simulated-confirmed', pending=False, finishedAt=time.time())
        mod.atomic_write(state_path(folder), state)
        return state


def status(root=None):
    mod = dependency()
    folder = mod.runtime_dir(root)
    with mod.locked(folder):
        state = read_state(folder)
        if state.get('pending'):
            payload = mod.read_bundle(folder)
            if payload is None or payload.get('id') != state.get('transactionId'):
                raise ValueError('Pending transaction has no valid sealed bundle')
    if state.get('unit'):
        state = dict(state, systemdActive=active(state['unit']) if root is None else None)
    return state


def clear(root=None):
    mod = dependency()
    folder = mod.runtime_dir(root)
    with mod.locked(folder):
        state = read_state(folder)
        if state.get('pending'):
            raise ValueError('Cannot clear a pending transaction')
        mod.read_bundle(folder)
        state_path(folder).unlink(missing_ok=True)
        (folder / 'bundle.json').unlink(missing_ok=True)
    return {'version': '0.5.14', 'phase': 'cleared', 'physicalOutputsChanged': False}


def selftest():
    mod = dependency()
    sample = {'outputs': [{'name': 'eDP-2', 'width': 1920, 'height': 1080,
                           'refreshRate': 60.06, 'x': 0, 'y': 0, 'scale': 1,
                           'transform': 0, 'disabled': False, 'mirrorOf': 'none'}]}
    approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
    with tempfile.TemporaryDirectory() as tmp:
        mod.prepare(Path('/unused'), tmp, lambda _: approved, lambda: sample)
        try:
            start(5, tmp, launcher=lambda _: None, readiness=lambda _: True)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unready supervisor accepted')
        assert status(tmp)['phase'] == 'startup-failed'
        clear(tmp)
        mod.prepare(Path('/unused'), tmp, lambda _: approved, lambda: sample)
        with mod.locked(mod.runtime_dir(tmp)):
            payload = mod.read_bundle(mod.runtime_dir(tmp))
            token = payload['id']
            state = {'transactionId': token, 'pending': True, 'watchdogReady': True,
                     'deadline': time.time()+20, 'phase': 'simulated-pending'}
            mod.atomic_write(state_path(mod.runtime_dir(tmp)), state)
        try:
            start(5, tmp, launcher=lambda _: None, readiness=lambda _: True)
        except ValueError:
            pass
        else:
            raise AssertionError('Concurrent transaction accepted')
        assert confirm(token, tmp)['phase'] == 'simulated-confirmed'
        clear(tmp)
    for phase, expected in [('rollback-verified', 'rollback-simulated-verified'),
                            ('rollback-incomplete', 'rollback-simulated-incomplete'),
                            ('rollback-failed', 'rollback-simulated-failed')]:
        fields = simulation_fields({'phase': phase, 'simulatedRollbackVerified': phase == 'rollback-verified'})
        assert fields['rollbackSimulationPhase'] == expected
        assert fields['physicalRollbackVerified'] is False
        assert fields['simulatedRollbackVerified'] is (phase == 'rollback-verified')
    return {'selftest': 'passed', 'simulatedPhasesTested': 3, 'sharedIdentity': True, 'readinessRequired': True,
            'duplicateRejected': True, 'physicalOutputsChanged': False,
            'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'start', 'status', 'confirm', 'clear', '_watch'])
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--id')
    args = parser.parse_args()
    if args.command == 'selftest':
        result = selftest()
    elif args.command == 'start':
        result = start(args.seconds)
    elif args.command == 'status':
        result = status()
    elif args.command == 'confirm':
        if not args.id:
            parser.error('--id required')
        result = confirm(args.id)
    elif args.command == 'clear':
        result = clear()
    else:
        if not args.id:
            parser.error('--id required')
        watch(args.id)
        return 0
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        sys.exit(2)
