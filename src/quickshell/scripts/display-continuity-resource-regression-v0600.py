#!/usr/bin/env python3
"""Offline resource identity checks for continuous authority; no display operations."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('continuous', HERE / 'display-continuous-authority-v0600.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def selftest():
    rows = []
    def check(name, condition):
        rows.append({'scenario': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-resource-boundary-') as temp:
        base = Path(temp)
        for index, name in enumerate(('runtime-lock', 'journal-lock', 'broker-socket')):
            roots = [base / (str(index) + '-' + x) for x in ('runtime', 'journal', 'broker')]
            for root in roots:
                root.mkdir(mode=0o700)
            with mod.ContinuousSession(*roots) as session:
                path = (roots[0] / mod.base.owner.NAME, roots[1] / '.intent-serialize.lock', roots[2] / 'worker.sock')[index]
                original = path.lstat()
                check(name + '-initial-live', session._live() is None)
                check(name + '-private', stat.S_IMODE(original.st_mode) == 0o600)
                check(name + '-identity-captured', len(session._resource_identities) == 3)
                path.chmod(0o644)
                try:
                    try: session._live()
                    except mod.ContinuityDenied: denied = True
                    else: denied = False
                    check(name + '-permissive-denied', denied)
                finally:
                    path.chmod(0o600)
                check(name + '-restored-after-mode', session._live() is None)
                other = path.with_name(path.name + '.held')
                path.rename(other)
                try:
                    try: session._live()
                    except mod.ContinuityDenied: denied = True
                    else: denied = False
                    check(name + '-missing-denied', denied)
                    if index != 2:
                        path.write_bytes(b'')
                        path.chmod(0o600)
                    else:
                        path.symlink_to(other)
                    try:
                        try: session._live()
                        except mod.ContinuityDenied: denied = True
                        else: denied = False
                        check(name + '-replacement-denied', denied)
                    finally:
                        path.unlink()
                finally:
                    other.rename(path)
                check(name + '-restored-after-replace', session._live() is None)
                check(name + '-inode-preserved', path.lstat().st_ino == original.st_ino)
                check(name + '-no-journal', not (roots[1] / 'intent.json').exists())
                check(name + '-no-physical', session.report()['physicalExecutionEnabled'] is False)
            check(name + '-socket-closed', not (roots[2] / 'worker.sock').exists())
    return {'version': '0.6.0', 'mode': 'offline-resource-continuity',
            'selftest': 'passed', 'totalScenarios': len(rows), 'results': rows,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'authenticatedDelegationImplemented': False, 'commandsExecuted': 0,
            'limitations': ['Path identity checks are not an atomic guarantee against same-UID races',
                            'No live-worker revocation or physical display execution',
                            'Noncooperating direct journal writers remain outside this contract']}

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))
