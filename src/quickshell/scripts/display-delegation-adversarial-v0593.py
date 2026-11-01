#!/usr/bin/env python3
"""v0.5.93 offline adversarial characterization. No display or systemd commands."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

HERE = Path(__file__).resolve().parent
MODULE = HERE / 'display_runtime_admission_v0592.py'

# Each scenario executes in its own process and disposable XDG runtime root.
CASES = {
 'fresh_owner': '''with a.admission(root): assert a.child_fds()''',
 'lock_contention': '''with a.admission(root):\n p=subprocess.run([sys.executable,'-c', 'import os,sys;sys.path.insert(0,sys.argv[1]);import display_runtime_admission_v0592 as a; a.admission(os.environ["XDG_RUNTIME_DIR"]).__enter__()',str(here)],env={k:v for k,v in os.environ.items() if k!=a.ENV},capture_output=True,text=True,timeout=5)\n assert p.returncode!=0''',
 'environment_only_rejected': '''os.environ[a.ENV]='999999'; expect_reject(lambda: a.admission(root).__enter__())''',
 'low_fd_rejected': '''os.environ[a.ENV]='1'; expect_reject(lambda: a.admission(root).__enter__())''',
 'non_numeric_fd_rejected': '''os.environ[a.ENV]='not-an-fd'; expect_reject(lambda: a.admission(root).__enter__())''',
 'closed_fd_rejected': '''fd=os.open(root/a.NAME,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.environ[a.ENV]=str(fd);expect_reject(lambda: a.admission(root).__enter__())''',
 'wrong_inode_rejected': '''(root/a.NAME).touch();fd=os.open(root/'other',os.O_CREAT|os.O_RDWR,0o600);os.environ[a.ENV]=str(fd);expect_reject(lambda: a.admission(root).__enter__());os.close(fd)''',
 'symlink_lock_rejected': '''(root/a.NAME).symlink_to(root/'other');expect_reject(lambda: a.admission(root).__enter__())''',
 'permissive_root_rejected': '''os.chmod(root,0o755);expect_reject(lambda: a.admission(root).__enter__())''',
 'permissive_lock_rejected': '''(root/a.NAME).touch(mode=0o600);os.chmod(root/a.NAME,0o644);expect_reject(lambda: a.admission(root).__enter__())''',
 'unlocked_fd_gap': '''fd=os.open(root/a.NAME,os.O_CREAT|os.O_RDWR,0o600);os.environ[a.ENV]=str(fd);\nwith a.admission(root): pass\nos.close(fd)''',
 'delegated_child': '''with a.admission(root):\n fd=a.child_fds()[0];code='import os,sys;sys.path.insert(0,sys.argv[1]);import display_runtime_admission_v0592 as a; a.admission(os.environ["XDG_RUNTIME_DIR"]).__enter__()';p=subprocess.run([sys.executable,'-c',code,str(here)],pass_fds=(fd,),capture_output=True,text=True,timeout=5);assert p.returncode==0,p.stderr''',
 'owner_release_reacquire': '''with a.admission(root): pass\nwith a.admission(root): pass''',
 'env_restored': '''with a.admission(root): assert a.ENV in os.environ\nassert a.ENV not in os.environ''',
}

RUNNER = '''import os,sys,subprocess,tempfile\nfrom pathlib import Path\nhere=Path(sys.argv[1]);sys.path.insert(0,str(here))\nimport display_runtime_admission_v0592 as a\nroot=Path(os.environ['XDG_RUNTIME_DIR'])\ndef expect_reject(fn):\n try: fn()\n except (a.AdmissionError,OSError,ValueError): return\n raise AssertionError('operation unexpectedly accepted')\n'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['selftest'])
    p.parse_args()
    if not MODULE.is_file() or MODULE.is_symlink():
        print(json.dumps({'selftest':'failed','error':'missing or unsafe dependency'}))
        return 2
    rows=[]
    for name, body in CASES.items():
        with tempfile.TemporaryDirectory(prefix='serpantinum-v0593-') as td:
            root=Path(td)/'runtime'
            root.mkdir(mode=0o700)
            env=os.environ.copy()
            env.pop('SERPANTINUM_RUNTIME_ADMISSION_FD',None)
            env.pop('PYTHONPATH',None)
            env['XDG_RUNTIME_DIR']=str(root)
            env['PYTHONDONTWRITEBYTECODE']='1'
            code=RUNNER+'\n'+textwrap.dedent(body)+'\n'
            try:
                r=subprocess.run([sys.executable,'-B','-c',code,str(HERE)],env=env,
                                 capture_output=True,text=True,timeout=8)
                passed=r.returncode==0
                detail=(r.stderr[-400:] if not passed else '')
            except subprocess.TimeoutExpired:
                passed=False
                detail='scenario timeout'
        rows.append({'scenario':name,'passed':passed,'classification': 'GAP' if name=='unlocked_fd_gap' and passed else ('PASS' if passed else 'FAIL'), 'detail':detail})
    ok=all(x['passed'] for x in rows)
    print(json.dumps({'version':'0.5.93','mode':'offline-adversarial-characterization',
      'selftest':'passed' if ok else 'failed','totalScenarios':len(rows),'results':rows,
      'unlockedDescriptorGapReproduced':next(x['passed'] for x in rows if x['scenario']=='unlocked_fd_gap'),
      'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,
      'systemdIntegration':False,'productionModified':False,'executionAuthorized':False,
      'physicalExecutionEnabled':False,'commandsExecuted':0,
      'limitations':['Unlocked matching descriptor can acquire lock in delegated branch',
                     'No independently authenticated worker identity',
                     'No physical or systemd runtime evidence']},indent=2))
    return 0 if ok else 2

if __name__=='__main__':
    sys.exit(main())
