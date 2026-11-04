#!/usr/bin/env python3
"""Offline source/API safety checks; full worker runtime regressions still required."""
import ast,importlib.util,json,os,stat,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
source=HERE/'display-continuous-fenced-authority-v0600.py'
spec=importlib.util.spec_from_file_location('continuous_fenced_test',source)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
rows=[]
def check(name,ok):
    rows.append({'scenario':name,'passed':bool(ok)})
    if not ok: raise AssertionError(name)
try:
    tree=ast.parse(source.read_text())
    check('module-parses',isinstance(tree,ast.Module))
    check('class-present',hasattr(mod,'FencedContinuousSession'))
    check('inherits-continuous',issubclass(mod.FencedContinuousSession,mod.continuous.ContinuousSession))
    check('transition-override','transition' in mod.FencedContinuousSession.__dict__)
    check('begin-override','begin' in mod.FencedContinuousSession.__dict__)
    check('decision-override','decision' in mod.FencedContinuousSession.__dict__)
    check('report-override','report' in mod.FencedContinuousSession.__dict__)
    check('no-fencedjournal-nested','FencedJournal(' not in source.read_text())
    check('no-subprocess',not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('Popen','run','system') for n in ast.walk(tree)))
    check('no-physical-commands',not any(x in source.read_text() for x in ('hyprctl','wlr-randr','systemctl','systemd-run')))
    with tempfile.TemporaryDirectory(prefix='serpantinum-fenced-check-') as tmp:
        p=Path(tmp);os.chmod(p,0o700)
        check('private-fixture',stat.S_IMODE(p.stat().st_mode)==0o700)
        check('empty-fence-decision',mod.fence.decision(p)=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        record=mod.fence.begin(p,'offline-probe','digest')
        check('schema2',record['schema']==2)
        check('recovery-required',record['recovery_required'] is True)
        check('session-id-length',len(record['session_id'])==64)
        check('identity-present',bool(record['coordinator_identity']))
        check('journal-roundtrip',mod.fence.read(p)==record)
        check('fence-prepared',record['phase']=='prepared')
        check('decision-blocks',mod.fence.decision(p)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        try: mod.fence.begin(p,'offline-probe','digest')
        except mod.fence.FenceDenied: duplicate=True
        else: duplicate=False
        check('duplicate-rejected',duplicate)
        try: mod.fence.advance(p,'wrong',1,'applying')
        except mod.fence.FenceDenied: wrong=True
        else: wrong=False
        check('foreign-session-rejected',wrong)
        updated=mod.fence.advance(p,record['session_id'],1,'applying')
        check('valid-advance',updated['generation']==2)
        check('persisted-advance',mod.fence.read(p)==updated)
        check('recovery-remains-required',updated['recovery_required'] is True)
    print(json.dumps({'version':'0.6.0','mode':'offline-fenced-continuous-smoke',
        'selftest':'passed','totalScenarios':len(rows),'results':rows,
        'workerHandshakeRuntimeVerified':False,'persistentFencingIntegrated':False,
        'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,
        'limitations':['Source and journal smoke only; no worker handshake executed',
        'No concurrent revocation or SIGKILL lifecycle proof',
        'Cooperative same-UID protocol, not physical authorization']},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':str(exc),'results':rows,'commandsExecuted':0}))
    sys.exit(2)
