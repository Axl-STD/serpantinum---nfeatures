#!/usr/bin/env python3
"""Offline regression for the physical transaction adapter (never physical)."""
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent
p = HERE / 'display-physical-transaction-adapter-v0600.py'
s = importlib.util.spec_from_file_location('physical_adapter_test', p)
a = importlib.util.module_from_spec(s)
s.loader.exec_module(a)

results = []
def check(name, condition):
    results.append({'scenario': name, 'passed': bool(condition)})
    if not condition: raise AssertionError(name)

def make(parent, target='eDP-1', topology='internal-only', state='ARMED', phase='NONE', mode=None, deadline=None):
    root = parent / secrets.token_hex(8)
    root.mkdir(mode=0o700)
    record = {'schema': 1, 'id': secrets.token_hex(32), 'generation': 2,
              'state': state, 'target': target, 'topology': topology,
              'plan_sha256': 'b'*64, 'boot_id': a._boot(),
              'deadline_boottime_ns': deadline or (time.clock_gettime_ns(time.CLOCK_BOOTTIME)+20_000_000_000),
              'phase': phase}
    a.ipc.write(root, 'transaction.json', record)
    a.ipc.write(root, 'simulated-ipc.json', {'connector': target, 'mode': mode or a.ORIGINAL})
    return root, record

def denied(fn):
    try: fn()
    except (a.Denied, a.ipc.Denied, ValueError, KeyError, OSError): return True
    return False

with tempfile.TemporaryDirectory(prefix='serpantinum-physical-adapter-') as tmp:
    parent = Path(tmp)
    for target in ('eDP-1','eDP-2'):
        for topology in ('internal-only','internal-and-hdmi'):
            root, r = make(parent, target, topology)
            out = a.execute(root,'apply',r['id'])
            check(f'apply_{target}_{topology}', out['decision']=='SIMULATED_APPLY_VERIFIED')
            check(f'candidate_{target}_{topology}', a.execute(root,'inspect',r['id'])['observedMode']==a.CANDIDATE)
            check(f'early_recovery_{target}_{topology}', denied(lambda: a.execute(root,'recover',r['id'])))
            out = a.execute(root,'recover',r['id'],now_ns=r['deadline_boottime_ns']+1)
            check(f'rollback_{target}_{topology}',out['decision']=='SIMULATED_ROLLBACK_VERIFIED')
            check(f'terminal_{target}_{topology}',a.execute(root,'recover',r['id'])['decision']=='TERMINAL_NO_ACTION')
    root,r=make(parent)
    check('bad_token',denied(lambda:a.execute(root,'apply','0'*64)))
    check('bad_boot',denied(lambda:a.execute(root,'apply',r['id'],boot_id='wrong')))
    check('unknown_action',denied(lambda:a.execute(root,'physical-apply',r['id'])))
    check('bad_failpoint',denied(lambda:a.execute(root,'apply',r['id'],failpoint='unexpected')))
    out=a.execute(root,'apply',r['id'],failpoint='after-intent')
    check('apply_intent',out['phase']=='APPLY_INTENT')
    check('apply_uncertain_block',denied(lambda:a.execute(root,'recover',r['id'])))
    root,r=make(parent)
    out=a.execute(root,'apply',r['id'],failpoint='after-effect')
    check('apply_effect_uncertain',out['state']=='UNCERTAIN')
    check('apply_effect_no_false_rollback',denied(lambda:a.execute(root,'recover',r['id'])))
    for point in ('after-intent','after-effect'):
        root,r=make(parent,state='APPLIED',phase='APPLY_VERIFIED',mode=a.CANDIDATE,deadline=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1)
        out=a.execute(root,'recover',r['id'],failpoint=point)
        check('rollback_'+point,out['phase']=='ROLLBACK_INTENT')
        if point=='after-intent':
            check('rollback_intent_candidate_block',denied(lambda:a.execute(root,'recover',r['id'])))
        else:
            check('rollback_effect_reconcile',a.execute(root,'recover',r['id'])['decision']=='SIMULATED_RECONCILIATION_VERIFIED')
            check('reconciled_terminal',a.execute(root,'recover',r['id'])['decision']=='TERMINAL_NO_ACTION')
    root,r=make(parent)
    a.ipc.write(root,'simulated-ipc.json',{'connector':'HDMI-A-1','mode':a.ORIGINAL})
    check('connector_drift',denied(lambda:a.execute(root,'inspect',r['id'])))
    root,r=make(parent)
    a.ipc.write(root,'simulated-ipc.json',{'connector':r['target'],'mode':'invalid'})
    check('unknown_mode',denied(lambda:a.execute(root,'inspect',r['id'])))
    root,r=make(parent)
    a.ipc.write(root,'transaction.json',dict(r,topology='unknown'))
    check('topology_drift',denied(lambda:a.execute(root,'inspect',r['id'])))
    root,r=make(parent,state='APPLIED',phase='APPLY_VERIFIED',mode=a.CANDIDATE,deadline=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs=list(pool.map(lambda _:a.execute(root,'recover',r['id']),range(4)))
    check('concurrent_one_rollback',sum(x['decision']=='SIMULATED_ROLLBACK_VERIFIED' for x in outputs)==1)
    check('concurrent_three_terminal',sum(x['decision']=='TERMINAL_NO_ACTION' for x in outputs)==3)
    check('concurrent_final_state',a.execute(root,'inspect',r['id'])['state']=='ROLLED_BACK')
    check('no_physical_authority',all(x['physicalExecutionEnabled'] is False and x['commandsExecuted']==0 for x in outputs))
    root,r=make(parent,state='CONFIRMED')
    check('confirmed_no_action',a.execute(root,'recover',r['id'])['decision']=='TERMINAL_NO_ACTION')
    root,r=make(parent,state='ROLLED_BACK',phase='COMPLETED',mode=a.CANDIDATE)
    check('terminal_mismatch',denied(lambda:a.execute(root,'recover',r['id'])))

print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,
                  'physicalExecutionEnabled':False,'commandsExecuted':0}))
