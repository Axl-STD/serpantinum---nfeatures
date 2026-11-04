#!/usr/bin/env python3
"""Offline regression for physical snapshot binding; no display commands."""
import copy, importlib.util, json, pathlib, secrets, tempfile, time
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('binding',HERE/'display-physical-snapshot-binding-v0600.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
base={'name':'eDP-1','description':'AU Optronics B156HAN15.H','width':1920,'height':1080,'refreshRate':60.061,'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True}
external=dict(base,name='HDMI-A-1',description='Dell monitor',refreshRate=60.0,x=1920)
cap={'decision':'DYNAMIC_TOPOLOGY_READONLY_READY','target':'eDP-1','topology':'internal-only','persistentSource':'config/monitors.lua','captureAtomic':False,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'compositor':{'instance':'abc12345','pid':981,'starttime':123},'luaSha256':'a'*64,'monitorDigest':'b'*64}
results=[]
def check(name, fn, allowed=True):
    try:
        fn(); passed=allowed
    except (b.Denied, b.ipc.Denied, OSError, ValueError, TypeError, KeyError):
        passed=not allowed
    results.append({'scenario':name,'passed':passed})

def setup(mons=None, capture=None):
    tmp=tempfile.TemporaryDirectory(prefix='snapshot-reg-')
    root=pathlib.Path(tmp.name)/'transaction';root.mkdir(mode=0o700)
    m=copy.deepcopy(mons if mons is not None else [base]); c=copy.deepcopy(capture if capture is not None else cap)
    rec={'schema':1,'id':secrets.token_hex(32),'generation':1,'state':'PREPARED','target':c['target'],'topology':c['topology'],'plan_sha256':'c'*64,'boot_id':'boot-test','deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)+30_000_000_000,'phase':'NONE'}
    b.ipc.write(root,'transaction.json',rec)
    return tmp,root,m,c

for name,mut in [
 ('baseline',lambda m,c:None),('wrong_width',lambda m,c:m[0].update(width=1280)),('wrong_height',lambda m,c:m[0].update(height=720)),
 ('wrong_x',lambda m,c:m[0].update(x=1)),('wrong_y',lambda m,c:m[0].update(y=1)),('wrong_scale',lambda m,c:m[0].update(scale=1.25)),
 ('wrong_transform',lambda m,c:m[0].update(transform=1)),('disabled',lambda m,c:m[0].update(disabled=True)),
 ('dpms_off',lambda m,c:m[0].update(dpmsStatus=False)),('candidate_mode',lambda m,c:m[0].update(refreshRate=144.06)),
 ('bad_description',lambda m,c:m[0].update(description='')),('missing_field',lambda m,c:m[0].pop('scale')),
 ('duplicate',lambda m,c:m.append(copy.deepcopy(m[0]))),('unknown_connector',lambda m,c:m[0].update(name='DP-1')),
 ('bad_capture',lambda m,c:c.update(decision='BLOCK_AND_INVESTIGATE')),('capture_atomic',lambda m,c:c.update(captureAtomic=True)),
 ('capture_authority',lambda m,c:c.update(executionAuthorized=True)),('capture_target',lambda m,c:c.update(target='eDP-2')),
 ('capture_topology',lambda m,c:c.update(topology='internal-and-hdmi')),('capture_source',lambda m,c:c.update(persistentSource='monitors.conf')),
 ('capture_digest',lambda m,c:c.update(luaSha256='broken')),('capture_instance',lambda m,c:c['compositor'].update(instance='')),
 ('capture_pid',lambda m,c:c['compositor'].update(pid='981')),
 ('bad_refresh_type',lambda m,c:m[0].update(refreshRate='60.06')),
 ('bad_scale_type',lambda m,c:m[0].update(scale=True)),('bad_disabled_type',lambda m,c:m[0].update(disabled=0)),
 ('bad_position_type',lambda m,c:m[0].update(x=0.5)),('bad_dimensions_type',lambda m,c:m[0].update(width=True)),
]:
    t,r,m,c=setup();mut(m,c)
    check(name,lambda:b.bind(r,m,c),name=='baseline');t.cleanup()

for name,change in [('inspect_clean',lambda r,m,c:None),('drift_frequency',lambda r,m,c:m[0].update(refreshRate=60.1)),
 ('drift_pid',lambda r,m,c:c['compositor'].update(pid=999)),('drift_lua',lambda r,m,c:c.update(luaSha256='d'*64)),
 ('drift_topology',lambda r,m,c:m.append(copy.deepcopy(external))),
 ('tampered_sidecar',lambda r,m,c:b.ipc.write(r,'physical-snapshot.json',{'fake':1})),
 ('changed_transaction',lambda r,m,c:b.ipc.write(r,'transaction.json',dict(b.ipc.read(r,'transaction.json'),id='f'*64))),
 ('advanced_generation',lambda r,m,c:b.ipc.write(r,'transaction.json',dict(b.ipc.read(r,'transaction.json'),generation=4))),
 ('different_boot',lambda r,m,c:b.ipc.write(r,'transaction.json',dict(b.ipc.read(r,'transaction.json'),boot_id='new'))),
]:
    t,r,m,c=setup();b.bind(r,m,c);change(r,m,c)
    check(name,lambda:b.inspect(r,m,c),name in ('inspect_clean','advanced_generation'));t.cleanup()

# Dual-monitor topology and alternate connector.
for name,mons,capture in [('dual',[base,external],dict(cap,topology='internal-and-hdmi')),
 ('alternate',[dict(base,name='eDP-2')],dict(cap,target='eDP-2'))]:
    t,r,m,c=setup(mons,capture)
    check(name,lambda:(b.bind(r,m,c),b.inspect(r,m,c)));t.cleanup()

passed=all(x['passed'] for x in results)
print(json.dumps({'selftest':'passed' if passed else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
raise SystemExit(0 if passed else 1)
