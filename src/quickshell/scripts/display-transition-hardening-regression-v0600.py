#!/usr/bin/env python3
"""Offline transition integrity, interruption, and cleanup regressions."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('continuous_hardened', HERE / 'display-continuous-authority-v0600.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def selftest():
    rows = []
    def check(name, condition):
        rows.append({'scenario': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)

    def scenario(name, mutation, phase='applying', expect_success=False):
        with tempfile.TemporaryDirectory(prefix='serpantinum-transition-hardening-') as tmp:
            roots = [Path(tmp) / x for x in ('runtime', 'journal', 'broker')]
            for root in roots:
                root.mkdir(mode=0o700)
            session = mod.ContinuousSession(*roots)
            with session:
                guard = mod.base.serialized.guard
                initial = guard.prepare(roots[1], 'offline-probe', 'digest')
                def response():
                    mutation(session, guard, roots[1])
                    return {'accepted': True, 'peerPid': os.getpid(), 'operation': 'offline-probe'}
                session.broker.serve_once = response
                accepted = False
                try:
                    session.transition(os.getpid(), 1, phase)
                    accepted = True
                except (mod.ContinuityDenied, guard.GuardError, KeyboardInterrupt, InterruptedError):
                    pass
                check(name + '-decision', accepted is expect_success)
                current = guard._read(roots[1])
                check(name + '-generation', (current is not None and current['generation'] == 2) if expect_success else (current is None or current['phase'] != 'applying'))
                check(name + '-no-physical', session.report()['physicalExecutionEnabled'] is False)
                check(name + '-registration-consumed', not session.broker.allowed)
                if name == 'worker-identity-changed':
                    session.owner_starttime = mod.base.worker.starttime(os.getpid())
                if name == 'resource-replaced':
                    session.broker.path.unlink()
                    session.broker.sock.close()
                    session.broker.sock = None
                    session._resource_identities = session._capture_resources() if False else session._resource_identities
            check(name + '-socket-closed', not (roots[2] / 'worker.sock').exists())

    def noop(*_):
        pass
    scenario('baseline-accepted', noop, expect_success=True)
    scenario('revoked-after-handshake', lambda s, *_: s.revoked.add(os.getpid()))
    scenario('worker-identity-changed', lambda s, *_: setattr(s, 'owner_starttime', -1))
    scenario('journal-operation-changed', lambda s, g, root: g._write(root, dict(g._read(root), operation='other')))
    scenario('journal-digest-changed', lambda s, g, root: g._write(root, dict(g._read(root), snapshot_digest='other')))
    scenario('journal-phase-changed', lambda s, g, root: g._write(root, dict(g._read(root), phase='rollback_required')))
    scenario('journal-generation-changed', lambda s, g, root: g._write(root, dict(g._read(root), generation=5)))
    scenario('journal-removed', lambda s, g, root: (root / 'intent.json').unlink())
    scenario('handshake-interrupted', lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    scenario('handshake-io-interrupted', lambda *_: (_ for _ in ()).throw(InterruptedError()))

    # A failed liveness check at exit must still close the broker and release locks.
    with tempfile.TemporaryDirectory(prefix='serpantinum-transition-exit-') as tmp:
        roots = [Path(tmp) / x for x in ('runtime', 'journal', 'broker')]
        for root in roots:
            root.mkdir(mode=0o700)
        session = mod.ContinuousSession(*roots)
        session.__enter__()
        session.broker.path.unlink()
        try:
            session.__exit__(None, None, None)
        except mod.ContinuityDenied:
            check('corrupt-exit-fails-closed', True)
        else:
            check('corrupt-exit-fails-closed', False)
        check('corrupt-exit-inactive', not session.active)
        check('corrupt-exit-stack-closed', session._stack is None)
        check('corrupt-exit-broker-closed', session.broker is None)
        with mod.base.owner.owner_only(roots[0]):
            check('corrupt-exit-lock-released', True)

    return {'version': '0.6.0', 'mode': 'offline-transition-hardening',
            'selftest': 'passed', 'totalScenarios': len(rows), 'results': rows,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'authenticatedDelegationImplemented': False, 'commandsExecuted': 0,
            'limitations': ['Cooperative offline session only',
                            'No atomic protection against hostile same-UID writers',
                            'No physical display execution or rollback']}

if __name__ == '__main__':
    argparse.ArgumentParser().parse_args()
    print(json.dumps(selftest(), indent=2))
