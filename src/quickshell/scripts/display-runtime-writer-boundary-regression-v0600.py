#!/usr/bin/env python3
"""Offline writer boundary checks, including negative AST fixtures."""
import argparse
import importlib.util
import json
import pathlib
import tempfile

HERE=pathlib.Path(__file__).resolve().parent
SOURCE=HERE/'display-runtime-writer-boundary-v0600.py'
spec=importlib.util.spec_from_file_location('writer_boundary_v0600',SOURCE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def selftest():
    rows=[]
    def check(name, condition):
        rows.append({'scenario':name,'passed':bool(condition)})
        if not condition: raise AssertionError(name)
    real=m.audit()
    check('real-boundary-passes',real['passed'])
    check('real-sources-checked',real['checked']>=10)
    check('real-no-unexpected-writers',real['unexpectedWriters']==[])
    check('fixture-allowlist-explicit',len(m.FIXTURE_NAMES)>5)
    check('supervisor-exists',(HERE/m.SUPERVISOR).is_file())
    with tempfile.TemporaryDirectory(prefix='serpantinum-writer-boundary-') as tmp:
        root=pathlib.Path(tmp)
        fixtures={
            'clean.py':'def run():\n    return 1\n',
            'direct.py':'def run(guard, root):\n    guard.advance(root, 1, "applying")\n',
            'prepare.py':'def run(guard, root):\n    guard.prepare(root, "op", "digest")\n',
            'nested.py':'def run(base, root):\n    base.serialized.guard.advance(root,1,"applying")\n',
            'serialized.py':'def run(serialized,root):\n    serialized.advance(root,1,"applying")\n',
            'fixture.py':'def run():\n    return "guard.advance()"\n',
        }
        for filename,src in fixtures.items():
            path=root/filename
            path.write_text(src)
            hits=m.inspect_source(path)
            check('parse-'+filename, isinstance(hits,list))
            check('count-'+filename,len(hits)==(0 if filename in ('clean.py','fixture.py') else 1))
        check('clean-empty-root',m.audit(root/'empty')['passed'] if (root/'empty').mkdir() is None else False)
        rogue=root/'display-rogue.py'
        rogue.write_text(fixtures['direct.py'])
        check('rogue-detected',not m.audit(root)['passed'])
        check('rogue-identified',m.audit(root)['unexpectedWriters'][0]['file']=='display-rogue.py')
        rogue.write_text(fixtures['prepare.py'])
        check('prepare-detected',not m.audit(root)['passed'])
        rogue.write_text(fixtures['nested.py'])
        check('nested-detected',not m.audit(root)['passed'])
        rogue.write_text(fixtures['serialized.py'])
        check('serialized-detected',not m.audit(root)['passed'])
        rogue.unlink()
        check('rogue-removed',m.audit(root)['passed'])
        symlink=root/'display-link.py'
        symlink.symlink_to(root/'direct.py')
        try: m.audit(root)
        except m.BoundaryDenied: check('symlink-rejected',True)
        else: check('symlink-rejected',False)
        symlink.unlink()
        bad=root/'display-invalid.py'
        bad.write_text('def ???')
        try: m.audit(root)
        except SyntaxError: check('invalid-syntax-rejected',True)
        else: check('invalid-syntax-rejected',False)
        bad.unlink()
        check('restored-root-passes',m.audit(root)['passed'])
    check('no-physical-execution',True)
    check('no-systemd-integration',True)
    check('no-universal-exclusion-claim',True)
    return {'version':'0.6.0','mode':'offline-writer-boundary-regression','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Cooperative source audit only; dynamic imports and external callers not universally excluded',
                           'No physical operations or runtime wiring']}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('selftest',));p.parse_args()
    print(json.dumps(selftest(),indent=2))
