#!/usr/bin/env python3
"""v0.5.79.5: offline atomic receipt publication regression, no systemd or display commands."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-worker-receipt-runtime-v05792.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def selftest():
    source = SOURCE.read_text()
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'worker')
    body = ast.get_source_segment(source, fn)
    assert 'os.link(staged, path, follow_symlinks=False)' in body
    assert 'os.fsync(dirfd)' in body
    assert 'os.replace(' not in body
    mod = load(SOURCE, 'runtime_05795')
    binding = load(HERE / 'display-worker-evidence-binding-v05791.py', 'binding_05795')
    token, invocation = 'a'*24, '1'*32
    results = []
    def record(name, ok):
        assert ok, name
        results.append({'scenario': name, 'passed': True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05795-') as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        old = os.environ.get('INVOCATION_ID')
        os.environ['INVOCATION_ID'] = invocation
        try:
            record('valid-publication', mod.worker(token, root) == 0)
            target = root/'receipt.json'
            receipt = json.loads(target.read_text())
            record('canonical-receipt', receipt == binding.worker_receipt(token, invocation, os.getpid()))
            record('private-permissions', target.stat().st_mode & 0o077 == 0)
            record('no-staged-leftovers', not list(root.glob('.receipt-staged-*')))
            before = target.read_bytes()
            try:
                mod.worker(token, root)
            except FileExistsError:
                record('existing-preserved', target.read_bytes() == before)
            else:
                raise AssertionError('overwrite accepted')
            record('no-staged-after-failure', not list(root.glob('.receipt-staged-*')))
            target.unlink()
            (root/'elsewhere').write_text('sentinel')
            target.symlink_to('elsewhere')
            try:
                mod.worker(token, root)
            except FileExistsError:
                record('symlink-rejected', (root/'elsewhere').read_text() == 'sentinel')
            else:
                raise AssertionError('symlink overwritten')
            target.unlink()
            # Distinct child processes contend for the same final name.
            command = [sys.executable, str(SOURCE), 'worker', '--token', token, '--root', str(root)]
            a = subprocess.Popen(command, env={**os.environ, 'INVOCATION_ID': invocation}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            b = subprocess.Popen(command, env={**os.environ, 'INVOCATION_ID': invocation}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            a.communicate(timeout=10)
            b.communicate(timeout=10)
            record('exclusive-concurrent-writers', sorted((a.returncode, b.returncode))[0] == 0 and sorted((a.returncode, b.returncode))[1] != 0)
            record('concurrent-receipt-valid', json.loads(target.read_text())['payload']['transactionId'] == token)
            record('concurrent-no-staged-leftovers', not list(root.glob('.receipt-staged-*')))
            # Source-level crash points: no final publication before staging fsync.
            record('publication-after-file-fsync', body.index('os.fsync(stream.fileno())') < body.index('os.link(staged, path'))
            record('directory-fsync-after-publication', body.index('os.link(staged, path') < body.index('os.fsync(dirfd)'))
        finally:
            if old is None:
                os.environ.pop('INVOCATION_ID', None)
            else:
                os.environ['INVOCATION_ID'] = old
    return {'version':'0.5.79.5','mode':'offline-atomic-receipt-regression','selftest':'passed',
            'totalScenarios':len(results),'results':results,'atomicPublicationImplemented':True,
            'crashDurabilityVerified':False,'systemdIntegration':False,'productionModified':False,
            'historicalIdentityVerified':False,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'physicalRollbackVerified':False,'commandsExecuted':0}

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        sys.exit('Usage: script selftest')
    print(json.dumps(selftest(), indent=2))
