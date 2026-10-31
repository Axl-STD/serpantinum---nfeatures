#!/usr/bin/env node
'use strict';
// v0.5.76: recovery characterization using extracted diagnostic lifecycle callbacks; offline, no subprocesses.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.resolve(__dirname,'../guide/display/DisplayMainState.qml'),'utf8');
const region=source.slice(source.indexOf('id: displayTabRoot'),source.indexOf('property var defaultDisplaySettings:'));
function extract(marker,from=0){const at=region.indexOf(marker,from);assert(at>=0,marker);const open=region.indexOf('{',at+marker.length);let depth=1,q='',line=false,block=false;for(let i=open+1;i<region.length;i++){let c=region[i],n=region[i+1];if(line){if(c==='\n')line=false;continue;}if(block){if(c==='*'&&n==='/'){block=false;i++;}continue;}if(q){if(c==='\\'){i++;continue;}if(c===q)q='';continue;}if(c==='/'&&n==='/'){line=true;i++;continue;}if(c==='/'&&n==='*'){block=true;i++;continue;}if(c==='"'||c==="'"||c==='`'){q=c;continue;}if(c==='{')depth++;if(c==='}'&&--depth===0)return region.slice(open+1,i);}throw Error('unbalanced '+marker);}
const processAt=region.indexOf('id: displayDiagnosticProcess');
const request=extract('function requestDisplayDiagnostic()');
const stdout=extract('onStreamFinished:',processAt);
const exited=extract('onExited: (exitCode, exitStatus) =>',processAt);
const watchdog=extract('onTriggered:',region.indexOf('id: diagnosticWatchdog'));
const grace=extract('onTriggered:',region.indexOf('id: diagnosticCancelGrace'));
const recover=extract('function recoverDisplayDiagnostic()');
const base={assessment:'stable-observation',underlyingAssessment:'consistent-observable',executionAuthorized:false,finalizationAuthorized:false,reservationReusable:false,physicalExecutionEnabled:false,physicalRollbackVerified:false,atomicCrossStoreSnapshot:false,commandsExecuted:0};
function fixture(){let starts=0;const p={_running:false};Object.defineProperty(p,'running',{get(){return this._running},set(v){if(v&&!this._running)starts++;this._running=v;}});let watchdogActive=false,graceActive=false;const w={start(){watchdogActive=true},stop(){watchdogActive=false}},g={start(){graceActive=true},stop(){graceActive=false}};const root={diagnosticRoot:'/tmp/test',diagnosticBusy:false,diagnosticBlocked:false,diagnosticExitConfirmed:false,diagnosticTimedOut:false,diagnosticResponse:'',diagnosticResponseValid:false,diagnosticAssessment:'diagnostic-unavailable',diagnosticUnderlyingAssessment:'',diagnosticMessageKey:'not_requested'};Object.defineProperty(root,'diagnosticRecoveryAvailable',{get(){return this.diagnosticBlocked&&this.diagnosticExitConfirmed&&!this.diagnosticBusy&&!p.running;}});const ctx=vm.createContext({displayTabRoot:root,displayDiagnosticProcess:p,diagnosticWatchdog:w,diagnosticCancelGrace:g,JSON,Error});function run(body,args=[],that=null,withRoot=false){return new vm.Script('(function('+ (args.length?'exitCode,exitStatus':'') +'){'+(withRoot?'with(displayTabRoot){':'')+body+(withRoot?'}':'')+'\n})').runInContext(ctx,{timeout:1000}).apply(that,args);}return {root,p,starts:()=>starts,watchdogActive:()=>watchdogActive,graceActive:()=>graceActive,request:()=>run(request,[],null,true),stdout:t=>run(stdout,[],{text:t}),exit:(a,b)=>{p._running=false;run(exited,[a,b]);},timeout:()=>run(watchdog),grace:()=>run(grace),recover:()=>run(recover,[],null,true)};}
const results=[];
function test(name, fn) {
  try { const x=fn(); results.push({scenario:name,passed:true,...(x||{})}); }
  catch(e) { results.push({scenario:name,passed:false,error:String(e)}); }
}
// This test suite deliberately injects callbacks in orders that may be impossible
// in Quickshell. A finding is a state-machine exposure, not a runtime exploit.
test('duplicate-exit-after-success',()=>{
 const f=fixture(); f.request(); f.stdout(JSON.stringify(base)); f.exit(0,0);
 assert.equal(f.root.diagnosticAssessment,'stable-observation');
 f.exit(3,1);
 assert.equal(f.root.diagnosticAssessment,'stable-observation');
 return {acceptedDuplicate:false};
});
test('late-stdout-after-exit',()=>{
 const f=fixture(); f.request(); f.stdout(JSON.stringify(base)); f.exit(0,0);
 const before=f.root.diagnosticResponse; f.stdout('late-old-output');
 assert.equal(f.root.diagnosticResponse,before);
 return {acceptedLateOutput:false};
});
test('stale-stdout-after-retry-injected',()=>{
 const f=fixture(); f.request(); f.exit(2,0); f.request();
 f.stdout('old-run-output');
 const accepted=f.root.diagnosticResponseValid && f.root.diagnosticResponse==='old-run-output';
 return {acceptedStaleOutput:accepted, finding:accepted?'STALE_STDOUT_STATE_EXPOSURE':null};
});
test('stale-exit-after-retry-injected',()=>{
 const f=fixture(); f.request(); f.exit(2,0); f.request();
 // Deliver the old exit callback while the second process is still marked running.
 // Unlike fixture.exit(), this does not change process.running.
 f.root.diagnosticExitConfirmed=false;
 f.root.diagnosticResponseValid=false;
 f.root.diagnosticResponse='';
 const ctx=vm.createContext({displayTabRoot:f.root,diagnosticWatchdog:{stop(){}},diagnosticCancelGrace:{stop(){}},JSON,Error});
 new vm.Script('(function(exitCode,exitStatus){'+exited+'\n})').runInContext(ctx)(0,0);
 const accepted=f.root.diagnosticExitConfirmed || !f.root.diagnosticBusy;
 return {acceptedStaleExit:accepted, finding:accepted?'STALE_EXIT_STATE_EXPOSURE':null};
});
test('recovery-after-stale-exit-injected',()=>{
 const f=fixture(); f.request(); f.timeout(); f.grace(); f.exit(15,1);
 assert(f.root.diagnosticRecoveryAvailable);
 f.recover(); f.request();
 // Simulate a stale exit signal while new run is still active.
 const ctx=vm.createContext({displayTabRoot:f.root,diagnosticWatchdog:{stop(){}},diagnosticCancelGrace:{stop(){}},JSON,Error});
 new vm.Script('(function(exitCode,exitStatus){'+exited+'\n})').runInContext(ctx)(15,1);
 const altered=!f.root.diagnosticBusy || f.root.diagnosticExitConfirmed;
 return {staleExitAlteredNewRun:altered, finding:altered?'RECOVERED_RUN_STALE_EXIT_EXPOSURE':null};
});
const findings=results.filter(x=>x.finding).map(x=>x.finding);
const selftest=results.every(x=>x.passed)?'passed':'failed';
console.log(JSON.stringify({version:'0.5.77.3',mode:'source-bound-offline-adversarial-signal-characterization',selftest,totalScenarios:results.length,results,findings,productionModified:false,quickshellRuntimeProof:false,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Injected stale callbacks are adversarial simulations, not observed Quickshell behavior','No compositor commands, subprocesses, or physical configuration']},null,2));
if(selftest!=='passed')process.exitCode=1;
