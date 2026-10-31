#!/usr/bin/env python3
"""Serpantinum v0.5.25 isolated systemd simulation test harness.

Only runs injected Python simulations. Never calls hyprctl or changes displays.
Requires a working user systemd manager for 'run'.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
DEPENDENCIES = ('display-sealed-watchdog.py', 'display-rollback-engine.py',
                'display-recovery-bundle.py', 'display-restore-plan.py',
                'display-restore-verify.py', 'display-watchdog-audit.py',
                'display-authorized-cleanup.py')
SCENARIOS = ('operation-failure', 'incomplete', 'observer-failure')


def load(folder, filename, alias):
    spec = importlib.util.spec_from_file_location(alias, folder / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture():
    engine = load(HERE, 'display-rollback-engine.py', 'fixture_engine')
    return {'schemaVersion': 1, 'outputs': engine.fixtures()}


def prepare_isolated(root, scenario):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Unsafe test root')
    bundle = load(HERE, 'display-recovery-bundle.py', 'setup_bundle')
    watchdog = load(HERE, 'display-sealed-watchdog.py', 'setup_watchdog')
    approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
    snapshot = fixture()
    prepared = bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: snapshot)
    folder = bundle.runtime_dir(root)
    token = prepared['transactionId']
    unit = 'serpantinum-display-v0514-' + token + '.service'
    with bundle.locked(folder):
        bundle.atomic_write(watchdog.state_path(folder), {
            'version': '0.5.14', 'mode': 'simulation-only', 'phase': 'starting',
            'transactionId': token, 'unit': unit, 'pending': True,
            'deadline': time.time() + 2.5, 'timeoutSeconds': 2.5,
            'watchdogReady': False, 'integrityVerified': True,
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False})
    return token, unit


def worker(root, token, scenario):
    if scenario not in SCENARIOS:
        raise ValueError('Invalid scenario')
    watchdog = load(HERE, 'display-sealed-watchdog.py', 'worker_watchdog')
    original = watchdog.simulate_rollback
    snapshot = fixture()

    def injected(saved, current):
        if scenario == 'operation-failure':
            return original(saved, current, runner=lambda _: False)
        if scenario == 'incomplete':
            return original(saved, current, runner=lambda _: True,
                            observer=lambda: [dict(current[0], x=current[0]['x'] + 100), *current[1:]])
        return original(saved, current, runner=lambda _: True,
                        observer=lambda: (_ for _ in ()).throw(RuntimeError('injected observer failure')))

    watchdog.simulate_rollback = injected
    watchdog.watch(token, root=root, capture=lambda: snapshot)


def assess(root, scenario, unit):
    bundle = load(HERE, 'display-recovery-bundle.py', 'check_bundle')
    watchdog = load(HERE, 'display-sealed-watchdog.py', 'check_watchdog')
    auditor = load(HERE, 'display-watchdog-audit.py', 'check_auditor')
    cleanup = load(HERE, 'display-authorized-cleanup.py', 'check_cleanup')
    folder = bundle.runtime_dir(root)
    with bundle.locked(folder):
        payload = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
    if payload is None or payload['id'] != state.get('transactionId'):
        raise AssertionError('Lost or mismatched evidence')
    if state.get('pending') is not False or state.get('physicalRollbackVerified') is not False:
        raise AssertionError('Invalid terminal safety flags')
    expected = ('rollback-simulated-incomplete' if scenario == 'incomplete'
                else 'rollback-simulated-failed')
    if state.get('rollbackSimulationPhase') != expected:
        raise AssertionError('Unexpected simulation phase: ' + str(state.get('rollbackSimulationPhase')))
    classification, healthy = auditor.classify(state, True, 'inactive')
    if healthy or classification not in ('rollback-simulation-failed', 'rollback-simulation-incomplete'):
        raise AssertionError(
            "Auditor classification mismatch: " +
            json.dumps({
                "scenario": scenario,
                "phase": state.get("phase"),
                "rollbackSimulationPhase": state.get("rollbackSimulationPhase"),
                "classification": classification,
                "healthy": healthy,
                "pending": state.get("pending"),
                "simulation": state.get("rollbackSimulation"),
                "error": state.get("error"),
                "rollbackSimulationError": state.get("rollbackSimulationError"),
                "integrityVerified": state.get("integrityVerified"),
                "equivalentObservableState": state.get("equivalentObservableState")
            }, ensure_ascii=False)
        )
    rejected = False
    try:
        cleanup.cleanup(root)
    except ValueError:
        rejected = True
    if not rejected or bundle.read_bundle(folder) is None:
        raise AssertionError('Cleanup did not preserve failed evidence')
    return {'scenario': scenario, 'transactionId': state['transactionId'],
            'unit': unit, 'phase': state['phase'], 'rollbackSimulationPhase': expected,
            'assessment': classification, 'healthy': False, 'cleanupRejected': True,
            'evidencePreserved': True, 'commandsExecuted': 0,
            'physicalRollbackVerified': False}


def run():
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0525-', dir=os.environ.get('XDG_RUNTIME_DIR')) as temp:
        os.chmod(temp, 0o700)
        sandbox = Path(temp)
        for filename in DEPENDENCIES:
            shutil.copy2(HERE / filename, sandbox / filename)
        shutil.copy2(Path(__file__), sandbox / Path(__file__).name)
        for scenario in SCENARIOS:
            root = sandbox / scenario
            root.mkdir(mode=0o700)
            token, unit = prepare_isolated(root, scenario)
            cmd = ['systemd-run', '--user', '--unit=' + unit[:-8], '--collect', '--quiet',
                   sys.executable, str(sandbox / Path(__file__).name), '_worker',
                   '--root', str(root), '--token', token, '--scenario', scenario]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
            watchdog = load(sandbox, 'display-sealed-watchdog.py', 'poll_watchdog_' + scenario.replace('-', '_'))
            deadline = time.monotonic() + 12
            state = {}
            while time.monotonic() < deadline:
                state = watchdog.status(root)
                if state.get('pending') is False:
                    break
                time.sleep(.15)
            else:
                raise RuntimeError('Worker did not reach terminal state: ' + scenario)
            until = time.monotonic() + 5
            while time.monotonic() < until and watchdog.active(unit):
                time.sleep(.1)
            if watchdog.active(unit):
                raise RuntimeError('Worker still active: ' + scenario)
            results.append(assess(root, scenario, unit))
    return {'version': '0.5.25', 'mode': 'isolated-systemd-simulation',
            'selftest': 'passed', 'scenarios': results, 'physicalOutputsChanged': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def selftest():
    snapshot = fixture()
    engine = load(HERE, 'display-rollback-engine.py', 'test_engine')
    for scenario in SCENARIOS:
        if scenario == 'operation-failure':
            result = engine.rehearse(snapshot['outputs'], snapshot['outputs'], lambda _: False,
                                     lambda: snapshot['outputs'])
        elif scenario == 'incomplete':
            result = engine.rehearse(snapshot['outputs'], snapshot['outputs'], lambda _: True,
                                     lambda: [dict(snapshot['outputs'][0], x=123), snapshot['outputs'][1]])
        else:
            result = engine.rehearse(snapshot['outputs'], snapshot['outputs'], lambda _: True,
                                     lambda: (_ for _ in ()).throw(RuntimeError('test observer')))
        assert result['phase'] == ('rollback-incomplete' if scenario == 'incomplete' else 'rollback-failed')
        assert result['physicalRollbackVerified'] is False
    return {'selftest': 'passed', 'scenarios': len(SCENARIOS), 'systemdIntegration': 'not-run',
            'commandsExecuted': 0, 'physicalRollbackVerified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'run', '_worker'])
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    p.add_argument('--scenario', choices=SCENARIOS)
    args = p.parse_args()
    if args.command == '_worker':
        if args.root is None or args.token is None or args.scenario is None:
            raise ValueError('Missing worker arguments')
        worker(args.root, args.token, args.scenario)
        return 0
    result = selftest() if args.command == 'selftest' else run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
