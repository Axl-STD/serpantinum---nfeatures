#!/usr/bin/env python3
"""Serpantinum v0.5.12: read-only preflight + simulated systemd watchdog.

Never changes outputs or persistent configuration. Snapshot equivalence is not rollback.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def module(filename, name):
    path = HERE / filename
    if not path.is_file():
        raise ValueError('Missing dependency: ' + filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def invoke(watchdog, action, *args):
    cmd = [sys.executable, str(watchdog), action, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise ValueError('Watchdog ' + action + ' failed: ' + (result.stderr or result.stdout)[-600:])
    response = json.loads(result.stdout)
    if not isinstance(response, dict):
        raise ValueError('Invalid watchdog response')
    return response


def start(config, seconds, prepare=None, run=None):
    if not 5 <= seconds <= 120:
        raise ValueError('Timeout must be 5..120 seconds')
    if prepare is None:
        prepare = module('display-preflight-coordinator.py', 'display_preflight').preflight
    if run is None:
        watchdog = HERE / 'display-recovery-watchdog.py'
        if not watchdog.is_file():
            raise ValueError('Missing v0.5.8 watchdog')
        run = lambda action, *args: invoke(watchdog, action, *args)
    prepared = prepare(config)
    if prepared.get('phase') != 'prepared-read-only' or not prepared.get('structurallyValid') or not prepared.get('nativeConfigParserAccepted') or prepared.get('safeToExecuteAutomatically') is not False:
        raise ValueError('Preflight rejected transaction')
    pending = run('start', '--seconds', str(seconds))
    if pending.get('phase') != 'simulated-pending' or pending.get('pending') is not True or pending.get('watchdogReady') is not True or not pending.get('id') or not pending.get('unit'):
        raise ValueError('Watchdog not ready; no physical operations are permitted')
    return {'schemaVersion': 1, 'version': '0.5.12', 'mode': 'simulation-only',
            'phase': 'simulated-pending', 'transactionId': pending['id'],
            'unit': pending['unit'], 'deadline': pending.get('deadline'),
            'preflightPassed': True, 'watchdogReady': True,
            'commandsExecuted': 0, 'physicalOutputsChanged': False,
            'persistentConfigChanged': False, 'physicalRollbackVerified': False,
            'note': 'No physical apply or rollback is implemented'}


def selftest():
    def good(config):
        return {'phase':'prepared-read-only','structurallyValid':True,
                'nativeConfigParserAccepted':True,'safeToExecuteAutomatically':False}
    def runner(action, *args):
        assert action == 'start'
        return {'phase':'simulated-pending','pending':True,'watchdogReady':True,
                'id':'test-id','unit':'test-unit'}
    result = start(Path('/nonexistent'), 15, good, runner)
    assert result['preflightPassed'] and result['commandsExecuted'] == 0
    for bad in [dict(good(None), structurallyValid=False), dict(good(None), nativeConfigParserAccepted=False), dict(good(None), safeToExecuteAutomatically=True)]:
        try:
            start(Path('/nonexistent'), 15, lambda c: bad, runner)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe preflight accepted')
    try:
        start(Path('/nonexistent'), 15, good, lambda a,*x: {'pending':True,'watchdogReady':False})
    except ValueError:
        pass
    else:
        raise AssertionError('Unready watchdog accepted')
    return {'selftest':'passed','preflightFailClosed':True,'watchdogReadinessRequired':True,
            'commandsExecuted':0,'physicalOutputsChanged':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest','start','status','confirm'])
    p.add_argument('--config', type=Path, default=Path.home()/'.config/hypr/monitors.conf')
    p.add_argument('--seconds', type=int, default=15)
    p.add_argument('--id')
    args = p.parse_args()
    try:
        if args.command == 'selftest':
            response = selftest()
        elif args.command == 'start':
            response = start(args.config, args.seconds)
        else:
            watchdog = HERE / 'display-recovery-watchdog.py'
            if not watchdog.is_file():
                raise ValueError('Missing watchdog')
            if args.command == 'confirm':
                if not args.id:
                    raise ValueError('--id is required')
                state = invoke(watchdog, 'confirm', '--id', args.id)
            else:
                state = invoke(watchdog, 'status')
            response = {'version':'0.5.12','mode':'simulation-only','phase':state.get('phase'),
                        'pending':state.get('pending'), 'transactionId':state.get('id'),
                        'watchdogReady':state.get('watchdogReady'),
                        'systemdActive':state.get('systemdActive'),
                        'equivalentObservableState':state.get('equivalentObservableState'),
                        'differences':state.get('differences',[]),
                        'physicalRollbackVerified':False,'commandsExecuted':0,
                        'physicalOutputsChanged':False,'persistentConfigChanged':False}
        print(json.dumps(response, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError, AssertionError) as exc:
        print(json.dumps({'version':'0.5.12','error':str(exc),'safeToExecuteAutomatically':False,
                          'physicalRollbackVerified':False,'commandsExecuted':0}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
