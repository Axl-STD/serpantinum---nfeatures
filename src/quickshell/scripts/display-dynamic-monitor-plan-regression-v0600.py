#!/usr/bin/env python3
import copy,hashlib,importlib.util,json,tempfile,pathlib,os,sys
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('dynamic',HERE/'display-dynamic-monitor-plan-v0600.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
def check(name,passed):
 checks.append({'scenario':name,'passed':bool(passed)})
 if not passed: raise AssertionError(name)
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp);hypr=root/'hypr';lua=root/'lua';backup=root/'baseline'
 for p in (hypr/'config',lua/'config',backup):p.mkdir(parents=True)
 main=b'require("config/monitors")\n'
 rules=b'\n'.join(f'hl.monitor({{output="{n}",mode="1920x1080@60",position="0x0",scale="1"}})'.encode() for n in ('eDP-1','eDP-2','HDMI-A-1'))
 for p in (hypr,lua):
  (p/'hyprland.lua').write_bytes(main);(p/'config/monitors.lua').write_bytes(rules)
 def edp(name):return dict(name=name,make='AU Optronics',model='B156HAN15.H ',width=1920,height=1080,x=0,y=0,scale=1,transform=0,disabled=False,refreshRate=60.061,availableModes=['1920x1080@60.06Hz','1920x1080@144.06Hz'])
 hdmi=dict(name='HDMI-A-1',make='Dell Inc.',model='Inspiron 3043',width=1920,height=1080,x=1920,y=0,scale=1,transform=0,disabled=False,refreshRate=60.0,availableModes=['1920x1080@60.00Hz'])
 snap=[edp('eDP-1'),hdmi];obs=copy.deepcopy(snap)
 raw=b'# generated\nmonitor=offline\n';(hypr/'monitors.conf').write_bytes(raw);(backup/'monitors.conf').write_bytes(raw)
 (backup/'monitors.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  monitors.conf\n')
 (backup/"monitors.json").write_text(json.dumps(snap))
 def run(s=snap,o=obs):return m.plan(s,o,hypr,lua,hypr,backup)
 def denied(name,s=None,o=None):
  try:run(s if s is not None else snap,o if o is not None else obs)
  except (m.Denied,ValueError,TypeError):check(name,True)
  else:check(name,False)
 r=run();check('baseline',r['decision']=='DYNAMIC_READ_ONLY_PLAN_READY');check('target',r['target']=='eDP-1');check('alternate',r['alternate']=='eDP-2');check('protected',r['protected']=='HDMI-A-1');check('no-execution',r['commandsExecuted']==0 and r['physicalExecutionEnabled'] is False)
 alt=[edp('eDP-2'),copy.deepcopy(hdmi)];denied('connector-drift',o=alt)
 (backup/'monitors.json').write_text(json.dumps(snap))
 # alternate connector supported only with its own verified snapshot
 (backup/'monitors.json').write_text(json.dumps(alt));r=run(alt,alt);check('edp2-baseline',r['target']=='eDP-2');check('edp2-alternate',r['alternate']=='eDP-1')
 (backup/'monitors.json').write_text(json.dumps(snap))
 for i in (0,1):
  for key,val in [('width',1280),('height',720),('x',99),('y',99),('scale',1.5),('transform',1),('disabled',True),('refreshRate',75.0)]:
   o=copy.deepcopy(obs);o[i][key]=val;denied(f'drift-{i}-{key}',o=o)
 for name,change in [('missing-mode',lambda x:x[0].update(availableModes=[])),('wrong-make',lambda x:x[0].update(make='Other')),('wrong-model',lambda x:x[0].update(model='Other')),('external-make',lambda x:x[1].update(make='Other')),('external-model',lambda x:x[1].update(model='Other')),('duplicate',lambda x:x[1].update(name='eDP-1')),('missing-hdmi',lambda x:x.pop()),('extra-output',lambda x:x.append(dict(x[0],name='eDP-2'))),('nan-refresh',lambda x:x[0].update(refreshRate=float('nan')))]:
  o=copy.deepcopy(obs);change(o);denied(name,o=o)
 for path,data,name in [(hypr/'config/monitors.lua',b'bad','lua-drift'),(lua/'config/monitors.lua',b'bad','lua-backup-drift'),(hypr/'hyprland.lua',b'bad','import-drift'),(hypr/'monitors.conf',b'bad','secondary-config-drift'),(backup/'monitors.conf',b'bad','backup-drift'),(backup/'monitors.sha256',b'bad','manifest-drift'),(backup/'monitors.json',b'[]','snapshot-drift')]:
  old=path.read_bytes();path.write_bytes(data)
  try:denied(name)
  finally:path.write_bytes(old)
 check('no-physical-backend',run()['executionAuthorized'] is False)
result={'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(result,indent=2))
