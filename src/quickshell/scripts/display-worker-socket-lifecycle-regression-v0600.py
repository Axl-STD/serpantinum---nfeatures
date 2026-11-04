#!/usr/bin/env python3
"""Offline worker broker socket ownership regression; no display or systemd calls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('worker_lifecycle_target', HERE / 'display-worker-authority-v0600.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

def selftest():
    rows = []
    def check(name, ok):
        rows.append({'scenario': name, 'passed': bool(ok)})
        if not ok:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-worker-socket-') as d:
        root = Path(d) / 'broker'
        root.mkdir(mode=0o700)
        b = w.Broker(root)
        with b:
            path = b.path
            st = path.lstat()
            check('socket-created', stat.S_ISSOCK(st.st_mode))
            check('socket-private', stat.S_IMODE(st.st_mode) == 0o600)
            check('socket-owner', st.st_uid == os.getuid())
            check('identity-recorded', b._socket_identity == (st.st_dev, st.st_ino))
            check('broker-open', not b.closed)
            check('broker-listening', b.sock is not None)
            check('socket-path', path.parent == root)
            check('socket-single-path', path.name == 'worker.sock')
        check('normal-close', b.closed)
        check('normal-path-removed', not path.exists())
        check('normal-identity-cleared', b._socket_identity is None)
        check('normal-registration-cleared', not b.allowed)
        b = w.Broker(root)
        b.__enter__()
        original = b._socket_identity
        path.unlink()
        check('original-path-unlinked', not path.exists())
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            replacement.bind(str(path))
            os.chmod(path, 0o600)
            st = path.lstat()
            check('replacement-is-socket', stat.S_ISSOCK(st.st_mode))
            check('replacement-identity-differs', original != (st.st_dev, st.st_ino))
            b.__exit__(None, None, None)
            check('replacement-preserved', path.is_socket())
            check('replaced-broker-closed', b.closed)
            check('replaced-identity-cleared', b._socket_identity is None)
            check('replaced-registrations-cleared', not b.allowed)
            check('replacement-owned', path.lstat().st_uid == os.getuid())
            check('replacement-private', stat.S_IMODE(path.lstat().st_mode) == 0o600)
        finally:
            replacement.close()
            path.unlink(missing_ok=True)
        b = w.Broker(root)
        b.__enter__()
        path.unlink()
        path.write_text('unrelated')
        try:
            check('replacement-regular', path.is_file())
            b.__exit__(None, None, None)
            check('regular-file-preserved', path.read_text() == 'unrelated')
            check('regular-broker-closed', b.closed)
        finally:
            path.unlink(missing_ok=True)
        b = w.Broker(root)
        b.__enter__()
        path.unlink()
        b.__exit__(None, None, None)
        check('missing-path-close', b.closed)
        check('missing-path-stays-absent', not path.exists())
        check('missing-path-identity-cleared', b._socket_identity is None)
        with w.Broker(root) as b:
            check('reentry-after-cleanup', b.path.is_socket())
        check('reentry-cleanup', not (root / 'worker.sock').exists())
    return {'version':'0.6.0','mode':'offline-worker-socket-ownership',
            'selftest':'passed','totalScenarios':len(rows),'results':rows,
            'executionAuthorized':False,'physicalExecutionEnabled':False,
            'commandsExecuted':0,
            'limitations':['Cooperative same-UID socket ownership check, not atomic protection against hostile races',
                           'No worker lifetime authorization, physical rollback or runtime systemd proof']}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    try:
        print(json.dumps(selftest(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':repr(exc),'commandsExecuted':0}))
        raise SystemExit(2)
