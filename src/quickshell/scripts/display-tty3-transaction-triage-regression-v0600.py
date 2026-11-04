#!/usr/bin/env python3
"""Offline read-only regression for TTY3 physical transaction triage."""
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile

HERE = Path(__file__).resolve().parent
p = HERE / 'display-tty3-transaction-triage-v0600.py'
s = importlib.util.spec_from_file_location('triage_test',p)
t = importlib.util.module_from_spec(s)
s.loader.exec_module(t)

BOOT = 'test-boot'
INSTANCE = 'test_instance_12345678'
BASE = dict(name='eDP-1',description='AU Optronics B156HAN15.H',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
HDMI = dict(name='HDMI-A-1',description='Dell Inc. Inspiron 3043',width=1920,height=1080,refreshRate=60.0,x=1920,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)

def fixture(root, monitors, state='APPLIED', phase='APPLY_VERIFIED', target='eDP-1'):
    root.mkdir(mode=0o700)
    originals = [dict(x) for x in monitors]
    originals.sort(key=lambda x:x['name'])
    r = dict(schema=1,id=secrets.token_hex(32),generation=4,state=state,phase=phase,target=target,
             topology='internal-only' if len(originals)==1 else 'internal-and-hdmi',plan_sha256='b'*64,
             boot_id=BOOT,deadline_boottime_ns=999999999999999999)
    side = dict(schema=1,transaction_id=r['id'],generation_at_binding=1,boot_id=BOOT,target=target,
                topology=r['topology'],snapshot=originals,snapshot_sha256=t.snapshot.digest(originals),
                identity=dict(instance=INSTANCE,pid=981,starttime=24639,lua_sha256='a'*64,capture_digest='c'*64))
    w = dict(schema=1,transaction_id=r['id'],generation_at_arm=2,transaction_digest=t.obligation.digest(r),
             deadline_boottime_ns=r['deadline_boottime_ns'],boot_id=BOOT,status='ARMED_SIMULATED')
    with t.ipc.locked(root) as path:
        t.ipc.write(path,'transaction.json',r)
        t.ipc.write(path,'physical-snapshot.json',side)
        t.ipc.write(path,'watchdog-obligation.json',w)
    return r

def run():
    cases=[]
    with tempfile.TemporaryDirectory(prefix='serp-tty3-triage-') as tmp:
        rootbase=Path(tmp)
        def check(name, expected, base=None, live=None, state='APPLIED', phase='APPLY_VERIFIED', change=None, instance=INSTANCE, boot=BOOT):
            root=rootbase/name
            base = [dict(x) for x in (base if base is not None else [BASE])]
            fixture(root,base,state,phase)
            if change:
                with t.ipc.locked(root) as path:
                    change(path)
            observed=[dict(x) for x in (live if live is not None else base)]
            try:
                decision=t.assess(root,observed,instance,boot)['decision']
            except (OSError,ValueError,RuntimeError,TypeError,KeyError):
                decision='BLOCK_AND_INVESTIGATE'
            assert decision==expected,(name,decision,expected)
            cases.append(dict(scenario=name,passed=True))
        candidate=dict(BASE,refreshRate=144.061)
        check('original_pending','ORIGINAL_OBSERVED_TRANSACTION_PENDING')
        check('candidate_pending','CANDIDATE_OBSERVED_RECOVERY_REQUIRED',live=[candidate])
        check('candidate_rollback_required','CANDIDATE_OBSERVED_RECOVERY_REQUIRED',live=[candidate],state='ROLLBACK_REQUIRED')
        check('candidate_uncertain','CANDIDATE_OBSERVED_RECOVERY_REQUIRED',live=[candidate],state='UNCERTAIN',phase='ROLLBACK_INTENT')
        check('candidate_prepared','BLOCK_CANDIDATE_WITHOUT_PENDING_STATE',live=[candidate],state='PREPARED',phase='NONE')
        check('terminal_original','ORIGINAL_OBSERVED_TERMINAL_UNVERIFIED',state='ROLLED_BACK',phase='COMPLETED')
        check('confirmed_original','ORIGINAL_OBSERVED_CONFIRMED',state='CONFIRMED')
        check('hdmi_original','ORIGINAL_OBSERVED_TRANSACTION_PENDING',base=[BASE,HDMI])
        check('hdmi_candidate','CANDIDATE_OBSERVED_RECOVERY_REQUIRED',base=[BASE,HDMI],live=[candidate,HDMI])
        check('hdmi_disconnected','BLOCK_TOPOLOGY_CHANGED',base=[BASE,HDMI],live=[BASE])
        check('hdmi_connected','BLOCK_TOPOLOGY_CHANGED',live=[BASE,HDMI])
        check('connector_changed','BLOCK_TOPOLOGY_CHANGED',live=[dict(BASE,name='eDP-2')])
        check('compositor_changed','BLOCK_COMPOSITOR_CHANGED',instance='other_instance_999')
        check('wrong_boot','BLOCK_AND_INVESTIGATE',boot='wrong-boot')
        for field,value in [('width',1280),('height',720),('x',100),('y',10),('scale',1.25),('transform',1),('disabled',True),('dpmsStatus',False),('description','Different')]:
            check('drift_'+field,'BLOCK_MONITOR_DRIFT',live=[dict(BASE,**{field:value})])
        check('unexpected_frequency','BLOCK_UNEXPECTED_MODE',live=[dict(BASE,refreshRate=75)])
        def tamper(path):
            side=t.ipc.read(path,'physical-snapshot.json');side['snapshot_sha256']='0'*64;t.ipc.write(path,'physical-snapshot.json',side)
        check('tamper_snapshot','BLOCK_AND_INVESTIGATE',change=tamper)
        def tamper_obligation(path):
            w=t.ipc.read(path,'watchdog-obligation.json');w['transaction_id']='bad';t.ipc.write(path,'watchdog-obligation.json',w)
        check('tamper_obligation','BLOCK_AND_INVESTIGATE',change=tamper_obligation)
        def tamper_tx(path):
            r=t.ipc.read(path,'transaction.json');r['id']='wrong';t.ipc.write(path,'transaction.json',r)
        check('tamper_transaction','BLOCK_AND_INVESTIGATE',change=tamper_tx)
        check('missing_monitor_field','BLOCK_AND_INVESTIGATE',live=[{k:v for k,v in BASE.items() if k!='scale'}])
        check('duplicate_monitor','BLOCK_AND_INVESTIGATE',base=[BASE,HDMI],live=[BASE,BASE])
        check('missing_all','BLOCK_TOPOLOGY_CHANGED',live=[])
        check('extra_unknown','BLOCK_TOPOLOGY_CHANGED',live=[BASE,dict(HDMI,name='DP-1')])
        check('candidate_no_false_terminal','BLOCK_CANDIDATE_WITHOUT_PENDING_STATE',live=[candidate],state='ROLLED_BACK',phase='COMPLETED')
        check('no_fake_physical_verification','ORIGINAL_OBSERVED_TERMINAL_UNVERIFIED',state='ROLLED_BACK',phase='COMPLETED')
    return dict(selftest='passed',totalScenarios=len(cases),results=cases,physicalExecutionEnabled=False,commandsExecuted=0)

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
