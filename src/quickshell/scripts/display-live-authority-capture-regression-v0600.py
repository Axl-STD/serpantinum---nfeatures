#!/usr/bin/env python3
"""Offline adversarial contract tests. No Hyprland commands or physical operations."""
import importlib.util,json,os
from pathlib import Path
import tempfile
s=importlib.util.spec_from_file_location('capture',Path(__file__).with_name('display-live-authority-capture-v0600.py'))
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
checks=[]
def test(name,fn,accept=True):
    try: value=fn();ok=bool(value) if accept else False
    except (m.CaptureDenied,OSError,ValueError,TypeError,KeyError,AttributeError):ok=not accept
    checks.append({'scenario':name,'passed':ok})
    if not ok:raise AssertionError(name)
base=[{'name':'eDP-1','width':1920},{'name':'HDMI-A-1','width':1920}]
test('edp1-valid',lambda:len(m.fingerprint(base))==64)
test('edp2-valid',lambda:len(m.fingerprint([{'name':'eDP-2'},{'name':'HDMI-A-1'}]))==64)
invalid=[[],[{'name':'eDP-1'}],[{'name':'eDP-1'},{'name':'eDP-2'}],[{'name':'eDP-1'},{'name':'eDP-1'}],[{'name':'HDMI-A-1'},{'name':'HDMI-A-1'}],[{'name':'DP-1'},{'name':'HDMI-A-1'}],[None,{'name':'HDMI-A-1'}],['eDP-1',{'name':'HDMI-A-1'}],{},'invalid',None,[{'name':'eDP-1'},{'name':'HDMI-A-1'},{'name':'DP-1'}],[{'name':'eDP-1'},{}]]
for i,value in enumerate(invalid):test('topology-rejected-'+str(i),lambda v=value:m.fingerprint(v),False)
test('digest-order-independent',lambda:m.fingerprint(base)==m.fingerprint(list(reversed(base))))
test('digest-change-detected',lambda:m.fingerprint(base)!=m.fingerprint([{'name':'eDP-1','width':1440},{'name':'HDMI-A-1','width':1920}]))
with tempfile.TemporaryDirectory() as td:
    root=Path(td);proc=root/'proc';proc.mkdir();runtime=root/'run';(runtime/'hypr'/'test-instance').mkdir(parents=True)
    os.chmod(runtime/'hypr'/'test-instance',0o700)
    import socket
    sock=socket.socket(socket.AF_UNIX);sock.bind(str(runtime/'hypr'/'test-instance'/'.socket.sock'))
    (proc/'123').mkdir();(proc/'123'/'stat').write_text('123 (Hyprland) S '+' '.join(['1']*19)+' 98765 '+' '.join(['1']*20))
    good=[{'pid':123,'instance':'test-instance'}]
    # Synthetic proc stat fixture: starttime field 22, index 19 after field 3.
    (proc/'123'/'stat').write_text('123 (Hyprland) S '+' '.join(['1']*18)+' 98765 '+' '.join(['1']*20))
    test('starttime-parsed',lambda:m.starttime(123,proc)==98765)
    test('identity-valid',lambda:m.identity(good,runtime,os.getuid(),proc)['pid']==123)
    test('identity-starttime',lambda:m.identity(good,runtime,os.getuid(),proc)['starttime']==98765)
    for i,value in enumerate([[],good+good,[{}],[{'pid':True,'instance':'test-instance'}],[{'pid':0,'instance':'test-instance'}],[{'pid':123,'instance':'../bad'}],[{'pid':123,'instance':''}],[{'pid':123,'instance':'missing'}],[{'pid':999,'instance':'test-instance'}],'bad']):
        test('identity-rejected-'+str(i),lambda v=value:m.identity(v,runtime,os.getuid(),proc),False)
    sock.close()
result={'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(result,indent=2))
