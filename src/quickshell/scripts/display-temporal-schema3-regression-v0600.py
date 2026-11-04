#!/usr/bin/env python3
"""Schema-3 additive offline regression, no physical operations."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import sys
HERE=Path(__file__).resolve().parent

def load(name,alias):
    p=HERE/name;s=importlib.util.spec_from_file_location(alias,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
m=load('display-temporal-schema3-v0600.py','schema3_test')
c=load('display-physical-recovery-controller-v0600.py','schema3_controller_test')
checks=[]
def check(label,value):
    checks.append({'scenario':label,'passed':bool(value)})
    if not value:raise AssertionError(label)
BASE={'name':'eDP-1','width':1920,'height':1080,'refresh_mhz':60060,'scale_milli':1000,'transform':0}
try:
    with tempfile.TemporaryDirectory(prefix='schema3-') as tmp:
        root=Path(tmp)/'state';root.mkdir(mode=0o700)
        check('missing-sidecar',m.inspect(root)['decision']=='BLOCK_NO_SCHEMA3')
        now={'realtime_ns':time.time_ns(),'boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)}
        r=c.prepare(root,BASE,now['realtime_ns']+30_000_000_000)
        b=m.bind(root,now=now)
        check('schema-version',b['clock_schema']==3)
        check('identity-bound',b['transaction_id']==r['id'])
        check('generation-bound',b['generation_at_binding']==r['generation'])
        check('boottime-deadline',b['deadline_boottime_ns']==now['boottime_ns']+30_000_000_000)
        check('wall-audit',b['deadline_realtime_ns']==r['deadline_ns'])
        check('recovery-obligation',b['recovery_required'] is True)
        check('permissions',(root/'temporal-schema3.json').stat().st_mode & 0o777==0o600)
        check('waiting',m.inspect(root,now=now,current_boot=b['boot_id'])['decision']=='WAITING')
        check('early-wait',m.inspect(root,now={'realtime_ns':now['realtime_ns']+1_000_000_000,'boottime_ns':now['boottime_ns']+1_000_000_000},current_boot=b['boot_id'])['decision']=='WAITING')
        check('due',m.inspect(root,now={'realtime_ns':now['realtime_ns']+30_000_000_000,'boottime_ns':now['boottime_ns']+30_000_000_000},current_boot=b['boot_id'])['decision']=='RECOVERY_DUE')
        check('late',m.inspect(root,now={'realtime_ns':now['realtime_ns']+40_000_000_000,'boottime_ns':now['boottime_ns']+40_000_000_000},current_boot=b['boot_id'])['decision']=='RECOVERY_DUE')
        check('forward-clock',m.inspect(root,now={'realtime_ns':now['realtime_ns']+15_000_000_000,'boottime_ns':now['boottime_ns']},current_boot=b['boot_id'])['decision']=='RECONCILIATION_REQUIRED_CLOCK_DIVERGENCE')
        check('backward-clock',m.inspect(root,now={'realtime_ns':now['realtime_ns']-15_000_000_000,'boottime_ns':now['boottime_ns']},current_boot=b['boot_id'])['decision']=='RECONCILIATION_REQUIRED_CLOCK_DIVERGENCE')
        check('boot-change',m.inspect(root,current_boot='00000000-0000-0000-0000-000000000000')['decision']=='RECONCILIATION_REQUIRED_NEW_BOOT')
        check('no-execution',m.inspect(root)['commandsExecuted']==0)
        check('no-authorization',m.inspect(root)['executionAuthorized'] is False)
        try:m.bind(root,now=now)
        except m.TemporalDenied:duplicate=True
        else:duplicate=False
        check('duplicate-rejected',duplicate)
        armed=c.transition(root,r['id'],r['generation'],'ARMED')
        check('generation-progress-allowed',m.inspect(root)['decision']=='WAITING')
        uncertain=c.transition(root,r['id'],armed['generation'],'UNCERTAIN')
        check('uncertain-reconcile',m.inspect(root)['decision']=='RECONCILIATION_REQUIRED_UNCERTAIN')
        check('original-schema-preserved',c.inspect(root)['schema']==1)
        check('original-state-preserved',c.inspect(root)['state']=='UNCERTAIN')
        check('sidecar-persisted',m._read(root)['clock_schema']==3)
        check('sidecar-identity-preserved',m._read(root)['transaction_id']==r['id'])
        check('sidecar-boot-preserved',m._read(root)['boot_id']==b['boot_id'])
        check('no-physical',m.inspect(root)['physicalExecutionEnabled'] is False)
        # Distinct transaction must never adopt an existing temporal sidecar.
        (root/'recovery.json').unlink()
        newer=c.prepare(root,BASE,time.time_ns()+30_000_000_000)
        check('new-transaction-rejected',m.inspect(root)['decision']=='BLOCK_IDENTITY_MISMATCH')
        check('new-token-different',newer['id']!=r['id'])
        check('sidecar-no-adoption',m._read(root)['transaction_id']!=newer['id'])
        check('read-no-mutation',c.inspect(root)['state']=='PREPARED')
        check('valid-boot-format',len(b['boot_id'])==36)
        check('schema1-no-upgrade',set(c.inspect(root))=={'schema','id','state','deadline_ns','snapshot','generation'})
        check('read-only-clock-sample',m.clocks()['boottime_ns']>0)
        check('offline-only',True)
    print(json.dumps({'version':'0.6.0','mode':'temporal-schema3-offline','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except Exception as exc:
    print(json.dumps({'selftest':'failed','totalScenarios':len(checks),'error':type(exc).__name__+': '+str(exc),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2));sys.exit(2)
