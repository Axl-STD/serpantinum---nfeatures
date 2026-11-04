#!/usr/bin/env python3
"""Offline regression for watchdog integration boundary, no display mutations."""
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wd_integration', HERE/'display-physical-watchdog-integration-v0600.py')
wd=importlib.util.module_from_spec(spec);spec.loader.exec_module(wd)
results=[]
def check(name, condition):
    results.append({'scenario':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)

with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-') as temp:
    root=Path(temp);root.chmod(0o700)
    instance='hyprland_test_instance_123456'
    fixture=root/'monitors.json'
    def monitor(name='eDP-1', hz=60.061, hdmi=False):
        value=[{'name':name,'disabled':False,'width':1920,'height':1080,
                'refreshRate':hz,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}]
        if hdmi:value.append({'name':'HDMI-A-1','disabled':False,'width':1920,'height':1080})
        fixture.write_text(json.dumps(value))
    def transaction(state='APPLIED', phase='APPLY_VERIFIED', mode=wd.ipc.TARGET, target='eDP-1', boot=None):
        record=dict(schema=1,id=secrets.token_hex(32),generation=4,state=state,target=target,
                    topology='internal-only',plan_sha256='b'*64,
                    boot_id=boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1_000_000_000,
                    phase=phase)
        wd.ipc.write(root,'transaction.json',record)
        wd.ipc.write(root,'simulated-ipc.json',dict(connector=target,mode=mode))
        return record
    monitor();transaction()
    a=wd.inspect(root,instance,fixture)
    check('readonly_decision',a['decision']=='PHYSICAL_OBSERVATION_READONLY')
    check('readonly_mode',a['observedMode']==wd.ipc.BASE)
    check('no_physical_execution',a['commandsExecuted']==0 and a['physicalExecutionEnabled'] is False)
    check('no_physical_proof',a['rollbackPhysicalVerified'] is False)
    b=wd.simulate_recovery(root,instance,fixture)
    check('offline_recovery_decision',b['recoveryDecision']=='SIMULATED_ROLLBACK_VERIFIED')
    check('offline_recovery_terminal',b['state']=='ROLLED_BACK')
    check('offline_recovery_not_physical',b['rollbackPhysicalVerified'] is False)
    check('offline_effect_restored',wd.ipc.read(root,'simulated-ipc.json')['mode']==wd.ipc.BASE)
    c=wd.simulate_recovery(root,instance,fixture)
    check('idempotent_terminal',c['recoveryDecision']=='TERMINAL_VERIFIED')
    monitor(hdmi=True)
    check('two_monitor_observation',wd.inspect(root,instance,fixture)['topology']=='internal-and-hdmi')
    monitor(name='eDP-2')
    try:wd.inspect(root,instance,fixture);bad=False
    except wd.worker.Denied:bad=True
    check('connector_mismatch_denied',bad)
    monitor()
    transaction(boot='wrong-boot')
    check('boot_mismatch_blocked',wd.inspect(root,instance,fixture)['decision']=='BLOCK_NEW_BOOT')
    transaction(state='UNCERTAIN',phase='APPLY_INTENT')
    check('uncertain_apply_stays_blocked',wd.simulate_recovery(root,instance,fixture)['recoveryDecision']=='BLOCK_UNCERTAIN_APPLY')
    transaction(state='UNCERTAIN',phase='ROLLBACK_INTENT',mode=wd.ipc.BASE)
    check('uncertain_rollback_reconciled_simulated',wd.simulate_recovery(root,instance,fixture)['recoveryDecision']=='SIMULATED_RECONCILIATION_VERIFIED')
    transaction(state='UNCERTAIN',phase='ROLLBACK_INTENT',mode=wd.ipc.TARGET)
    check('uncertain_effect_blocks',wd.simulate_recovery(root,instance,fixture)['recoveryDecision']=='BLOCK_UNCERTAIN_EFFECT')
    transaction()
    monitor(hz=144.06)
    check('candidate_observed',wd.inspect(root,instance,fixture)['observedMode']==wd.ipc.TARGET)
    monitor(hz=60.061)
    transaction(target='eDP-2')
    monitor(name='eDP-2')
    check('alternate_connector_supported',wd.inspect(root,instance,fixture)['target']=='eDP-2')
    monitor(name='eDP-2',hdmi=True)
    check('alternate_plus_hdmi',wd.inspect(root,instance,fixture)['topology']=='internal-and-hdmi')

print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,
                  'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
