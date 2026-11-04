#!/usr/bin/env python3
"""Offline topology and command regressions; no display mutation."""
import copy,hashlib,importlib.util,json,os,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(n,a):
 s=importlib.util.spec_from_file_location(a,HERE/n);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
m=load('display-dynamic-topology-v0600.py','topology_test')
c=load('display-topology-command-plan-v0600.py','topology_command_test')
checks=[]
def ok(name,cond):
 checks.append({'scenario':name,'passed':bool(cond)})
 if not cond:raise AssertionError(name)
def deny(name,fn):
 try:fn()
 except (m.Denied,c.Denied,ValueError,TypeError,KeyError):ok(name,True)
 else:ok(name,False)
def edp(name):return dict(name=name,make='AU Optronics',model='B156HAN15.H ',width=1920,height=1080,x=0,y=0,scale=1,transform=0,disabled=False,refreshRate=60.061,availableModes=['1920x1080@60.06Hz','1920x1080@144.06Hz'])
hdmi=dict(name='HDMI-A-1',make='Dell Inc.',model='Inspiron 3043',width=1920,height=1080,x=1920,y=0,scale=1,transform=0,disabled=False,refreshRate=60.0)
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);hypr=root/'hypr';backup=root/'lua';config=root/'baseline'
 for d in (hypr/'config',backup/'config',config):d.mkdir(parents=True)
 lua=b'\n'.join(f'hl.monitor({{output="{n}",mode="1920x1080@60",position="0x0",scale="1"}})'.encode() for n in ('eDP-1','eDP-2','HDMI-A-1'))
 for d in (hypr,backup):
  (d/'hyprland.lua').write_text('require("config/monitors")\n');(d/'config/monitors.lua').write_bytes(lua)
 raw=b'monitor=legacy\n';(hypr/'monitors.conf').write_bytes(raw);(config/'monitors.conf').write_bytes(raw)
 (config/'monitors.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  monitors.conf\n')
 (config/'monitors.json').write_text(json.dumps([edp('eDP-1'),hdmi]))
 comp=dict(pid=123,starttime=456,instance='test_instance',socketDevice=1,socketInode=2)
 def cap(proof):return dict(decision='DYNAMIC_TOPOLOGY_READONLY_READY',captureAtomic=False,executionAuthorized=False,physicalExecutionEnabled=False,commandsExecuted=0,compositor=comp,protected='HDMI-A-1',persistentSource='config/monitors.lua',**proof)
 for name in ('eDP-1','eDP-2'):
  for with_hdmi in (False,True):
   obs=[edp(name)]+([copy.deepcopy(hdmi)] if with_hdmi else [])
   proof=m.validate(obs,hypr,backup,config)
   label=name+('-dual' if with_hdmi else '-mono')
   ok(label+'-target',proof['target']==name)
   ok(label+'-topology',proof['topology']==('internal-and-hdmi' if with_hdmi else 'internal-only'))
   ok(label+'-fingerprint',proof['monitorDigest']==m.fingerprint(obs))
   contract=c.plan(cap(proof),obs,lua)
   ok(label+'-contract',contract['decision']=='DYNAMIC_TOPOLOGY_COMMANDS_READONLY')
   ok(label+'-commands-zero',contract['commandsExecuted']==0 and contract['physicalExecutionEnabled'] is False)
   ok(label+'-protected',contract['protected']=='HDMI-A-1' and all('HDMI-A-1' not in str(x) for x in contract['candidateArgs']))
   ok(label+'-rollback',contract['rollbackArgs'][-1].startswith(name+',1920x1080@60.06,'))
   changed=copy.deepcopy(obs);changed[0]['refreshRate']=144.061
   deny(label+'-stale',lambda:c.plan(cap(proof),changed,lua))
 mono=[edp('eDP-1')];proof=m.validate(mono,hypr,backup,config)
 for name,obs in [('empty',[]),('duplicate',[edp('eDP-1'),edp('eDP-1')]),('both-edp',[edp('eDP-1'),edp('eDP-2')]),('unknown',[edp('eDP-1'),dict(hdmi,name='DP-1')]),('three',[edp('eDP-1'),hdmi,dict(hdmi,name='DP-1')]),('external-only',[hdmi])]:
  deny('reject-'+name,lambda obs=obs:m.validate(obs,hypr,backup,config))
 for name,key,value in [('wrong-make','make','Other'),('wrong-model','model','Other'),('wrong-refresh','refreshRate',75),('disabled','disabled',True),('missing-modes','availableModes',[])]:
  obs=copy.deepcopy(mono);obs[0][key]=value;deny('reject-'+name,lambda obs=obs:m.validate(obs,hypr,backup,config))
 for name,path in [('lua-drift',hypr/'config/monitors.lua'),('import-drift',hypr/'hyprland.lua'),('conf-drift',hypr/'monitors.conf')]:
  original=path.read_bytes();path.write_bytes(b'bad');deny('reject-'+name,lambda:m.validate(mono,hypr,backup,config));path.write_bytes(original)
 for name,fn in [('lua-hash',lambda:c.plan(cap(proof),mono,b'wrong')),('stale-digest',lambda:c.plan(dict(cap(proof),monitorDigest='0'*64),mono,lua)),('authority',lambda:c.plan(dict(cap(proof),executionAuthorized=True),mono,lua)),('bad-socket',lambda:c.plan(dict(cap(proof),compositor=dict(comp,socketInode=-1)),mono,lua))]:deny('reject-'+name,fn)
result={'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(result,indent=2))
