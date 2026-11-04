#!/usr/bin/env python3
"""Offline writer boundary: explicit supervised entrypoint and fail-closed source audit.

No display execution, no systemd integration, no claim of hostile same-UID isolation.
"""
import argparse
import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPERVISOR = 'display-transition-supervisor-v0600.py'
# Fixtures are allowed to construct synthetic journal states; never production writers.
FIXTURE_NAMES = frozenset({
 'display-transaction-fault-matrix-v0600.py',
 'display-transaction-orphan-audit-v0600.py',
 'display-transaction-integration-v0600.py',
 'display-broker-ipc-reconcile-regression-v0600.py',
 'display-authority-crash-lifecycle-v0600.py',
 'display-continuous-authority-regression-v0600.py',
 'display-transition-supervisor-regression-v0600.py',
 'display-transition-hardening-regression-v0600.py',
 'display-supervisor-crash-regression-v0600.py',
 'display-continuity-resource-regression-v0600.py',
 'display-transaction-guard-v0600.py',
 'display-transaction-serialized-v0600.py',
 'display-continuous-authority-v0600.py',
 'display-operational-writer-regression-v0600.py',
})
class BoundaryDenied(RuntimeError):
    pass

def _attr(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attr(node.value)
        return (parent + '.' if parent else '') + node.attr
    return ''

def inspect_source(path):
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise BoundaryDenied('unsafe source')
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    hits=[]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            symbol=_attr(node.func)
            if symbol.endswith('.guard.advance') or symbol.endswith('.guard.prepare') or symbol in ('guard.advance','guard.prepare','serialized.advance','serialized.prepare'):
                hits.append((node.lineno,symbol))
    return hits

def audit(root=HERE):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        raise BoundaryDenied('unsafe audit root')
    findings=[]
    checked=0
    for path in sorted(root.glob('display*.py')):
        hits=inspect_source(path)
        checked+=1
        if hits and path.name not in FIXTURE_NAMES:
            findings.extend({'file':path.name,'line':line,'call':symbol} for line,symbol in hits)
    return {'checked':checked,'unexpectedWriters':findings,'passed':not findings}

def open_supervised_session(runtime_root,journal_root,broker_root):
    source=HERE/SUPERVISOR
    if source.is_symlink() or not source.is_file():
        raise BoundaryDenied('unsafe supervisor')
    spec=importlib.util.spec_from_file_location('display_writer_supervisor_v0600',source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SupervisedSession(runtime_root,journal_root,broker_root)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('audit',))
    args=parser.parse_args()
    result=audit()
    payload={'version':'0.6.0','mode':'offline-writer-boundary','selftest':'passed' if result['passed'] else 'failed',
             'filesChecked':result['checked'],'unexpectedWriters':result['unexpectedWriters'],
             'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,
             'universalRuntimeExclusionVerified':False}
    print(json.dumps(payload,indent=2))
    return 0 if result['passed'] else 2
if __name__=='__main__':
    raise SystemExit(main())
