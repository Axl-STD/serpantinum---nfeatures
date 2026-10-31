#!/usr/bin/env python3
"""v0.5.79.7: read-only classification of isolated staged worker receipts.

No cleanup, publication, systemd query, compositor access, or physical execution.
A valid staged receipt is NEVER evidence that the final name was published.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
STAGED = re.compile(r'\.receipt-staged-[A-Za-z0-9_-]+\Z')
MAX_BYTES = 1024 * 1024
SAFE_FALSE = ('historicalIdentityVerified', 'workerIdentityVerified',
              'operationCompletionVerified', 'finalizationAuthorized',
              'executionAuthorized', 'physicalExecutionEnabled',
              'physicalRollbackVerified')


def binding_module():
    spec = importlib.util.spec_from_file_location('binding_05797', HERE / 'display-worker-evidence-binding-v05791.py')
    if spec is None or spec.loader is None:
        raise ValueError('Binding dependency unavailable')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_entry(dirfd, name):
    """Read a bounded regular file relative to a validated directory descriptor."""
    if name != 'receipt.json' and not STAGED.fullmatch(name):
        raise ValueError('Unexpected evidence name')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_nlink != 1
                or before.st_size > MAX_BYTES):
            raise ValueError('Unsafe evidence entry')
        raw = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) > MAX_BYTES:
            raise ValueError('Oversized evidence entry')
        if (len(raw) != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_ctime_ns != before.st_ctime_ns
                or after.st_ino != before.st_ino or after.st_dev != before.st_dev
                or after.st_nlink != before.st_nlink):
            raise ValueError('Unstable evidence entry')
        return raw
    finally:
        os.close(fd)


def classify(root, token, worker_state='unknown', *, expected_metadata=None):
    result = dict(version='0.5.79.8.1', mode='read-only-orphaned-receipt-classification',
                  assessment='rejected', stagedCount=0, finalPresent=False,
                  receiptMetadataConsistent=False, workerState=worker_state,
                  **{key: False for key in SAFE_FALSE}, commandsExecuted=0)
    dirfd = None
    try:
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction token')
        if worker_state not in ('confirmed-exited', 'active', 'unknown'):
            raise ValueError('Invalid worker state')
        folder = Path(root)
        # A directory descriptor anchors enumeration and every subsequent open.
        dirfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        initial = os.fstat(dirfd)
        if (not stat.S_ISDIR(initial.st_mode) or initial.st_uid != os.getuid()
                or initial.st_mode & 0o077):
            raise ValueError('Unsafe evidence directory')
        # Resolve the opened directory, not a second pathname lookup.
        opened = Path(os.readlink(f'/proc/self/fd/{dirfd}')).resolve()
        if not opened.is_absolute() or not opened.exists():
            raise ValueError('Unsafe evidence directory')
        runtime = os.environ.get('XDG_RUNTIME_DIR')
        if runtime:
            live = (Path(runtime) / 'serpantinum-display-v0513').resolve()
            if opened == live or live in opened.parents:
                raise ValueError('Live display state forbidden')
        names = sorted(os.listdir(dirfd))
        staged = [name for name in names if name.startswith('.receipt-staged-')]
        if any(not STAGED.fullmatch(name) for name in staged):
            raise ValueError('Unexpected staged filename')
        result['stagedCount'] = len(staged)
        result['finalPresent'] = 'receipt.json' in names
        raw = {name: read_entry(dirfd, name) for name in staged + (['receipt.json'] if result['finalPresent'] else [])}
        # Reject directory replacement and changes to the evidence name set.
        current = os.stat(folder, follow_symlinks=False)
        if (not stat.S_ISDIR(current.st_mode) or current.st_ino != initial.st_ino
                or current.st_dev != initial.st_dev or sorted(os.listdir(dirfd)) != names):
            raise ValueError('Unstable evidence directory')
        binding = binding_module()
        def valid(data):
            try:
                value = json.loads(data.decode('utf-8'))
                if not isinstance(value, dict) or set(value) != {'payload', 'sha256'}:
                    return False
                payload = value['payload']
                if not isinstance(payload, dict) or set(payload) != {'version', 'transactionId', 'invocationId', 'workerPid', 'outcome', 'physicalExecutionEnabled', 'commandsExecuted'}:
                    return False
                if payload['transactionId'] != token:
                    return False
                expected = binding.worker_receipt(token, payload['invocationId'], payload['workerPid'])
                return value == expected
            except (ValueError, TypeError, KeyError, UnicodeError):
                return False
        # Bind the SAME descriptor-anchored byte snapshot to historical metadata.
        # A staged receipt remains unpublished even when its identity matches.
        if expected_metadata is not None and worker_state == 'confirmed-exited':
            if (not isinstance(expected_metadata, dict)
                    or expected_metadata.get('transactionId') != token
                    or expected_metadata.get('systemdExecutionMetadataConsistent') is not True
                    or any(expected_metadata.get(key) is not False for key in SAFE_FALSE)
                    or expected_metadata.get('commandsExecuted') != 0):
                raise ValueError('Unverified historical metadata')
            for name, data in raw.items():
                if not valid(data):
                    continue  # Existing incomplete/invalid evidence classification wins.
                receipt = json.loads(data.decode('utf-8'))
                linked = binding.assess(token, expected_metadata, receipt)
                if linked.get('receiptMetadataConsistent') is not True:
                    result['assessment'] = 'conflicting-evidence'
                    return result
            result['receiptMetadataConsistent'] = (bool(raw) and all(valid(data) for data in raw.values())
                                                   and len(set(raw.values())) == 1)
        if worker_state != 'confirmed-exited':
            result['assessment'] = 'worker-state-uncertain'
        elif not staged:
            result['assessment'] = 'final-only-unverified' if result['finalPresent'] else 'no-receipt-evidence'
        elif any(not valid(raw[name]) for name in staged):
            result['assessment'] = 'incomplete-staged-evidence'
        elif not result['finalPresent']:
            result['assessment'] = 'unpublished-staged-evidence' if len(set(raw[name] for name in staged)) == 1 else 'conflicting-evidence'
        elif not valid(raw['receipt.json']):
            result['assessment'] = 'conflicting-evidence'
        elif all(raw[name] == raw['receipt.json'] for name in staged):
            result['assessment'] = 'published-with-staged-remnant'
        else:
            result['assessment'] = 'conflicting-evidence'
    except (OSError, ValueError, TypeError) as exc:
        result['assessment'] = ('unsafe-file-entry' if isinstance(exc, OSError)
                                or any(word in str(exc) for word in ('Unsafe', 'Unstable', 'Unexpected staged'))
                                else 'rejected')
        result['reason'] = str(exc)[:160]
    finally:
        if dirfd is not None:
            os.close(dirfd)
    return result


def selftest():
    binding = binding_module()
    token = 'a' * 24
    receipt = binding.canonical(binding.worker_receipt(token, '1' * 32, 12345))
    other = binding.canonical(binding.worker_receipt(token, '2' * 32, 12345))
    cases = [
        ('empty', {}, 'no-receipt-evidence', 'confirmed-exited'),
        ('final-only', {'receipt.json': receipt}, 'final-only-unverified', 'confirmed-exited'),
        ('partial', {'.receipt-staged-abc': receipt[:12]}, 'incomplete-staged-evidence', 'confirmed-exited'),
        ('complete-unpublished', {'.receipt-staged-abc': receipt}, 'unpublished-staged-evidence', 'confirmed-exited'),
        ('matching-final', {'.receipt-staged-abc': receipt, 'receipt.json': receipt}, 'published-with-staged-remnant', 'confirmed-exited'),
        ('contradictory', {'.receipt-staged-abc': other, 'receipt.json': receipt}, 'conflicting-evidence', 'confirmed-exited'),
        ('multiple-matching', {'.receipt-staged-abc': receipt, '.receipt-staged-def': receipt, 'receipt.json': receipt}, 'published-with-staged-remnant', 'confirmed-exited'),
        ('multiple-conflicting', {'.receipt-staged-abc': receipt, '.receipt-staged-def': other}, 'conflicting-evidence', 'confirmed-exited'),
        ('wrong-token', {'.receipt-staged-abc': binding.canonical(binding.worker_receipt('b'*24, '1'*32, 12345))}, 'incomplete-staged-evidence', 'confirmed-exited'),
        ('active-worker', {'.receipt-staged-abc': receipt}, 'worker-state-uncertain', 'active'),
        ('unknown-worker', {'.receipt-staged-abc': receipt}, 'worker-state-uncertain', 'unknown'),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05797-') as temp:
        base = Path(temp)
        for name, files, expected, state in cases:
            folder = base / name
            folder.mkdir(mode=0o700)
            for filename, data in files.items():
                path = folder / filename
                path.write_bytes(data)
                path.chmod(0o600)
            before = {f: (folder / f).read_bytes() for f in files}
            output = classify(folder, token, state)
            assert output['assessment'] == expected, (name, output)
            assert all(output[k] is False for k in SAFE_FALSE) and output['commandsExecuted'] == 0
            assert before == {f: (folder / f).read_bytes() for f in files}, name
            results.append(dict(scenario=name, passed=True))
        for name, kind in [('symlink', 'symlink'), ('world-readable', 'mode'), ('hardlink', 'hardlink')]:
            folder = base / name
            folder.mkdir(mode=0o700)
            path = folder / '.receipt-staged-abc'
            if kind == 'symlink':
                path.symlink_to(base / 'missing')
            elif kind == 'mode':
                path.write_bytes(receipt)
                path.chmod(0o644)
            else:
                original = folder / 'original'
                original.write_bytes(receipt)
                original.chmod(0o600)
                os.link(original, path)
            output = classify(folder, token, 'confirmed-exited')
            assert output['assessment'] == 'unsafe-file-entry', (name, output)
            results.append(dict(scenario=name, passed=True))
    return dict(version='0.5.79.7', mode='offline-read-only-orphaned-receipt',
                selftest='passed', totalScenarios=len(results), results=results,
                productionModified=False, systemdIntegration=False,
                automaticCleanup=False, receiptMetadataConsistent=False,
                **{key: False for key in SAFE_FALSE}, commandsExecuted=0,
                limitations=['Read-only classification of isolated test directories',
                             'No systemd identity or independent receipt authentication',
                             'No power-loss durability or physical rollback proof'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))


if __name__ == '__main__':
    main()
