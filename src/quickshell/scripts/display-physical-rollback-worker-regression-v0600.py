#!/usr/bin/env python3
"""Offline regression of read-only rollback boundary; no display mutations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

p=Path(__file__).with_name('display-physical-rollback-worker-v0600.py')
s=importlib.util.spec_from_file_location('physical_boundary',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
INSTANCE='efb50993780079460b0cbed1363e2166a2de1d9f_1793622541_919627583'

def monitor(name='eDP-1',rate=60.061):
    return dict(name=name,width=1920,height=1080,disabled=False,refreshRate=rate,availableModes=['1920x1080@60.06Hz','1920x1080@144.06Hz'])

results=[]
def check(name, fn, accepted=True):
    try:
        out=fn()
        ok=(out['decision'].endswith('_READONLY') if accepted else False)
    except (m.Denied, ValueError, TypeError, KeyError):
        ok=not accepted
    results.append(dict(scenario=name,passed=ok))

for connector in ('eDP-1','eDP-2'):
    for rate in (60.061,144.06):
        for external in (False,True):
            mons=[monitor(connector,rate)]+([monitor('HDMI-A-1')] if external else [])
            check(f'valid_{connector}_{rate}_{external}',lambda mons=mons,connector=connector:m.validate(mons,connector,'1920x1080@60.06',INSTANCE))

cases=[
 ('missing',[], 'eDP-1'),('duplicate',[monitor(),monitor()],'eDP-1'),
 ('wrong_target',[monitor('eDP-2')],'eDP-1'),('unknown',[monitor('DP-1')],'eDP-1'),
 ('external_only',[monitor('HDMI-A-1')],'eDP-1'),
 ('both_internal',[monitor('eDP-1'),monitor('eDP-2')],'eDP-1'),
 ('three',[monitor(),monitor('HDMI-A-1'),monitor('DP-1')],'eDP-1'),
 ('disabled',[dict(monitor(),disabled=True)],'eDP-1'),
 ('wrong_width',[dict(monitor(),width=1280)],'eDP-1'),
 ('wrong_height',[dict(monitor(),height=720)],'eDP-1'),
 ('wrong_rate',[monitor(rate=90)],'eDP-1'),
 ('missing_modes',[dict(monitor(),availableModes=[])],'eDP-1'),
 ('bad_modes',[dict(monitor(),availableModes='abc')],'eDP-1'),
 ('malformed',[None],'eDP-1'),
 ('invalid_connector',[monitor()],'eDP-3'),
 ('empty_instance',[monitor()],'eDP-1'),
]
for name,mons,target in cases:
    inst='' if name=='empty_instance' else INSTANCE
    check(name,lambda mons=mons,target=target,inst=inst:m.validate(mons,target,'1920x1080@60.06',inst),False)

with tempfile.TemporaryDirectory() as tmp:
    fixture=Path(tmp)/'monitors.json';fixture.write_text(json.dumps([monitor()]))
    check('fixture_valid',lambda:m.execute('eDP-1',INSTANCE,fixture))
    fixture.write_text('not json')
    check('fixture_corrupt',lambda:m.execute('eDP-1',INSTANCE,fixture),False)
    fixture.unlink();fixture.symlink_to('/etc/passwd')
    check('fixture_symlink',lambda:m.execute('eDP-1',INSTANCE,fixture),False)

# Prove the only live command is a read-only query and no mutation is issued.
commands=[]
def runner(cmd,**kwargs):
    commands.append(cmd)
    return subprocess.CompletedProcess(cmd,0,json.dumps([monitor()]),'')
check('live_readonly',lambda:m.execute('eDP-1',INSTANCE,runner=runner))
results.append(dict(scenario='only_readonly_command',passed=len(commands)==1 and commands[0][3:]==['monitors','-j']))

out=dict(selftest='passed' if all(x['passed'] for x in results) else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,commandsExecuted=0)
print(json.dumps(out,indent=2))
sys.exit(0 if out['selftest']=='passed' else 2)
