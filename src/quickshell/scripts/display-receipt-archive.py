#!/usr/bin/env python3
"""Serpantinum v0.5.20: archive completed SIMULATION cleanup receipts.

Never runs hyprctl, changes displays, or claims physical recovery. Archive is
persistent, while the active receipt lives under XDG_RUNTIME_DIR. Fail closed.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_private_dir(path, create=False):
    if path.is_symlink():
        raise ValueError('Unsafe archive directory symlink')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('Unsafe archive directory permissions')


def unit_inactive(state, test_mode=False):
    token = state.get('transactionId')
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction ID')
    unit = 'serpantinum-display-v0514-' + token + '.service'
    if state.get('unit') != unit:
        raise ValueError('Watchdog unit identity mismatch')
    if test_mode:
        return
    proc = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                          capture_output=True, text=True, timeout=6)
    if proc.returncode != 0 or 'ActiveState=inactive' not in proc.stdout.splitlines():
        raise ValueError('Watchdog unit not verifiably inactive')


def checked_receipt(folder, bundle, watchdog, cleanup):
    state = watchdog.read_state(folder)
    if state.get('pending') is not False or state.get('phase') not in cleanup.TERMINAL:
        raise ValueError('Transaction is not a completed simulation')
    if bundle.read_bundle(folder) is not None:
        raise ValueError('Sealed bundle still exists')
    receipt = cleanup.read_receipt(folder, bundle)
    if receipt is None or receipt.get('phase') != 'completed-cleaned':
        raise ValueError('No committed cleanup receipt')
    if receipt.get('transactionId') != state.get('transactionId'):
        raise ValueError('Receipt transaction mismatch')
    if receipt.get('stateHash') != cleanup.state_hash(state, bundle):
        raise ValueError('Receipt does not match watchdog state')
    if receipt.get('physicalRollbackVerified') is not False:
        raise ValueError('Unexpected physical rollback claim')
    return state, receipt


def read_private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 65536:
            raise ValueError('Unsafe receipt file')
        return stream.read()


def archive(root=None, archive_dir=None, test_mode=False, fault=None):
    bundle = load('display-recovery-bundle.py', 'bundle_0520')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_0520')
    cleanup = load('display-authorized-cleanup.py', 'cleanup_0520')
    folder = bundle.runtime_dir(root)
    destination = Path(archive_dir) if archive_dir is not None else Path.home() / 'Proyectos/serpantinum-display-history'
    # Do not create or remove anything until the transaction is checked.
    with bundle.locked(folder):
        state, receipt = checked_receipt(folder, bundle, watchdog, cleanup)
        unit_inactive(state, test_mode)
        token = receipt['transactionId']
        source = cleanup.receipt_path(folder)
        raw = read_private_file(source)
        if json.loads(raw) != receipt:
            raise ValueError('Receipt changed while reading')
        check_private_dir(destination, create=True)
        target = destination / (token + '-cleanup-receipt.json')
        if target.is_symlink():
            raise ValueError('Unsafe archive target symlink')
        if target.exists():
            if read_private_file(target) != raw:
                raise ValueError('Existing archive differs; refusing overwrite')
            already = True
        else:
            already = False
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if fault == 'after-write':
                raise RuntimeError('Injected interruption after archive write')
            if read_private_file(target) != raw:
                raise ValueError('Archive verification failed')
            dirfd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        if read_private_file(target) != raw:
            raise ValueError('Archive verification failed')
        if fault == 'before-unlink':
            raise RuntimeError('Injected interruption before active receipt removal')
        # Both the verification and removal occur under the existing shared lock.
        source.unlink()
        dirfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        return {'version': '0.5.20', 'phase': 'receipt-archived', 'transactionId': token,
                'archivePath': str(target), 'archiveSha256': hashlib.sha256(raw).hexdigest(),
                'archiveReused': already, 'activeReceiptRemoved': True,
                'physicalOutputsChanged': False, 'physicalRollbackVerified': False,
                'commandsExecuted': 0}


def selftest():
    bundle = load('display-recovery-bundle.py', 'bundle_test_0520')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_test_0520')
    cleanup = load('display-authorized-cleanup.py', 'cleanup_test_0520')
    approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
    sample = {'outputs': [{'name': 'eDP-2', 'width': 1920}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'runtime'
        root.mkdir()
        dest = Path(tmp) / 'history'
        bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)
        folder = bundle.runtime_dir(root)
        with bundle.locked(folder):
            token = bundle.read_bundle(folder)['id']
            state = {'transactionId': token, 'phase': 'snapshot-equivalent', 'pending': False,
                     'unit': 'serpantinum-display-v0514-' + token + '.service'}
            bundle.atomic_write(watchdog.state_path(folder), state)
        cleanup.cleanup(root)
        try:
            archive(root, dest, True, 'after-write')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Injected interruption did not trigger')
        assert cleanup.receipt_path(folder).exists()
        result = archive(root, dest, True)
        assert result['archiveReused'] and not cleanup.receipt_path(folder).exists()
        assert read_private_file(Path(result['archivePath']))
        # A new transaction is possible once the receipt has been archived.
        assert bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)['phase'] == 'sealed-read-only'
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'runtime'
        root.mkdir()
        dest = Path(tmp) / 'history'
        bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: sample)
        folder = bundle.runtime_dir(root)
        try:
            archive(root, dest, True)
        except ValueError:
            pass
        else:
            raise AssertionError('Pending or uncleaned transaction archived')
    return {'selftest': 'passed', 'interruptionResumable': True,
            'existingArchiveNeverOverwritten': True, 'uncommittedRejected': True,
            'commandsExecuted': 0, 'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['archive', 'selftest'])
    args = parser.parse_args()
    try:
        data = selftest() if args.command == 'selftest' else archive()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
