#!/usr/bin/env python3
"""Pure offline fail-closed tests of the physical executor gate."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

p = Path(__file__).with_name('display-physical-executor-gate-v0600.py')
s = importlib.util.spec_from_file_location('physical_gate', p)
g = importlib.util.module_from_spec(s)
s.loader.exec_module(g)

lua = b'-- unchanged test lua\n'
instance = 'abcdef123456_123456_987654'
internal = {'name':'eDP-1','width':1920,'height':1080,'refreshRate':60.061,'disabled':False,'x':0,'y':0,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}
external = {'name':'HDMI-A-1','width':1920,'height':1080,'refreshRate':60.0,'disabled':False,'x':1920,'y':0}
capture = {'decision':'DYNAMIC_TOPOLOGY_READONLY_READY','compositor':{'instance':instance,'pid':981},'target':'eDP-1','topology':'internal-only','luaSha256':hashlib.sha256(lua).hexdigest(),'physicalExecutionEnabled':False,'executionAuthorized':False,'captureAtomic':False,'commandsExecuted':0}
def command(hz): return ['hyprctl','-i',instance,'keyword','monitor',f'eDP-1,1920x1080@{hz},0x0,1']
plan = {'decision':'DYNAMIC_TOPOLOGY_COMMANDS_READONLY','target':'eDP-1','topology':'internal-only','protected':'HDMI-A-1','candidateArgs':command('144.06'),'rollbackArgs':command('60.06'),'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
instances = [{'instance':instance,'pid':981}]

def baseline():
    return [copy.deepcopy(x) for x in (plan,capture,[internal],lua,instances,[internal])]

def evaluate(case, should_pass, mutate):
    args = baseline()
    mutate(args)
    try:
        out = g.check(*args)
        passed = should_pass and out['decision']=='PHYSICAL_EXECUTOR_PREFLIGHT_READONLY' and out['physicalExecutionEnabled'] is False and out['commandsExecuted']==0 and out['watchdogPhysicalVerified'] is False
    except (g.Denied, TypeError, ValueError, KeyError):
        passed = not should_pass
    return {'scenario':case,'passed':passed}

cases = [
('baseline',True,lambda a:None),
('bad_plan',False,lambda a:a[0].update(decision='READY')),
('bad_capture',False,lambda a:a[1].update(decision='READY')),
('plan_authority',False,lambda a:a[0].update(physicalExecutionEnabled=True)),
('capture_authority',False,lambda a:a[1].update(executionAuthorized=True)),
('atomic_claim',False,lambda a:a[1].update(captureAtomic=True)),
('plan_commands',False,lambda a:a[0].update(commandsExecuted=1)),
('capture_commands',False,lambda a:a[1].update(commandsExecuted=1)),
('lua_changed',False,lambda a:a.__setitem__(3,b'changed')),
('monitor_drift',False,lambda a:a[5][0].update(refreshRate=144.06)),
('two_instances',False,lambda a:a[4].append({'instance':'other','pid':42})),
('pid_drift',False,lambda a:a[4][0].update(pid=982)),
('instance_drift',False,lambda a:a[4][0].update(instance='other_instance')),
('bad_instance',False,lambda a:a[1]['compositor'].update(instance='../unsafe')),
('target_drift',False,lambda a:a[0].update(target='eDP-2')),
('external_unprotected',False,lambda a:a[0].update(protected='none')),
('topology_label',False,lambda a:a[0].update(topology='internal-and-hdmi')),
('unexpected_monitor',False,lambda a:a[2].append(copy.deepcopy(external))),
('duplicate_monitor',False,lambda a:a[2].append(copy.deepcopy(internal))),
('disabled_internal',False,lambda a:a[2][0].update(disabled=True)),
('bad_width',False,lambda a:a[2][0].update(width=1280)),
('bad_frequency',False,lambda a:a[2][0].update(refreshRate=144.06)),
('mode_missing',False,lambda a:a[2][0].update(availableModes=['1920x1080@60.06Hz'])),
('position_float',False,lambda a:a[2][0].update(x=0.0)),
('candidate_tampered',False,lambda a:a[0]['candidateArgs'].__setitem__(5,'eDP-1,1920x1080@144.06,100x0,1')),
('rollback_tampered',False,lambda a:a[0]['rollbackArgs'].__setitem__(5,'HDMI-A-1,1920x1080@60.06,0x0,1')),
('bad_topology',False,lambda a:a[1].update(topology='unknown')),
('missing_capture_compositor',False,lambda a:a[1].pop('compositor')),
('missing_internal',False,lambda a:a[2].clear()),
('unexpected_state',False,lambda a:a[0].update(executionAuthorized=True)),
]
results=[evaluate(*case) for case in cases]
# Add supported internal-and-HDMI and eDP-2 scenarios.
a=baseline(); a[2].append(copy.deepcopy(external));a[5].append(copy.deepcopy(external));a[0]['topology']=a[1]['topology']='internal-and-hdmi'
results.append({'scenario':'internal_and_hdmi','passed':g.check(*a)['topology']=='internal-and-hdmi'})
a=baseline()
for item in (a[2][0],a[5][0]): item['name']='eDP-2'
a[0]['target']=a[1]['target']='eDP-2'
for key in ('candidateArgs','rollbackArgs'): a[0][key][5]=a[0][key][5].replace('eDP-1','eDP-2')
results.append({'scenario':'edp2','passed':g.check(*a)['target']=='eDP-2'})
output={'selftest':'passed' if all(x['passed'] for x in results) else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(output,indent=2))
raise SystemExit(0 if output['selftest']=='passed' else 2)
