#!/usr/bin/env python3
"""Serpantinum v0.5.32: isolated, simulation-only durable protocol journal.

Never touches the live bundle, compositor, or systemd. The caller supplies an
explicit isolated root. No physical execution or recovery authorization.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
JOURNAL = 'recovery-protocol-v0531.json'


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def directory(root):
    if root is None:
        raise ValueError('Explicit isolated root required')
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('Unsafe isolated root')
    # Do not allow access to the live runtime root or its transaction directory.
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        live = (Path(runtime) / 'serpantinum-display-v0513').resolve()
        resolved = root.resolve()
        if resolved == live or resolved == Path(runtime).resolve() or live in resolved.parents:
            raise ValueError('Live runtime directory prohibited')
    return root


def journal_path(folder):
    return folder / JOURNAL


def read(folder):
    path = journal_path(folder)
    if path.is_symlink():
        raise ValueError('Unsafe journal symlink')
    if not path.exists():
        return None
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size > 1024 * 1024:
        raise ValueError('Unsafe journal')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != '0.5.31':
        raise ValueError('Invalid journal schema')
    state = value.get('state')
    if not isinstance(state, dict) or state.get('physicalExecutionEnabled') is not False or state.get('physicalRollbackVerified') is not False or state.get('commandsExecuted') != 0:
        raise ValueError('Unsafe protocol state')
    history = state.get('history')
    if not isinstance(history, list) or not history or history[-1] != state.get('phase') or len(history) > 32:
        raise ValueError('Invalid history')
    if state.get('phase') not in load('display-recovery-protocol.py', 'proto_read').PHASES:
        raise ValueError('Invalid protocol phase')
    return value


def init(root, token, deadline):
    bundle = load('display-recovery-bundle.py', 'bundle_init')
    proto = load('display-recovery-protocol.py', 'proto_init')
    folder = directory(root)
    with bundle.locked(folder):
        if read(folder) is not None:
            raise ValueError('Journal already exists')
        state = proto.begin(token, deadline)
        envelope = {'version': '0.5.31', 'mode': 'isolated-simulation-only', 'state': state}
        bundle.atomic_write(journal_path(folder), envelope)
        return envelope


def advance(root, token, event, now):
    bundle = load('display-recovery-bundle.py', 'bundle_advance')
    proto = load('display-recovery-protocol.py', 'proto_advance')
    folder = directory(root)
    with bundle.locked(folder):
        envelope = read(folder)
        if envelope is None:
            raise ValueError('Journal missing')
        state = proto.transition(envelope['state'], event, now, token)
        updated = {**envelope, 'state': state}
        bundle.atomic_write(journal_path(folder), updated)
        return updated


def finish(root, token, scenario):
    """Commit a fully simulated rehearsal under the shared journal lock.

    The scenario is a fixed fixture, never an arbitrary executable or callback.
    Repeating a completed finish is rejected and cannot overwrite evidence.
    """
    if scenario not in ('verified', 'operation-failure', 'incomplete', 'observer-failure'):
        raise ValueError('Unknown test scenario')
    bundle = load('display-recovery-bundle.py', 'bundle_finish')
    proto = load('display-recovery-protocol.py', 'proto_finish')
    folder = directory(root)
    with bundle.locked(folder):
        envelope = read(folder)
        if envelope is None:
            raise ValueError('Journal missing')
        state = envelope['state']
        if state.get('transactionId') != token:
            raise ValueError('Transaction identity mismatch')
        if state.get('phase') != 'rollback-running':
            raise ValueError('Recovery not running or already finished')
        fixture = proto.engine().fixture()
        saved = [dict(item) for item in fixture]
        current = [dict(item) for item in fixture]
        if scenario == 'verified':
            runner, observer = lambda _: True, lambda: current
        elif scenario == 'operation-failure':
            runner, observer = lambda _: False, lambda: current
        elif scenario == 'incomplete':
            runner, observer = lambda _: True, lambda: [dict(current[0], x=current[0]['x'] + 100), *current[1:]]
        else:
            runner = lambda _: True
            def observer():
                raise RuntimeError('Injected observer failure')
        finished = proto.finish(state, saved, current, runner, observer)
        if finished.get('physicalRollbackVerified') is not False or finished.get('commandsExecuted') != 0:
            raise ValueError('Unsafe rehearsal result')
        updated = {**envelope, 'state': finished}
        bundle.atomic_write(journal_path(folder), updated)
        return updated


def status(root):
    bundle = load('display-recovery-bundle.py', 'bundle_status')
    folder = directory(root)
    with bundle.locked(folder):
        result = read(folder)
        if result is None:
            raise ValueError('Journal missing')
        return result


def selftest():
    token = 'a' * 24
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0531-') as root:
        os.chmod(root, 0o700)
        init(root, token, 15)
        assert status(root)['state']['phase'] == 'prepared'
        try:
            init(root, token, 15)
            raise AssertionError('Duplicate initialization accepted')
        except ValueError:
            pass
        for event, now, phase in [('supervisor-ready', 1, 'supervised'), ('reserve', 2, 'operation-reserved'),
                                  ('initiator-exited', 3, 'operation-interrupted'), ('expire', 15, 'deadline-expired'),
                                  ('recover', 15, 'rollback-running')]:
            advance(root, token, event, now)
            assert status(root)['state']['phase'] == phase
        original = status(root)
        for bad_token, event, now in [('b'*24, 'recover', 16), (token, 'reserve', 16)]:
            try:
                advance(root, bad_token, event, now)
                raise AssertionError('Invalid transition accepted')
            except ValueError:
                assert status(root) == original
        for bad_token in ('b'*24,):
            try:
                finish(root, bad_token, 'verified')
                raise AssertionError('Wrong identity accepted for finish')
            except ValueError:
                assert status(root) == original
        finished = finish(root, token, 'verified')
        assert finished['state']['phase'] == 'rollback-rehearsal-verified'
        assert finished['state']['physicalRollbackVerified'] is False
        assert finished['state']['commandsExecuted'] == 0
        try:
            finish(root, token, 'verified')
            raise AssertionError('Duplicate finish accepted')
        except ValueError:
            assert status(root) == finished
        path = journal_path(Path(root))
        path.write_text('{invalid', encoding='utf-8')
        try:
            status(root)
            raise AssertionError('Corrupt journal accepted')
        except json.JSONDecodeError:
            pass
    return {'selftest': 'passed', 'phaseTransitions': 5, 'duplicateRejected': True,
            'invalidIdentityRejected': True, 'invalidTransitionRejected': True,
            'corruptionRejected': True, 'finishVerified': True, 'duplicateFinishRejected': True, 'commandsExecuted': 0,
            'physicalExecutionEnabled': False, 'systemdIntegration': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'init', 'advance', 'finish', 'status'])
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    p.add_argument('--deadline', type=float)
    p.add_argument('--event')
    p.add_argument('--now', type=float)
    p.add_argument('--scenario', choices=['verified', 'operation-failure', 'incomplete', 'observer-failure'])
    args = p.parse_args()
    if args.command == 'selftest':
        result = selftest()
    elif args.command == 'init':
        if args.token is None or args.deadline is None:
            raise ValueError('Missing token or deadline')
        result = init(args.root, args.token, args.deadline)
    elif args.command == 'advance':
        if args.token is None or args.event is None or args.now is None:
            raise ValueError('Missing transition arguments')
        result = advance(args.root, args.token, args.event, args.now)
    elif args.command == 'finish':
        if args.token is None or args.scenario is None:
            raise ValueError('Missing finish arguments')
        result = finish(args.root, args.token, args.scenario)
    else:
        result = status(args.root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalExecutionEnabled': False}), file=sys.stderr)
        sys.exit(2)
