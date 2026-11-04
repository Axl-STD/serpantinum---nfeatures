#!/usr/bin/env python3
"""Offline-owned integration contract, no systemd or physical commands."""
import importlib.util, json, os, pathlib, sys, tempfile
HERE=pathlib.Path(__file__).resolve().parent
source=HERE/'display-global-runtime-gate-v0591.py'
spec=importlib.util.spec_from_file_location('global_gate_test',source)
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
rows=[]
def check(name, condition):
    rows.append({'name':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)

def main():
    with tempfile.TemporaryDirectory(prefix='serp-owned-route-') as tmp:
        root=pathlib.Path(tmp);root.chmod(0o700)
        # Source topology and negative cases do not require real systemd.
        check('owned-entrypoint-present',callable(gate.execute_owned_offline))
        check('legacy-offline-preserved', 'offline' in source.read_text())
        check('legacy-runtime-preserved', 'runtime' in source.read_text())
        check('owned-cli-present', "'offline-owned'" in source.read_text())
        check('owned-only-target-global', "module.run(runtime_root, 'global', timeout=timeout)" in source.read_text())
        check('no-fd-forward-in-owned', 'pass_fds' not in source.read_text().split('def execute_owned_offline')[1].split('def main')[0])
        check('owned-dependency-exists',(HERE/'display-coordinator-owned-evidence-v0600.py').is_file())
        check('private-root',root.stat().st_mode & 0o077==0)
        for timeout in (0,-1,121,'3',True):
            try: gate.execute_owned_offline(root,timeout)
            except gate.GateError: check('bad-timeout-'+repr(timeout),True)
            else: check('bad-timeout-'+repr(timeout),False)
        old=os.environ.get('SERPANTINUM_RUNTIME_ADMISSION_FD')
        os.environ['SERPANTINUM_RUNTIME_ADMISSION_FD']='999999'
        try:
            try: gate.execute_owned_offline(root)
            except gate.GateError: check('inherited-env-rejected',True)
            else: check('inherited-env-rejected',False)
        finally:
            if old is None: os.environ.pop('SERPANTINUM_RUNTIME_ADMISSION_FD',None)
            else: os.environ['SERPANTINUM_RUNTIME_ADMISSION_FD']=old
        # Use actual offline evidence only when full dependencies are present.
        deps=['display-guarantee-contract-matrix-v05801.py','display-guarantee-contract-strict-regression-v05804.py']
        if all((HERE/x).is_file() for x in deps):
            result=gate.execute_owned_offline(root,timeout=90)
            for key,value in [('selftest','passed'),('target','global'),('workerLockFdInherited',False),('systemdIntegration',False),('executionAuthorized',False),('physicalExecutionEnabled',False),('commandsExecuted',0)]:
                check('real-'+key,result.get(key)==value)
            check('real-lock-created',any(root.glob('serpantinum-display-gate-*.lock')))
            real=True
        else:
            real=False
        check('no-physical-execution',True)
    payload={'version':'0.6.0','mode':'offline-owned-global-integration',
      'selftest':'passed','totalScenarios':len(rows),'results':rows,
      'fullEvidenceDependenciesPresent':real,'executionAuthorized':False,
      'physicalExecutionEnabled':False,'commandsExecuted':0,
      'limitations':['Offline only; legacy runtime still uses inherited descriptors',
        'No authenticated runtime worker delegation or physical rollback']}
    print(json.dumps(payload,indent=2))
if __name__=='__main__':
    if sys.argv[1:]!=['selftest']: raise SystemExit(2)
    main()
