#!/usr/bin/env python3
"""Offline phase journal regression suite."""
import importlib.util,json,tempfile,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'display-phase-journal-v0600.py';s=importlib.util.spec_from_file_location('phase',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
results=[]
def check(name,fn):
 try:fn();results.append({'scenario':name,'passed':True})
 except Exception as e:results.append({'scenario':name,'passed':False,'error':str(e)})
def setup(root):
 m.sim.initialize(root);r=m.c.prepare(root,m.sim.BASE,time.time_ns()+30_000_000_000);a=m.c.transition(root,r['id'],r['generation'],'ARMED');return a
def assert_equal(a,b):
 if a!=b:raise AssertionError((a,b))
def expect_error(fn):
 try:fn()
 except (RuntimeError,ValueError):return
 raise AssertionError('expected rejection')
def corrupt_check(root):
 p=root/'operation-phase.json'
 p.write_text('{broken')
 m.audit(root)
with tempfile.TemporaryDirectory(prefix='serpantinum-phase-') as temp:
 root=Path(temp)/'apply';root.mkdir(mode=0o700);a=setup(root)
 check('missing-journal',lambda:assert_equal(m.audit(root)['decision'],'BLOCK_MISSING_JOURNAL'))
 check('apply',lambda:assert_equal(m.operation(root,'apply',a['id'],a['generation'])['state'],'APPLIED'))
 check('apply-audit',lambda:assert_equal(m.audit(root)['decision'],'APPLY_VERIFIED'))
 check('apply-phase',lambda:assert_equal(m.read(root)['phase'],'COMPLETED'))
 check('apply-observed',lambda:assert_equal(m.sim.inspect(root)['simulatedMonitor'],m.sim.TARGET))
 check('reject-duplicate',lambda:expect_error(lambda:m.operation(root,'apply',a['id'],a['generation'])))
 root2=Path(temp)/'rollback';root2.mkdir(mode=0o700);a2=setup(root2);ap=m.sim.apply(root2,a2['id'],a2['generation']);due=m.c.transition(root2,ap['id'],ap['generation'],'ROLLBACK_REQUIRED')
 check('rollback',lambda:assert_equal(m.operation(root2,'rollback',due['id'],due['generation'])['state'],'ROLLED_BACK'))
 check('rollback-audit',lambda:assert_equal(m.audit(root2)['decision'],'TERMINAL_VERIFIED'))
 check('rollback-observed',lambda:assert_equal(m.sim.inspect(root2)['simulatedMonitor'],m.sim.BASE))
 check('rollback-idempotence',lambda:assert_equal(m.audit(root2)['decision'],'TERMINAL_VERIFIED'))
 root3=Path(temp)/'uncertain-apply';root3.mkdir(mode=0o700);a3=setup(root3)
 check('injected-apply-fault',lambda:expect_error(lambda:m.operation(root3,'apply',a3['id'],a3['generation'],True)))
 check('apply-uncertain',lambda:assert_equal(m.audit(root3)['decision'],'RECONCILIATION_REQUIRED'))
 check('apply-intent',lambda:assert_equal(m.read(root3)['phase'],'APPLY_INTENT'))
 check('apply-no-auto-close',lambda:assert_equal(m.c.inspect(root3)['state'],'UNCERTAIN'))
 root4=Path(temp)/'uncertain-rollback';root4.mkdir(mode=0o700);a4=setup(root4);ap4=m.sim.apply(root4,a4['id'],a4['generation']);due4=m.c.transition(root4,ap4['id'],ap4['generation'],'ROLLBACK_REQUIRED')
 check('injected-rollback-fault',lambda:expect_error(lambda:m.operation(root4,'rollback',due4['id'],due4['generation'],True)))
 check('rollback-uncertain',lambda:assert_equal(m.audit(root4)['decision'],'RECONCILIATION_REQUIRED'))
 check('rollback-intent',lambda:assert_equal(m.read(root4)['phase'],'ROLLBACK_INTENT'))
 check('rollback-no-auto-close',lambda:assert_equal(m.c.inspect(root4)['state'],'UNCERTAIN'))
 check('wrong-action',lambda:expect_error(lambda:m.operation(root4,'unknown','x',1)))
 check('wrong-token',lambda:expect_error(lambda:m.operation(root4,'rollback','foreign',1)))
 check('wrong-generation',lambda:expect_error(lambda:m.operation(root4,'rollback',due4['id'],1)))
 check('journal-identity',lambda:assert_equal(m.read(root4)['transaction_id'],due4['id']))
 check('journal-generation',lambda:assert_equal(m.read(root4)['generation'],due4['generation']))
 check('journal-boot',lambda:assert_equal(m.read(root4)['boot_id'],Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
 check('no-physical-commands',lambda:assert_equal(m.audit(root4)['commandsExecuted'],0))
 check('no-physical-execution',lambda:assert_equal(m.audit(root4)['physicalExecutionEnabled'],False))
 check('journal-permissions',lambda:assert_equal((root4/'operation-phase.json').stat().st_mode & 0o777,0o600))
 check('journal-corruption',lambda:expect_error(lambda:corrupt_check(root4)))

print(json.dumps({'selftest':'passed' if all(r['passed'] for r in results) else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
raise SystemExit(0 if all(r['passed'] for r in results) else 2)
