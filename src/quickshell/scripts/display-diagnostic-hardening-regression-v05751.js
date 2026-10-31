#!/usr/bin/env node
'use strict';
// v0.5.75.1: extracted diagnostic lifecycle callbacks; offline, no subprocesses.
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
const base={assessment:'stable-observation',underlyingAssessment:'consistent-observable',executionAuthorized:false,finalizationAuthorized:false,reservationReusable:false,physicalExecutionEnabled:false,physicalRollbackVerified:false,atomicCrossStoreSnapshot:false,commandsExecuted:0};
function fixture(){let starts=0;const p={_running:false};Object.defineProperty(p,'running',{get(){return this._running},set(v){if(v&&!this._running)starts++;this._running=v;}});let watchdogActive=false,graceActive=false;const w={start(){watchdogActive=true},stop(){watchdogActive=false}},g={start(){graceActive=true},stop(){graceActive=false}};const root={diagnosticRoot:'/tmp/test',diagnosticBusy:false,diagnosticBlocked:false,diagnosticTimedOut:false,diagnosticResponse:'',diagnosticResponseValid:false,diagnosticAssessment:'diagnostic-unavailable',diagnosticUnderlyingAssessment:'',diagnosticMessageKey:'not_requested'};const ctx=vm.createContext({displayTabRoot:root,displayDiagnosticProcess:p,diagnosticWatchdog:w,diagnosticCancelGrace:g,JSON,Error});function run(body,args=[],that=null,withRoot=false){return new vm.Script('(function('+ (args.length?'exitCode,exitStatus':'') +'){'+(withRoot?'with(displayTabRoot){':'')+body+(withRoot?'}':'')+'\n})').runInContext(ctx,{timeout:1000}).apply(that,args);}return {root,p,starts:()=>starts,watchdogActive:()=>watchdogActive,graceActive:()=>graceActive,request:()=>run(request,[],null,true),stdout:t=>run(stdout,[],{text:t}),exit:(a,b)=>{p._running=false;run(exited,[a,b]);},timeout:()=>run(watchdog),grace:()=>run(grace)};}
const results=[];function test(name,fn){fn();results.push({scenario:name,passed:true});}
test('request-arms-watchdog',()=>{const f=fixture();f.request();assert(f.watchdogActive());assert.equal(f.starts(),1);});
test('repeated-request-no-overlap',()=>{const f=fixture();f.request();f.request();assert.equal(f.starts(),1);});
test('success-disarms-watchdog',()=>{const f=fixture();f.request();f.stdout(JSON.stringify(base));f.exit(0,0);assert.equal(f.root.diagnosticAssessment,'stable-observation');assert(!f.watchdogActive());});
test('timeout-requests-cancellation',()=>{const f=fixture();f.request();f.timeout();assert(f.root.diagnosticTimedOut);assert(f.graceActive());assert(!f.p.running);assert(f.root.diagnosticBusy);});
test('late-stdout-rejected',()=>{const f=fixture();f.request();f.timeout();f.stdout(JSON.stringify(base));assert(!f.root.diagnosticResponseValid);assert.equal(f.root.diagnosticResponse,'');});
test('timeout-exit-fails-closed',()=>{const f=fixture();f.request();f.stdout(JSON.stringify(base));f.timeout();f.exit(0,0);assert.equal(f.root.diagnosticAssessment,'observation-failed');assert.equal(f.root.diagnosticMessageKey,'process_failed');assert(!f.root.diagnosticBusy);});
test('timeout-exit-allows-manual-retry',()=>{const f=fixture();f.request();f.timeout();f.exit(15,1);f.request();assert.equal(f.starts(),2);});
test('unconfirmed-cancellation-blocks',()=>{const f=fixture();f.request();f.timeout();f.grace();assert(f.root.diagnosticBlocked);f.request();assert.equal(f.starts(),1);});
test('blocked-exit-retains-block',()=>{const f=fixture();f.request();f.timeout();f.grace();f.exit(0,0);assert(f.root.diagnosticBlocked);assert.equal(f.root.diagnosticAssessment,'observation-failed');});
test('no-auto-retry',()=>{const f=fixture();f.request();f.timeout();f.exit(15,1);assert.equal(f.starts(),1);});
console.log(JSON.stringify({version:'0.5.75.1',mode:'offline-extracted-diagnostic-lifecycle',selftest:'passed',totalScenarios:results.length,results,hardeningComplete:true,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Simulated event ordering; no Quickshell runtime proof','No physical configuration executed']},null,2));
