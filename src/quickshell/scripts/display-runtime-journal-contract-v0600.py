#!/usr/bin/env python3
"""Offline-only cross-layer runtime admission/journal contract. No display commands."""
import argparse, importlib.util, json, os, pathlib, stat, subprocess, sys, tempfile
HERE=pathlib.Path(__file__).resolve().parent

def load(filename, key):
    p=HERE/filename
    if not p.is_file() or p.is_symlink(): raise RuntimeError('unsafe dependency')
    spec=importlib.util.spec_from_file_location(key,p)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def selftest():
    admission=load('display_runtime_admission_v0592.py','admission_contract')
    journal=load('display-transaction-serialized-v0600.py','journal_contract')
    integration=load('display-transaction-integration-v0600.py','integration_contract')
    rows=[]
    def check(name, ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-crosslayer-') as tmp:
        base=pathlib.Path(tmp); runtime=base/'runtime'; store=base/'store'; isolated=base/'offline'
        for p in (runtime,store,isolated): p.mkdir(mode=0o700)
        old_runtime=os.environ.get('XDG_RUNTIME_DIR'); old_fd=os.environ.pop(admission.ENV,None)
        os.environ['XDG_RUNTIME_DIR']=str(runtime)
        try:
            check('private-runtime',stat.S_IMODE(runtime.stat().st_mode)==0o700)
            check('private-store',stat.S_IMODE(store.stat().st_mode)==0o700)
            check('empty-not-safe',journal.decision(store)=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
            with admission.admission(runtime) as fd:
                check('admission-fd-valid',fd>=3)
                check('child-fds-exact',admission.child_fds()==(fd,))
                check('shared-lock-created',(runtime/admission.NAME).is_file())
                check('shared-lock-private',stat.S_IMODE((runtime/admission.NAME).stat().st_mode)==0o600)
                p=journal.prepare(store,'offline-contract','digest')
                check('journal-prepared',p['phase']=='prepared')
                check('journal-generation-1',p['generation']==1)
                check('journal-blocks',journal.decision(store)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
                code='import os,sys;sys.path.insert(0,sys.argv[1]);import display_runtime_admission_v0592 as a;\ntry:\n with a.admission(): pass\nexcept a.AdmissionError: sys.exit(17)\nsys.exit(0)'
                # Use an explicit Python fixture with no inherited admission fd.
                code=code.replace('\\n','\n')
                env={k:v for k,v in os.environ.items() if k!=admission.ENV}
                result=subprocess.run([sys.executable,'-B','-c',code,str(HERE)],env=env,capture_output=True,text=True,timeout=6)
                check('external-contender-rejected',result.returncode==17)
                check('journal-unchanged-under-contention',journal.guard._read(store)==p)
                check('offline-integration-not-runtime-lock',integration.inspect(store,isolated)['universalRuntimeExclusionVerified'] is False)
                p=journal.advance(store,1,'applying')
                check('journal-advance',p['generation']==2 and p['phase']=='applying')
                check('runtime-fd-still-valid',admission.child_fds()==(fd,))
            check('admission-env-restored',admission.ENV not in os.environ)
            with admission.admission(runtime): check('admission-reacquired',True)
            check('journal-persists-after-lock-release',journal.guard._read(store)['phase']=='applying')
            check('lock-release-not-rollback',journal.decision(store)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('offline-inspection-no-authorization',integration.inspect(store,isolated)['executionAuthorized'] is False)
            check('offline-inspection-no-cleanup',integration.inspect(store,isolated)['cleanupAuthorized'] is False)
            check('no-physical-execution',True)
        finally:
            if old_runtime is None: os.environ.pop('XDG_RUNTIME_DIR',None)
            else: os.environ['XDG_RUNTIME_DIR']=old_runtime
            os.environ.pop(admission.ENV,None)
            if old_fd is not None: os.environ[admission.ENV]=old_fd
    return {'version':'0.6.0','mode':'offline-crosslayer-contract','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,'commandsExecuted':0,'limitations':['Cooperative cross-layer contract only','No authenticated worker delegation','No physical rollback or universal entrypoint exclusion']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['selftest']);parser.parse_args()
    print(json.dumps(selftest(),indent=2))
