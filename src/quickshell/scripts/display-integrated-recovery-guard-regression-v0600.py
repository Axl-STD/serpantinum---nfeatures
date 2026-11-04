#!/usr/bin/env python3
"""Cross-module guard regression: no filesystem or Hyprland mutation."""
import contextlib
import importlib.util
import json
from pathlib import Path

p = Path(__file__).with_name('display-integrated-recovery-guard-v0600.py')
s = importlib.util.spec_from_file_location('guard_regression', p)
g = importlib.util.module_from_spec(s)
s.loader.exec_module(g)

base = dict(name='eDP-1', description='AU Optronics B156HAN15.H', width=1920, height=1080,
            refreshRate=60.061, x=0, y=0, scale=1, transform=0, disabled=False, dpmsStatus=True)
record = dict(id='a'*64, boot_id='boot', target='eDP-1', topology='internal-only',
              generation=4, state='APPLIED', phase='APPLY_VERIFIED')
side = dict(schema=1, transaction_id=record['id'], boot_id='boot', target='eDP-1',
            topology='internal-only', generation_at_binding=1, snapshot=[base],
            snapshot_sha256=g.snap.digest([base]), identity={'instance':'instance123'})
original = (g.ipc.locked, g.wd._read, g.wd._receipt, g.ipc.read)
@contextlib.contextmanager
def fake_lock(_):
    yield Path('/unused')
def run(r, sidecar, observed, instance='instance123'):
    g.ipc.locked = fake_lock
    g.wd._read = lambda _: r
    g.wd._receipt = lambda _p, _r: True
    g.ipc.read = lambda _p, name: sidecar if name == 'physical-snapshot.json' else None
    try:
        return g.inspect(Path('/unused'), observed, instance=instance)['decision']
    except (RuntimeError, ValueError, KeyError, TypeError):
        return 'BLOCK_EXCEPTION'

def change(obj, **kwargs):
    return dict(obj, **kwargs)

cases = [
 ('candidate_pending',record,side,[change(base,refreshRate=144.06)],'instance123','PHYSICAL_CANDIDATE_PENDING'),
 ('original_pending',record,side,[base],'instance123','PHYSICAL_ORIGINAL_PENDING'),
 ('rollback_required',change(record,state='ROLLBACK_REQUIRED'),side,[change(base,refreshRate=144.06)],'instance123','PHYSICAL_CANDIDATE_PENDING'),
 ('uncertain_apply',change(record,state='UNCERTAIN',phase='APPLY_INTENT'),side,[base],'instance123','BLOCK_UNCERTAIN_PHYSICAL'),
 ('uncertain_rollback',change(record,state='UNCERTAIN',phase='ROLLBACK_INTENT'),side,[base],'instance123','BLOCK_UNCERTAIN_PHYSICAL'),
 ('terminal',change(record,state='ROLLED_BACK',phase='COMPLETED'),side,[base],'instance123','TERMINAL_ORIGINAL_OBSERVED_UNVERIFIED'),
 ('terminal_candidate',change(record,state='ROLLED_BACK',phase='COMPLETED'),side,[change(base,refreshRate=144.06)],'instance123','BLOCK_TERMINAL_MISMATCH'),
 ('confirmed',change(record,state='CONFIRMED'),side,[base],'instance123','CONFIRMED_NO_ACTION'),
 ('armed',change(record,state='ARMED',phase='NONE'),side,[base],'instance123','BASELINE_OBSERVED'),
 ('prepared',change(record,state='PREPARED',phase='NONE'),side,[base],'instance123','BASELINE_OBSERVED'),
 ('armed_candidate',change(record,state='ARMED',phase='NONE'),side,[change(base,refreshRate=144.06)],'instance123','BLOCK_INCONSISTENT_PHASE'),
 ('instance_changed',record,side,[base],'other-instance','BLOCK_COMPOSITOR_CHANGED'),
 ('transaction_changed',change(record,id='b'*64),side,[base],'instance123','BLOCK_EXCEPTION'),
 ('boot_changed',change(record,boot_id='different'),side,[base],'instance123','BLOCK_EXCEPTION'),
 ('target_changed',change(record,target='eDP-2'),side,[base],'instance123','BLOCK_EXCEPTION'),
 ('generation_rollback',change(record,generation=0),side,[base],'instance123','BLOCK_EXCEPTION'),
 ('snapshot_tamper',record,change(side,snapshot_sha256='f'*64),[base],'instance123','BLOCK_EXCEPTION'),
 ('geometry_changed',record,side,[change(base,x=1)],'instance123','BLOCK_EXCEPTION'),
 ('scale_changed',record,side,[change(base,scale=1.25)],'instance123','BLOCK_EXCEPTION'),
 ('hotplug',record,side,[base,change(base,name='HDMI-A-1')],'instance123','BLOCK_EXCEPTION'),
 ('frequency_unexpected',record,side,[change(base,refreshRate=90)],'instance123','BLOCK_EXCEPTION'),
 ('missing_snapshot',record,None,[base],'instance123','BLOCK_EXCEPTION'),
]
results=[]
try:
    for name,r,sd,obs,instance,expected in cases:
        actual=run(r,sd,obs,instance)
        results.append(dict(scenario=name,passed=actual==expected,decision=actual))
finally:
    g.ipc.locked, g.wd._read, g.wd._receipt, g.ipc.read = original
passed=all(x['passed'] for x in results)
print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),
                      results=results,physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
raise SystemExit(0 if passed else 1)
