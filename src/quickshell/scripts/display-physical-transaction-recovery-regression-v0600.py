#!/usr/bin/env python3
"""Offline regression matrix for the fail-closed recovery coordinator."""
import importlib.util
import json
import secrets
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / 'display-physical-transaction-recovery-v0600.py'
spec = importlib.util.spec_from_file_location('tx_recovery_test', P)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
tx, ipc, stage = w.tx, w.ipc, w.tx.stage
RESULTS=[]

def scenario(name, func):
    try:
        func()
        RESULTS.append(dict(scenario=name, passed=True))
    except Exception as exc:
        RESULTS.append(dict(scenario=name, passed=False, error=type(exc).__name__+': '+str(exc)))

def fixture():
    tmp = tempfile.TemporaryDirectory(prefix='serpantinum-tx-recovery-')
    p = Path(tmp.name)
    p.chmod(0o700)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    monitor=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,
                 refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    r=dict(schema=1,id=secrets.token_hex(32),generation=2,state='ARMED',phase='NONE',
           execution_backend='physical',target='eDP-1',topology='internal-only',
           plan_sha256='b'*64,boot_id=boot,
           deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+120_000_000_000)
    s=dict(transaction_id=r['id'],boot_id=boot,target='eDP-1',topology='internal-only',
           snapshot=[monitor],snapshot_sha256=stage.digest([monitor]),identity={'instance':'laboratory-only'})
    with ipc.locked(p) as path:
        ipc.write(path,'transaction.json',r)
        ipc.write(path,'physical-snapshot.json',s)
    assert stage.stage(p)['decision']=='PHYSICAL_OBLIGATION_STAGED_NOT_ARMED'
    assert tx.execute(p,'prepare')['decision']=='REHEARSAL_PREPARED_NOT_ARMED'
    return tmp,p

def test_phase(n, expected, advance=0):
    tmp,p=fixture()
    try:
        for _ in range(advance):
            tx.execute(p,'advance')
        before=ipc.read(p,'transaction.json')
        response=w.execute(p,'recover')
        assert response['decision']==expected, response
        assert ipc.read(p,'transaction.json')==before
        assert response['commandsExecuted']==0 and not response['physicalExecutionEnabled']
    finally:
        tmp.cleanup()

scenario('ready_no_effect',lambda:test_phase(0,'NO_EFFECT_RECORDED_NOT_ARMED',0))
scenario('intent_uncertain',lambda:test_phase(1,'UNCERTAIN_EFFECT_REQUIRES_EVIDENCE',1))
scenario('observed_to_rollback_intent',lambda:test_phase(2,'ROLLBACK_INTENT_PERSISTED_NOT_EXECUTED',2))
scenario('rollback_intent_pending',lambda:test_phase(3,'ROLLBACK_PENDING_EXTERNAL_EVIDENCE',3))
scenario('terminal_simulated_only',lambda:test_phase(4,'SIMULATED_TERMINAL_NOT_PHYSICAL',4))

def repeated():
    tmp,p=fixture()
    try:
        for _ in range(2): tx.execute(p,'advance')
        assert w.execute(p,'recover')['decision']=='ROLLBACK_INTENT_PERSISTED_NOT_EXECUTED'
        assert w.execute(p,'recover')['decision']=='ROLLBACK_PENDING_EXTERNAL_EVIDENCE'
        assert ipc.read(p,tx.NAME)['step']==3
    finally: tmp.cleanup()
scenario('idempotent_recovery',repeated)

def invalidation():
    tmp,p=fixture()
    try:
        with ipc.locked(p) as path:
            r=ipc.read(path,'transaction.json')
            ipc.write(path,'transaction.json',dict(r,generation=3))
        try: w.execute(p,'recover')
        except (tx.Blocked,stage.Blocked): return
        raise AssertionError('generation drift accepted')
    finally: tmp.cleanup()
scenario('generation_drift_denied',invalidation)

def corrupted():
    tmp,p=fixture()
    try:
        with ipc.locked(p) as path:
            v=ipc.read(path,tx.NAME)
            ipc.write(path,tx.NAME,dict(v,step=4))
        try: w.execute(p,'recover')
        except tx.Blocked: return
        raise AssertionError('corrupt phase accepted')
    finally: tmp.cleanup()
scenario('phase_corruption_denied',corrupted)

def expired():
    tmp,p=fixture()
    try:
        with ipc.locked(p) as path:
            r=ipc.read(path,'transaction.json')
            ipc.write(path,'transaction.json',dict(r,deadline_boottime_ns=0))
        try: w.execute(p,'recover')
        except (tx.Blocked,stage.Blocked): return
        raise AssertionError('expired obligation accepted')
    finally: tmp.cleanup()
scenario('expired_denied',expired)

out=dict(selftest='passed' if all(x['passed'] for x in RESULTS) else 'failed',
         totalScenarios=len(RESULTS),results=RESULTS,
         watchdogPhysicalArmed=False,physicalExecutionEnabled=False,
         executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False)
print(json.dumps(out,indent=2))
raise SystemExit(0 if out['selftest']=='passed' else 2)
