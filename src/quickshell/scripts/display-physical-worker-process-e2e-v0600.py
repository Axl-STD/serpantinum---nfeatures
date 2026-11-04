#!/usr/bin/env python3
"""Offline process-boundary tests. No Hyprland calls, no physical authority."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time

D = Path(__file__).resolve().parent

def load(path, alias):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

w = load(D / 'display-physical-recovery-worker-v0600.py', 'worker_e2e')
ipc = w.ipc
BOOT = Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def fixture(root):
    root.mkdir(mode=0o700)
    snap = [dict(name='eDP-1', description='AU Optronics', width=1920, height=1080,
                 refreshRate=60.061, x=0, y=0, scale=1, transform=0,
                 disabled=False, dpmsStatus=True)]
    record = dict(schema=1, id=secrets.token_hex(32), generation=4,
                  state='APPLIED', target='eDP-1', topology='internal-only',
                  plan_sha256='b'*64, boot_id=BOOT,
                  deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1_000_000,
                  phase='APPLY_VERIFIED', execution_backend='physical')
    side = dict(transaction_id=record['id'], boot_id=BOOT, target='eDP-1',
                topology='internal-only', snapshot=snap,
                snapshot_sha256=w.sha(snap), identity={'instance':'fixture-instance'})
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    receipt=dict(transaction_id=record['id'],
                 transaction_digest=w.sha({k:record[k] for k in keys}),
                 status='ARMED_LAB_ONLY')
    with ipc.locked(root) as p:
        ipc.write(p,'transaction.json',record)
        ipc.write(p,'physical-snapshot.json',side)
        ipc.write(p,'physical-watchdog-rehearsal.json',receipt)
    return snap

def invoke(root, hz, failpoint=None, kill=False):
    code = '''
import importlib.util,json,os,signal,sys
from pathlib import Path
p=Path(sys.argv[1]); root=Path(sys.argv[2]); hz=float(sys.argv[3]); fail=sys.argv[4]; kill=sys.argv[5]=='kill'
s=importlib.util.spec_from_file_location('worker_child',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with m.ipc.locked(root) as d: snap=m.ipc.read(d,'physical-snapshot.json')['snapshot']
snap[0]['refreshRate']=hz
transport=m.LabTransport(snap,'fixture-instance')
result=m.recover_lab(root,transport,failpoint=None if fail=='none' else fail)
print(json.dumps(result),flush=True)
if kill: os.kill(os.getpid(),signal.SIGKILL)
'''
    return subprocess.run([sys.executable,'-B','-c',code,
        str(D/'display-physical-recovery-worker-v0600.py'),str(root),str(hz),
        failpoint or 'none','kill' if kill else 'normal'],
        capture_output=True,text=True,timeout=12)

def check_state(root, state, phase):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
    assert (r['state'],r['phase'],r['execution_backend']) == (state,phase,'physical'),r
    return r

results=[]
def scenario(name,fn):
    try:
        fn()
        results.append({'scenario':name,'passed':True})
    except Exception as exc:
        results.append({'scenario':name,'passed':False,'error':repr(exc)})

def intent_then_recover(base):
    root=base/'intent';fixture(root)
    first=invoke(root,144.06,'after-intent',True)
    assert first.returncode == -signal.SIGKILL,(first.returncode,first.stderr)
    check_state(root,'UNCERTAIN','ROLLBACK_INTENT')
    second=invoke(root,144.06)
    assert second.returncode == 0,second.stderr
    assert json.loads(second.stdout)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
    check_state(root,'ROLLED_BACK','COMPLETED')

def effect_then_recover(base):
    root=base/'effect';fixture(root)
    first=invoke(root,144.06,'after-effect',True)
    assert first.returncode == -signal.SIGKILL,(first.returncode,first.stderr)
    check_state(root,'UNCERTAIN','ROLLBACK_INTENT')
    # The transport is in-memory. Reconstructing it at ORIGINAL explicitly
    # models an already-restored effect; it is NOT a persisted physical effect.
    second=invoke(root,60.061)
    assert second.returncode==0,second.stderr
    assert json.loads(second.stdout)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
    check_state(root,'ROLLED_BACK','COMPLETED')

def concurrent_case(base):
    root=base/'concurrent';fixture(root)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        out=list(pool.map(lambda _:invoke(root,144.06),range(4)))
    # Each process owns a separate in-memory transport. Once the first
    # process commits, others must fail closed because their stale fake
    # observation still shows CANDIDATE. This is intentional.
    good=[x for x in out if x.returncode==0]
    denied=[x for x in out if x.returncode!=0]
    assert len(good)==1 and len(denied)==3,[(x.returncode,x.stderr) for x in out]
    assert json.loads(good[0].stdout)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
    assert all('inconsistent terminal' in x.stderr for x in denied)
    check_state(root,'ROLLED_BACK','COMPLETED')

def rejected_receipt(base):
    root=base/'receipt';fixture(root)
    with ipc.locked(root) as p:
        x=ipc.read(p,'physical-watchdog-rehearsal.json');x['status']='REVOKED';ipc.write(p,'physical-watchdog-rehearsal.json',x)
    x=invoke(root,144.06)
    assert x.returncode != 0
    check_state(root,'APPLIED','APPLY_VERIFIED')

def rejected_backend(base):
    root=base/'backend';fixture(root)
    with ipc.locked(root) as p:
        x=ipc.read(p,'transaction.json');x['execution_backend']='simulated';ipc.write(p,'transaction.json',x)
    x=invoke(root,144.06)
    assert x.returncode != 0
    with ipc.locked(root) as p:
        assert ipc.read(p,'transaction.json')['state']=='APPLIED'

with tempfile.TemporaryDirectory(prefix='serpantinum-worker-e2e-') as td:
    base=Path(td)
    base.chmod(0o700)
    for name,fn in [('sigkill_after_intent',intent_then_recover),
                    ('sigkill_after_effect_reconstructed',effect_then_recover),
                    ('four_independent_processes',concurrent_case),
                    ('revoked_lab_receipt_denied',rejected_receipt),
                    ('simulated_backend_denied',rejected_backend)]:
        scenario(name,lambda fn=fn:fn(base))

ok=all(r['passed'] for r in results)
print(json.dumps(dict(version='0.6.0',selftest='passed' if ok else 'failed',
                      totalScenarios=len(results),results=results,
                      physicalExecutionEnabled=False,executionAuthorized=False,
                      commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
sys.exit(0 if ok else 1)
