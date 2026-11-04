#!/usr/bin/env python3
"""Fail-closed regression of shadow supervision, no systemd writes or monitor writes."""
import importlib.util, tempfile, json, secrets, time, copy
from pathlib import Path
P=Path(__file__).with_name('display-physical-supervisor-shadow-v0600.py')
spec=importlib.util.spec_from_file_location('shadow',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
def check(name,fn):
    fn();checks.append({'scenario':name,'passed':True})
with tempfile.TemporaryDirectory(prefix='serpantinum-shadow-') as td:
    root=Path(td);root.chmod(0o700)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    base=dict(name='eDP-1',description='panel',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    rec=dict(id=secrets.token_hex(32),boot_id=boot,execution_backend='physical',state='ARMED',phase='NONE',generation=1,target='eDP-1',topology='internal-only')
    snap=dict(transaction_id=rec['id'],boot_id=boot,target='eDP-1',topology='internal-only',snapshot=[base],snapshot_sha256=__import__('hashlib').sha256(m.ipc.canonical([base])).hexdigest(),identity={'instance':'test_instance_123'})
    def write(r=rec,s=snap):
        with m.ipc.locked(root) as p:
            m.ipc.write(p,'transaction.json',r);m.ipc.write(p,'physical-snapshot.json',s)
    def allowed():
        write();o=m.inspect(root,[{'instance':'test_instance_123'}],[base]);assert o['decision']=='SHADOW_LIVE_OBSERVATION_CONSISTENT';assert o['commandsExecuted']==0 and not o['watchdogPhysicalArmed']
    check('baseline',allowed)
    check('offline_read',lambda: (write(),m.inspect(root)))
    for state in ('ARMED','APPLIED','ROLLBACK_REQUIRED','UNCERTAIN','ROLLED_BACK'):
        def positive(state=state):
            r=dict(rec,state=state);write(r);assert m.inspect(root)['decision']=='SHADOW_TRANSACTION_VALID_READONLY'
        check('state_'+state,positive)
    mutations=[('simulated',lambda r,s:r.update(execution_backend='simulated')),('boot',lambda r,s:r.update(boot_id='other')),('target',lambda r,s:r.update(target='DP-1')),('topology',lambda r,s:r.update(topology='invalid')),('identity',lambda r,s:s.update(transaction_id='other')),('digest',lambda r,s:s.update(snapshot_sha256='0'*64)),('instance',lambda r,s:s.update(identity={})),('missing',lambda r,s:s.pop('snapshot')),('state',lambda r,s:r.update(state='INVALID'))]
    for name,change in mutations:
        def negative(change=change):
            r=copy.deepcopy(rec);s=copy.deepcopy(snap);change(r,s);write(r,s)
            try:m.inspect(root);raise AssertionError('unexpected acceptance')
            except (m.Denied,KeyError,TypeError):pass
        check('reject_'+name,negative)
    for name,change in [('hotplug',lambda x:x.append(dict(base,name='DP-1'))),('geometry',lambda x:x[0].update(x=200)),('refresh',lambda x:x[0].update(refreshRate=99)),('disabled',lambda x:x[0].update(disabled=True)),('nan',lambda x:x[0].update(refreshRate=float('nan'))),('protected',lambda x:x[0].update(name='HDMI-A-1'))]:
        def negative_live(change=change):
            write();obs=[copy.deepcopy(base)];change(obs)
            try:m.inspect(root,[{'instance':'test_instance_123'}],obs);raise AssertionError('unexpected acceptance')
            except m.Denied:pass
        check('reject_'+name,negative_live)
    def wrong_instance():
        write()
        try:m.inspect(root,[{'instance':'other'}],[base]);raise AssertionError('unexpected acceptance')
        except m.Denied:pass
    check('reject_instance_drift',wrong_instance)
    def candidate():
        write();assert m.inspect(root,[{'instance':'test_instance_123'}],[dict(base,refreshRate=144.06)])['decision']=='SHADOW_LIVE_OBSERVATION_CONSISTENT'
    check('candidate_readonly',candidate)
print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}))
