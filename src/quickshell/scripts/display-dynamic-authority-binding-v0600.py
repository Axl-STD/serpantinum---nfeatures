#!/usr/bin/env python3
"""Offline-only binding of monitor evidence to schema-3 transaction identity.
This is a diagnostic contract, NOT an atomic capture or execution authorization.
"""
import argparse, hashlib, json, re, sys

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def decide(plan,record,side,compositor):
    base={'version':'0.6.0','mode':'dynamic-authority-binding-offline','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}
    def out(decision,**extra):return dict(base,decision=decision,**extra)
    if not all(isinstance(x,dict) for x in (plan,record,side,compositor)):
        return out('BLOCK_INVALID_INPUT')
    if plan.get('decision')!='DYNAMIC_READ_ONLY_PLAN_READY' or plan.get('physicalExecutionEnabled') is not False or plan.get('executionAuthorized') is not False or type(plan.get('commandsExecuted')) is not int or plan['commandsExecuted']!=0:
        return out('BLOCK_PLAN')
    if plan.get('target') not in ('eDP-1','eDP-2') or plan.get('alternate')!=({'eDP-1':'eDP-2','eDP-2':'eDP-1'}[plan['target']]) or plan.get('protected')!='HDMI-A-1' or plan.get('persistentSource')!='config/monitors.lua' or plan.get('configurationAdapter')!='hyprland' or plan.get('original')!='1920x1080@60.06Hz' or plan.get('candidate')!='1920x1080@144.06Hz' or not isinstance(plan.get('luaSha256'),str) or not re.fullmatch('[0-9a-f]{64}',plan['luaSha256']):
        return out('BLOCK_MONITOR_IDENTITY')
    if side.get('clock_schema')!=3 or side.get('recovery_required') is not True or side.get('transaction_id')!=record.get('id') or side.get('deadline_realtime_ns')!=record.get('deadline_ns') or type(record.get('generation')) is not int or type(side.get('generation_at_binding')) is not int or record['generation']<side['generation_at_binding']:
        return out('BLOCK_TRANSACTION_BINDING')
    if not isinstance(side.get('boot_id'),str) or not re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',side['boot_id']) or type(side.get('deadline_boottime_ns')) is not int or side['deadline_boottime_ns']<=0:
        return out('BLOCK_TEMPORAL')
    if record.get('state')=='UNCERTAIN':return out('BLOCK_RECONCILIATION')
    if record.get('state') not in ('PREPARED','ARMED','APPLIED','ROLLBACK_REQUIRED','CONFIRMED','ROLLED_BACK'):
        return out('BLOCK_STATE')
    if type(compositor.get('pid')) is not int or compositor['pid']<=0 or type(compositor.get('starttime')) is not int or compositor['starttime']<=0 or not isinstance(compositor.get('instance'),str) or not compositor['instance'] or compositor.get('connector')!=plan['target']:
        return out('BLOCK_COMPOSITOR_IDENTITY')
    return out('OFFLINE_TERMINAL_BINDING' if record['state'] in ('CONFIRMED','ROLLED_BACK') else 'OFFLINE_BINDING_READY',transactionId=record['id'],generation=record['generation'],bootId=side['boot_id'],target=plan['target'],luaSha256=plan['luaSha256'],evidenceDigest=digest({'plan':plan,'record':record,'side':side,'compositor':compositor}))

def main():
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--record',required=True);a.add_argument('--temporal',required=True);a.add_argument('--compositor',required=True);v=a.parse_args()
    try:
        from pathlib import Path
        def read(s):
            p=Path(s)
            if p.is_symlink() or not p.is_file() or p.stat().st_size>65536:raise ValueError('unsafe evidence')
            return json.loads(p.read_text())
        result=decide(read(v.plan),read(v.record),read(v.temporal),read(v.compositor))
    except (OSError,ValueError,TypeError):result={'decision':'BLOCK_AND_INVESTIGATE','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
    print(json.dumps(result,indent=2));return 0 if result['decision'].startswith('OFFLINE_') else 2
if __name__=='__main__':sys.exit(main())
