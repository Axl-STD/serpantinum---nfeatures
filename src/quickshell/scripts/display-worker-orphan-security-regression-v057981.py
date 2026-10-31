#!/usr/bin/env python3
"""v0.5.79.8.1: isolated negative audit for orphan receipt descriptor reads."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-worker-orphan-evidence-v05797.py'
spec = importlib.util.spec_from_file_location('orphan_security_v057981', SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TOKEN = 'a' * 24


def selftest():
    binding = mod.binding_module()
    content = binding.canonical(binding.worker_receipt(TOKEN, '1' * 32, 12345))
    results = []
    def check(name, actual, expected='unsafe-file-entry'):
        assert actual['assessment'] == expected, (name, actual)
        assert actual['commandsExecuted'] == 0
        assert actual['executionAuthorized'] is False
        assert actual['physicalRollbackVerified'] is False
        results.append({'scenario': name, 'passed': True})

    with tempfile.TemporaryDirectory(prefix='serpantinum-v057981-') as tmp:
        base = Path(tmp)
        def folder(name):
            path = base / name
            path.mkdir(mode=0o700)
            return path
        def write(path, data=content):
            path.write_bytes(data)
            path.chmod(0o600)
        d = folder('ordinary')
        write(d / '.receipt-staged-abc')
        check('ordinary-preserved', mod.classify(d, TOKEN, 'confirmed-exited'), 'unpublished-staged-evidence')
        check('active-uncertain', mod.classify(d, TOKEN, 'active'), 'worker-state-uncertain')
        check('unknown-uncertain', mod.classify(d, TOKEN, 'unknown'), 'worker-state-uncertain')
        check('invalid-token', mod.classify(d, 'bad', 'confirmed-exited'), 'rejected')

        symlink = base / 'directory-link'
        symlink.symlink_to(d, target_is_directory=True)
        check('directory-symlink', mod.classify(symlink, TOKEN, 'confirmed-exited'))
        loose = folder('loose')
        loose.chmod(0o755)
        check('unsafe-directory-permissions', mod.classify(loose, TOKEN, 'confirmed-exited'))

        fifo = folder('fifo')
        os.mkfifo(fifo / '.receipt-staged-abc', 0o600)
        check('fifo-nonblocking', mod.classify(fifo, TOKEN, 'confirmed-exited'))
        oversized = folder('oversized')
        with (oversized / '.receipt-staged-abc').open('wb') as out:
            out.truncate(mod.MAX_BYTES + 1)
        check('oversized-file', mod.classify(oversized, TOKEN, 'confirmed-exited'))

        # A concurrent modification during read must not yield a positive classification.
        changing = folder('changing')
        target = changing / '.receipt-staged-abc'
        write(target)
        original_read = mod.os.read
        def changed(fd, count):
            data = original_read(fd, count)
            with target.open('ab') as stream:
                stream.write(b'x')
            return data
        with patch.object(mod.os, 'read', side_effect=changed):
            check('concurrent-file-mutation', mod.classify(changing, TOKEN, 'confirmed-exited'))

        # Swap the directory pathname after enumeration. Reads remain descriptor-bound,
        # and the final path identity check must reject the changed pathname.
        swapped = folder('swapped')
        write(swapped / '.receipt-staged-abc')
        original_entry = mod.read_entry
        moved = base / 'moved-original'
        invoked = [False]
        def swap(fd, name):
            if not invoked[0]:
                invoked[0] = True
                swapped.rename(moved)
                swapped.mkdir(mode=0o700)
                write(swapped / '.receipt-staged-abc', b'other')
            return original_entry(fd, name)
        with patch.object(mod, 'read_entry', side_effect=swap):
            check('directory-path-replacement', mod.classify(swapped, TOKEN, 'confirmed-exited'))
        assert (moved / '.receipt-staged-abc').read_bytes() == content
        assert (swapped / '.receipt-staged-abc').read_bytes() == b'other'

        # Replacing the name set mid-read must be rejected.
        changed_dir = folder('changed-dir')
        write(changed_dir / '.receipt-staged-abc')
        invoked = [False]
        def add_name(fd, name):
            if not invoked[0]:
                invoked[0] = True
                write(changed_dir / '.receipt-staged-def')
            return original_entry(fd, name)
        with patch.object(mod, 'read_entry', side_effect=add_name):
            check('directory-entry-added', mod.classify(changed_dir, TOKEN, 'confirmed-exited'))

    return {'version': '0.5.79.8.1', 'mode': 'offline-descriptor-anchored-negative-audit',
            'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
            'productionModified': False, 'systemdIntegration': False,
            'automaticCleanup': False, 'historicalIdentityVerified': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'limitations': ['Simulated file and directory races; not an atomic filesystem snapshot',
                            'No power-loss durability or independent worker authentication']}



if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['selftest']:
        raise SystemExit('Expected selftest')
    print(json.dumps(selftest(), indent=2))
