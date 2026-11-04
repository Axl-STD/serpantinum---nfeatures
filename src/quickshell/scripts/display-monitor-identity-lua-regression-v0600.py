#!/usr/bin/env python3
import importlib.util,json,os,tempfile
from pathlib import Path
P=Path(__file__).with_name('display-monitor-identity-lua-v0600.py')
s=importlib.util.spec_from_file_location('identity',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
checks=[]
def check(name,fn,valid):
    try: fn();ok=True
    except (m.Denied,ValueError,UnicodeError):ok=False
    passed=(ok==valid);checks.append({'scenario':name,'passed':passed})
    if not passed:raise AssertionError(name)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)/'hypr';backup=Path(tmp)/'backup'
    for p in (root/'config',backup/'config'):p.mkdir(parents=True)
    main=b'require("config/monitors")\n'
    rules=b'\n'.join(f'hl.monitor({{output="{name}",mode="1920x1080@60",position="0x0",scale="1"}})'.encode() for name in ('eDP-1','eDP-2','HDMI-A-1'))
    for base in (root,backup):
        (base/'hyprland.lua').write_bytes(main);(base/'config/monitors.lua').write_bytes(rules)
    edp={'name':'eDP-1','make':'AU Optronics','model':'B156HAN15.H ','width':1920,'height':1080,'disabled':False,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}
    hdmi={'name':'HDMI-A-1','make':'Dell Inc.','model':'Inspiron 3043','disabled':False}
    import copy
    def run(obs="DEFAULT"):return m.audit(root,backup,copy.deepcopy([edp,hdmi] if obs=="DEFAULT" else obs))
    check('baseline',lambda:run(),True)
    alt=copy.deepcopy(edp);alt['name']='eDP-2'
    check('edp2-alias',lambda:run([alt,hdmi]),True)
    variants=[('duplicate', [edp,edp]),('both-internals',[edp,alt,hdmi]),('external-missing',[edp]),('empty',[]),('nonlist',{}),('null',None),('invalid-items',[1,hdmi]),('external-replaced',[edp,dict(hdmi,name='DP-1')]),('internal-replaced',[dict(edp,name='DP-1'),hdmi]),('make',[dict(edp,make='Unknown'),hdmi]),('model',[dict(edp,model='Other'),hdmi]),('width',[dict(edp,width=1280),hdmi]),('height',[dict(edp,height=720),hdmi]),('disabled',[dict(edp,disabled=True),hdmi]),('mode',[dict(edp,availableModes=[]),hdmi]),('external-make',[edp,dict(hdmi,make='Other')]),('external-model',[edp,dict(hdmi,model='Other')]),('external-disabled',[edp,dict(hdmi,disabled=True)]),('internal-name',[dict(edp,name='eDP-3'),hdmi])]
    for name,obs in variants:check(name,lambda obs=obs:run(obs),False)
    def alter(path,data,func,valid=False):
        old=path.read_bytes();path.write_bytes(data)
        try:check(func,lambda:run(),valid)
        finally:path.write_bytes(old)
    alter(root/'hyprland.lua',b'print("hello")\n','missing-require')
    alter(root/'config/monitors.lua',rules.replace(b'eDP-2',b'eDP-3'),'missing-fallback')
    alter(root/'config/monitors.lua',rules+b'\nhl.monitor({output="eDP-1",mode="1920x1080@60",position="0x0",scale="1"})','duplicate-output')
    alter(root/'config/monitors.lua',b'not lua','unsupported-lua')
    alter(root/'config/monitors.lua',rules.replace(b'scale="1"',b''),'missing-field')
    alter(backup/'config/monitors.lua',b'changed','backup-mismatch')
    alter(backup/'hyprland.lua',b'changed','main-backup-mismatch')
    link=root/'config/monitors.lua';old=link.read_bytes();link.unlink();link.symlink_to(backup/'config/monitors.lua')
    try:check('symlink',lambda:run(),False)
    finally:link.unlink();link.write_bytes(old)
    check('parse-comments',lambda:m.parse_rules('-- hl.monitor({output="bad"})\n'+rules.decode()),True)
    check('no-rules',lambda:m.parse_rules('return {}'),False)
    check('invalid-root',lambda:m.audit(root/'missing',backup,[edp,hdmi]),False)
    check('invalid-backup',lambda:m.audit(root,backup/'missing',[edp,hdmi]),False)
    check('result-no-physical',lambda: (_ for _ in ()).throw(m.Denied()) if run()['commandsExecuted']!=0 else None,True)
result={'selftest':'passed' if all(x['passed'] for x in checks) else 'failed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(result,indent=2))
