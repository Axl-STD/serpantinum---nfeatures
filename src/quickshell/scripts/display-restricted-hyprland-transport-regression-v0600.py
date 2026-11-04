import copy
import importlib.util
import json
from pathlib import Path
p=Path(__file__).with_name('display-restricted-hyprland-transport-v0600.py')
s=importlib.util.spec_from_file_location('transport',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
base={'name':'eDP-1','width':1920,'height':1080,'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True,'refreshRate':144.06}
hdmi={'name':'HDMI-A-1','width':1920,'height':1080,'x':1920,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True,'refreshRate':60}
results=[]
def check(name,fn,denied=False):
    try:
        fn();ok=not denied
    except (m.Denied,KeyError,TypeError,ValueError):ok=denied
    results.append({'scenario':name,'passed':ok})
for target in ('eDP-1','eDP-2'):
 for external in (False,True):
    b=copy.deepcopy(base);b['name']=target;arr=[b]+([copy.deepcopy(hdmi)] if external else [])
    check(f'{target}-{external}-candidate',lambda a=arr,t=target:m.snapshot(a,t))
    restored=copy.deepcopy(arr);restored[0]['refreshRate']=60.061
    switched=[False]
    check(f'{target}-{external}-rehearsal',lambda a=arr,r=restored,t=target:m.rehearse(a,t,'instance_123',lambda: (r if switched[0] else a),lambda args: switched.__setitem__(0,True)))
    check(f'{target}-{external}-original',lambda r=restored,t=target:m.snapshot(r,t))
for key,value in [('width',0),('height',-1),('scale',0),('scale',float('nan')),('transform','0'),('disabled',1),('dpmsStatus',None),('refreshRate',75),('x','0'),('name','HDMI-A-1')]:
    b=copy.deepcopy(base);b[key]=value
    check('reject-'+key+'-'+str(value),lambda a=b:m.snapshot([a],'eDP-1'),True)
for key,value in [('x',1),('scale',1.25),('transform',1),('width',1280),('dpmsStatus',False)]:
    changed=copy.deepcopy(base);changed[key]=value
    check('drift-'+key,lambda c=changed:m.compare(m.snapshot([base],'eDP-1'),m.snapshot([c],'eDP-1')),True)
check('duplicate',lambda:m.snapshot([base,base],'eDP-1'),True)
check('unknown',lambda:m.snapshot([base,dict(hdmi,name='DP-1')],'eDP-1'),True)
check('bad-instance',lambda:m.command('../unsafe','eDP-1','original',base),True)
check('bad-mode',lambda:m.command('instance_123','eDP-1','unsafe',base),True)
check('wrong-connector',lambda:m.command('instance_123','eDP-2','original',base),True)
check('rollback-ignored',lambda:m.rehearse([base],'eDP-1','instance_123',lambda:[base],lambda a:None),True)
check('live-readonly',lambda:m.live('instance_123','eDP-1',lambda *a,**kw:type('P',(),{'stdout':json.dumps([base])})()))
check('live-no-mutator',lambda:m.live('instance_123','eDP-1',lambda cmd,**kw: (_ for _ in ()).throw(m.Denied('mutation')) if 'keyword' in cmd else type('P',(),{'stdout':json.dumps([base])})()))
assert all(r['passed'] for r in results),results
print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}))
