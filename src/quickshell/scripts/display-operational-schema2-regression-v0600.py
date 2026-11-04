#!/usr/bin/env python3
"""Offline schema-2 operational facade contract tests (no physical execution)."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('op_schema2', HERE/'display-operational-writer-v0600.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run():
    rows=[]
    def check(name,ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    def denied(fn):
        try: fn()
        except (RuntimeError, ValueError, TypeError): return True
        return False
    with tempfile.TemporaryDirectory(prefix='serpantinum-op-schema2-') as tmp:
        roots=[Path(tmp)/name for name in ('runtime','journal','broker')]
        for root in roots: root.mkdir(mode=0o700)
        writer=mod.FencedOperationalWriter(*roots)
        check('legacy-default-still-present',hasattr(mod,'OperationalWriter'))
        check('schema2-opt-in-type',isinstance(writer,mod.OperationalWriter))
        check('before-enter-denied',denied(lambda:writer.begin('offline-probe','digest')))
        with writer:
            check('active',writer._session.active)
            check('schema2-session',type(writer._session).__name__=='FencedContinuousSession')
            check('same-thread',writer._thread==threading.get_ident())
            check('initial-decision',writer.decision()=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
            check('no-direct-physical-execute',not hasattr(writer,'execute'))
            check('no-direct-advance',not hasattr(writer,'advance'))
            check('no-direct-prepare',not hasattr(writer,'prepare'))
            report=writer.report()
            check('schema2-report',report['operationalSchema']==2)
            check('schema2-integrated',report['schema2Integrated'] is True)
            check('physical-disabled',report['physicalExecutionEnabled'] is False)
            check('execution-disabled',report['executionAuthorized'] is False)
            check('commands-zero',report['commandsExecuted']==0)
            check('no-logical-recovery',report['logicalRecoveryVerified'] is False)
            check('no-authenticated-delegation-claim',report['authenticatedDelegationImplemented'] is False)
            check('invalid-operation-denied',denied(lambda:writer.begin('', 'digest')))
            check('invalid-begin-no-journal',writer._session.fence.read(roots[1]) is None if hasattr(writer._session,'fence') else not (roots[1]/'intent.json').exists())
            prepared=writer.begin('offline-probe','digest')
            check('prepared',prepared['phase']=='prepared')
            check('generation-one',prepared['generation']==1)
            check('session-id-256-bit',len(prepared['session_id'])==64)
            check('recovery-required',prepared['recovery_required'] is True)
            check('pending-decision',writer.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('second-begin-denied',denied(lambda:writer.begin('offline-probe','again')))
            check('second-begin-preserves-journal',(roots[1]/'intent.json').exists())
            check('invalid-generation-denied',denied(lambda:writer.transition(0,2,'applying')))
            check('invalid-worker-denied',denied(lambda:writer.transition(0,1,'applying')))
            check('preflight-not-poisoned',writer._session.poisoned is False)
            result=[]
            thread=threading.Thread(target=lambda:result.append(denied(lambda:writer.begin('offline-probe','other'))))
            thread.start();thread.join(4)
            check('foreign-thread-joined',not thread.is_alive())
            check('foreign-thread-denied',result==[True])
        check('closed',writer._closed)
        check('after-close-denied',denied(lambda:writer.begin('offline-probe','digest')))
        check('reuse-denied',denied(lambda:writer.__enter__()))
        check('journal-preserved',(roots[1]/'intent.json').exists())
        with mod.FencedOperationalWriter(*roots) as second:
            check('pending-visible-after-restart',second.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('restart-cannot-adopt',denied(lambda:second.begin('offline-probe','replacement')))
            check('restart-not-authorized',second.report()['executionAuthorized'] is False)
        check('socket-cleaned',not (roots[2]/'worker.sock').exists())
        check('owner-lock-exists',any(roots[0].iterdir()))
        check('serialization-lock-exists',(roots[1]/'.intent-serialize.lock').exists())
    return {'version':'0.6.0','mode':'offline-operational-schema2','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Schema 2 opt-in; legacy writer unchanged',
                           'No physical display execution or runtime systemd integration',
                           'Does not prove hostile same-UID isolation or power-loss durability']}

if __name__=='__main__':
    try: print(json.dumps(run(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc)}))
        raise SystemExit(2)
