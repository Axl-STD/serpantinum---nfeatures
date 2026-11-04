#!/usr/bin/env python3
"""Offline/service recovery resilience; no Hyprland writes. --systemd uses real user systemd."""
import argparse, concurrent.futures, importlib.util, json, os, pathlib, secrets, subprocess, sys, tempfile, time
D=pathlib.Path(__file__).resolve().parent
P=D/'display-physical-shared-transport-v0600.py'
S=D/'display-recovery-service-v0600.py'
assert P.is_file() and not P.is_symlink() and S.is_file() and not S.is_symlink()
spec=importlib.util.spec_from_file_location('shared_resilience',P)
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
ipc=w.ipc

def fixture(root):
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    base=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    r=dict(schema=1,id=secrets.token_hex(32),generation=4,state='ROLLBACK_REQUIRED',phase='APPLY_VERIFIED',execution_backend='physical',target='eDP-1',topology='internal-only',plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1000000000)
    snap=dict(transaction_id=r['id'],boot_id=boot,target=r['target'],topology=r['topology'],snapshot=[base],snapshot_sha256=w.sha([base]),identity={'instance':'lab_instance'})
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    receipt=dict(transaction_id=r['id'],transaction_digest=w.sha({k:r[k] for k in keys}),status='ARMED_LAB_ONLY')
    with ipc.locked(root) as p:
        for name,val in [('transaction.json',r),('physical-snapshot.json',snap),('physical-watchdog-rehearsal.json',receipt),('shared-lab-monitor.json',dict(schema=1,instance='lab_instance',monitors=[dict(base,refreshRate=144.06)]))]:ipc.write(p,name,val)
    return r['id']

def call(root,action='recover'):
    return subprocess.run([sys.executable,'-B',str(S),action,str(root),'--max-wait','3'],capture_output=True,text=True,timeout=12)

def verify(root,tid):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json');v=ipc.read(p,'shared-lab-monitor.json')
        assert r['id']==tid and r['state']=='ROLLED_BACK' and r['phase']=='COMPLETED' and r['generation']==6
        assert v['monitors'][0]['refreshRate']==60.061
        return r

def scenario(kind,systemd=False):
    with tempfile.TemporaryDirectory(prefix='serpantinum-service-resilience-') as tmp:
        root=pathlib.Path(tmp);root.chmod(0o700);tid=fixture(root)
        if kind in ('intent','effect'):
            fail='after-intent' if kind=='intent' else 'after-effect'
            code='import importlib.util,os,signal,sys; p=sys.argv[1]; r=sys.argv[2]; s=importlib.util.spec_from_file_location("killshared",p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m); x=m.recover_lab(r,m.SharedLabTransport(r),failpoint="'+fail+'"); assert x["decision"]=="LAB_INTERRUPTED_'+('AFTER_INTENT' if kind=='intent' else 'AFTER_EFFECT')+'";os.kill(os.getpid(),signal.SIGKILL)'
            proc=subprocess.run([sys.executable,'-B','-c',code,str(P),str(root)],capture_output=True,timeout=12)
            assert proc.returncode == -9,(proc.returncode,proc.stderr)
            r=ipc.read(root,'transaction.json');assert r['state']=='UNCERTAIN' and r['phase']=='ROLLBACK_INTENT'
        if kind=='identity':
            with ipc.locked(root) as p:
                v=ipc.read(p,'shared-lab-monitor.json');v['instance']='changed';ipc.write(p,'shared-lab-monitor.json',v)
        if kind=='receipt':
            with ipc.locked(root) as p:
                v=ipc.read(p,'physical-watchdog-rehearsal.json');v['status']='ARMED_PHYSICAL';ipc.write(p,'physical-watchdog-rehearsal.json',v)
        if kind=='corrupt':
            with ipc.locked(root) as p:
                (p/'shared-lab-monitor.json').write_text('{broken')
        if kind=='concurrent':
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                runs=list(pool.map(lambda _:call(root),range(4)))
            assert all(p.returncode==0 for p in runs),[p.stderr for p in runs]
        elif systemd and kind in ('normal','intent','effect'):
            unit='serpantinum-v0600-resilience-'+secrets.token_hex(5)
            proc=subprocess.run(['systemd-run','--user','--wait','--unit='+unit,'--property=Type=oneshot','--property=TimeoutStartSec=15s',sys.executable,'-B',str(S),'recover',str(root),'--max-wait','3'],capture_output=True,text=True,timeout=30)
            assert proc.returncode==0,(proc.stdout,proc.stderr)
            journal=subprocess.run(['journalctl','--user','-u',unit+'.service','-o','cat','--no-pager'],capture_output=True,text=True,timeout=10)
            assert 'LAB_RECOVERY_VERIFIED_NOT_PHYSICAL' in journal.stdout,(journal.stdout,journal.stderr)
        else:
            proc=call(root);assert (proc.returncode!=0 if kind in ('identity','receipt','corrupt') else proc.returncode==0),(kind,proc.stdout,proc.stderr)
        if kind in ('identity','receipt','corrupt'):
            assert ipc.read(root,'transaction.json')['state']!='ROLLED_BACK'
        else:
            verify(root,tid)
            before=(root/'transaction.json').read_bytes(),(root/'shared-lab-monitor.json').read_bytes()
            assert call(root).returncode==0
            assert before==((root/'transaction.json').read_bytes(),(root/'shared-lab-monitor.json').read_bytes())
        return dict(scenario=kind,passed=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--systemd',action='store_true');args=parser.parse_args()
    kinds=['normal','intent','effect','concurrent','identity','receipt','corrupt']
    results=[]
    for kind in kinds:results.append(scenario(kind,args.systemd))
    print(json.dumps(dict(selftest='passed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
if __name__=='__main__':main()
