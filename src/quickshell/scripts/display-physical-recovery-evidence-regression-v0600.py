#!/usr/bin/env python3
"""Regression matrix for non-mutating recovery evidence decisions."""
import copy, importlib.util, json, tempfile, hashlib
from pathlib import Path
P=Path(__file__).resolve().parent/'display-physical-recovery-evidence-v0600.py'
s=importlib.util.spec_from_file_location('ev',P);ev=importlib.util.module_from_spec(s);s.loader.exec_module(ev)
ipc=ev.ipc

def baseline(name='eDP-1',hdmi=False):
    m=[dict(name=name,description='AU Optronics B156HAN15.H',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)]
    if hdmi:m.append(dict(name='HDMI-A-1',description='Dell',width=1920,height=1080,refreshRate=60.0,x=1920,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True))
    m.sort(key=lambda x:x["name"])
    return m

def capture(name,hdmi):
    return dict(decision='DYNAMIC_TOPOLOGY_READONLY_READY',target=name,topology='internal-and-hdmi' if hdmi else 'internal-only',persistentSource='config/monitors.lua',captureAtomic=False,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,compositor=dict(instance='example_instance_1234',pid=981,starttime=24639),luaSha256='a'*64,monitorDigest='b'*64)

def run_case(name,mutation=None,state='APPLIED',phase='APPLY_VERIFIED',candidate=False,hdmi=False,target='eDP-1',expected=None):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d); old=baseline(target,hdmi); cap=capture(target,hdmi)
        r=dict(schema=1,id='f'*64,generation=4,state=state,phase=phase,target=target,topology=cap['topology'],boot_id='boot',deadline_boottime_ns=999999999999999)
        side=dict(schema=1,transaction_id=r['id'],generation_at_binding=1,boot_id='boot',target=target,topology=r['topology'],snapshot=old,snapshot_sha256=ev.snap.digest(old),identity=ev.snap.identity(cap,target,r['topology']))
        obs=copy.deepcopy(old)
        if candidate:next(x for x in obs if x['name']==target)['refreshRate']=144.06
        if mutation:mutation(r,side,obs,cap)
        ipc.write(root,'transaction.json',r);ipc.write(root,'physical-snapshot.json',side)
        before=(root/'transaction.json').read_bytes(),(root/'physical-snapshot.json').read_bytes()
        try:result=ev.decide(root,obs,cap);decision=result['decision'];assert result['commandsExecuted']==0 and result['rollbackPhysicalVerified'] is False
        except (ev.Denied,ev.snap.Denied,KeyError,TypeError,ValueError):decision='BLOCK_AND_INVESTIGATE'
        assert decision==expected,(name,decision,expected)
        assert before==((root/'transaction.json').read_bytes(),(root/'physical-snapshot.json').read_bytes())
        return dict(scenario=name,passed=True)

cases=[]
def add(name,**kw):cases.append(run_case(name,**kw))
add('original_pending',expected='PHYSICAL_ORIGINAL_OBSERVED_PENDING')
add('candidate_pending',candidate=True,expected='PHYSICAL_CANDIDATE_OBSERVED_PENDING')
add('rollback_required',state='ROLLBACK_REQUIRED',candidate=True,expected='PHYSICAL_CANDIDATE_OBSERVED_PENDING')
add('uncertain_candidate',state='UNCERTAIN',phase='ROLLBACK_INTENT',candidate=True,expected='PHYSICAL_CANDIDATE_OBSERVED_PENDING')
add('uncertain_original',state='UNCERTAIN',phase='ROLLBACK_INTENT',expected='PHYSICAL_ORIGINAL_OBSERVED_PENDING')
add('terminal_original',state='ROLLED_BACK',phase='COMPLETED',expected='PHYSICAL_ORIGINAL_OBSERVED_TERMINAL_UNVERIFIED')
add('terminal_candidate_block',state='ROLLED_BACK',phase='COMPLETED',candidate=True,expected='BLOCK_AND_INVESTIGATE')
add('prepared',state='PREPARED',phase='NONE',expected='PHYSICAL_BASELINE_OBSERVED')
add('armed',state='ARMED',phase='NONE',expected='PHYSICAL_BASELINE_OBSERVED')
add('armed_candidate_block',state='ARMED',phase='NONE',candidate=True,expected='BLOCK_AND_INVESTIGATE')
add('edp2_candidate',target='eDP-2',candidate=True,expected='PHYSICAL_CANDIDATE_OBSERVED_PENDING')
add('dual_candidate',hdmi=True,candidate=True,expected='PHYSICAL_CANDIDATE_OBSERVED_PENDING')
add('dual_original',hdmi=True,expected='PHYSICAL_ORIGINAL_OBSERVED_PENDING')

def change(path,value):return lambda r,s,o,c: path(r,s,o,c,value)
def obs(k):return lambda r,s,o,c,v:o[0].__setitem__(k,v)
for key,val in [('width',1600),('height',900),('x',50),('y',10),('scale',1.25),('transform',1),('disabled',True),('dpmsStatus',False),('description','different'),('name','eDP-2'),('refreshRate',75.0)]:
    add('reject_internal_'+key,mutation=change(obs(key),val),expected='BLOCK_AND_INVESTIGATE')
for key,val in [('x',0),('refreshRate',75.0),('description','Other')]:
    add('reject_external_'+key,hdmi=True,mutation=lambda r,s,o,c,k=key,v=val:o[0].__setitem__(k,v),expected='BLOCK_AND_INVESTIGATE')
add('reject_hotplug',mutation=lambda r,s,o,c:o.append(dict(o[0],name='HDMI-A-1')),expected='BLOCK_AND_INVESTIGATE')
add('reject_unplug',hdmi=True,mutation=lambda r,s,o,c:o.pop(),expected='BLOCK_AND_INVESTIGATE')
add('reject_identity',mutation=lambda r,s,o,c:c['compositor'].__setitem__('pid',982),expected='BLOCK_AND_INVESTIGATE')
add('reject_lua',mutation=lambda r,s,o,c:c.__setitem__('luaSha256','c'*64),expected='BLOCK_AND_INVESTIGATE')
add('reject_transaction',mutation=lambda r,s,o,c:r.__setitem__('id','0'*64),expected='BLOCK_AND_INVESTIGATE')
add('reject_boot',mutation=lambda r,s,o,c:r.__setitem__('boot_id','newboot'),expected='BLOCK_AND_INVESTIGATE')
add('reject_generation',mutation=lambda r,s,o,c:r.__setitem__('generation',0),expected='BLOCK_AND_INVESTIGATE')
add('reject_sidecar_digest',mutation=lambda r,s,o,c:s.__setitem__('snapshot_sha256','0'*64),expected='BLOCK_AND_INVESTIGATE')
add('reject_sidecar_identity',mutation=lambda r,s,o,c:s['identity'].__setitem__('pid',12),expected='BLOCK_AND_INVESTIGATE')
print(json.dumps(dict(selftest='passed',totalScenarios=len(cases),results=cases,physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
