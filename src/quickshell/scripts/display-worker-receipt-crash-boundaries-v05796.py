#!/usr/bin/env python3
"""v0.5.79.6: source-bound process interruption at atomic receipt boundaries.

Isolated Python children only; no systemd, compositor, or physical configuration.
"""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-worker-receipt-runtime-v05792.py'
BINDING = HERE / 'display-worker-evidence-binding-v05791.py'
TOKEN = 'a' * 24
INVOCATION = '1' * 32

# Each marker is a unique statement in the production worker function.
BOUNDARIES = [
    ('before-stage', 'fd, staged = tempfile.mkstemp(', False, False),
    ('after-stage', 'os.fchmod(fd, 0o600)', False, True),
    ('during-temporary-write', 'stream.flush()', False, True),
    ('after-file-fsync', '# Atomic no-replace publication', False, True),
    ('before-link', 'os.link(staged, path, follow_symlinks=False)', False, True),
    ('after-link', 'dirfd = os.open(folder,', True, True),
    ('after-directory-fsync', 'os.close(dirfd)', True, True),
    ('after-unlink', 'return 0', True, False),
]


def instrument(source, marker, exit_code):
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'worker')
    matches = [i for i in range(worker.lineno - 1, worker.end_lineno)
               if marker in lines[i]]
    if len(matches) != 1:
        raise AssertionError('Missing/ambiguous source boundary: ' + marker)
    i = matches[0]
    indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    lines.insert(i, indent + 'os._exit(' + str(exit_code) + ')\n')
    ast.parse(''.join(lines))
    return ''.join(lines)


def load_binding():
    spec = importlib.util.spec_from_file_location('v05796_binding', BINDING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def selftest():
    source = SOURCE.read_text(encoding='utf-8')
    binding = load_binding()
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05796-') as temp:
        base = Path(temp)
        os.chmod(base, 0o700)
        for index, (name, marker, final_expected, staged_expected) in enumerate(BOUNDARIES):
            folder = base / name
            folder.mkdir(mode=0o700)
            script = folder / 'instrumented.py'
            script.write_text(instrument(source, marker, 80 + index), encoding='utf-8')
            shutil.copyfile(BINDING, folder / BINDING.name)
            env = dict(os.environ, INVOCATION_ID=INVOCATION)
            child = subprocess.run([sys.executable, str(script), 'worker', '--token', TOKEN,
                                    '--root', str(folder)], env=env, capture_output=True,
                                   text=True, timeout=12)
            final = folder / 'receipt.json'
            staged = list(folder.glob('.receipt-staged-*'))
            if final.exists():
                receipt = json.loads(final.read_text(encoding='utf-8'))
                valid = receipt == binding.worker_receipt(TOKEN, INVOCATION, child_pid(receipt))
            else:
                valid = False
            ok = (child.returncode == 80 + index and final.exists() == final_expected
                  and bool(staged) == staged_expected and valid == final_expected)
            if not ok:
                raise AssertionError(f'{name}: code={child.returncode} final={final.exists()} staged={len(staged)} valid={valid} stderr={child.stderr[-400:]}')
            results.append({'scenario': name, 'passed': True, 'exitCode': child.returncode,
                            'finalPresent': final_expected, 'stagedPresent': staged_expected})
        # Existing final name cannot be replaced by another complete publication.
        folder = base / 'existing'
        folder.mkdir(mode=0o700)
        target = folder / 'receipt.json'
        target.write_bytes(b'previous-evidence')
        env = dict(os.environ, INVOCATION_ID=INVOCATION)
        child = subprocess.run([sys.executable, str(SOURCE), 'worker', '--token', TOKEN,
                                '--root', str(folder)], env=env, capture_output=True,
                               text=True, timeout=12)
        if child.returncode == 0 or target.read_bytes() != b'previous-evidence':
            raise AssertionError('Existing receipt overwritten')
        results.append({'scenario': 'existing-final-preserved', 'passed': True})
    return {'version': '0.5.79.6', 'mode': 'source-bound-process-crash-boundaries',
            'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
            'finalPartialObserved': False, 'crashDurabilityVerified': False,
            'systemdIntegration': False, 'productionModified': False,
            'historicalIdentityVerified': False, 'workerIdentityVerified': False,
            'operationCompletionVerified': False, 'finalizationAuthorized': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'limitations': ['Injected process exits in a source-derived copy, not power-loss testing',
                            'Staged files can remain after a hard process exit',
                            'No systemd or compositor commands executed']}


def child_pid(receipt):
    return receipt['payload']['workerPid']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))


if __name__ == '__main__':
    main()
