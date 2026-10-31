#!/usr/bin/env node
'use strict';
// v0.5.75: source-bound offline lifecycle characterization. No external processes.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.resolve(__dirname,'../guide/display/DisplayMainState.qml'),'utf8');
const begin=source.indexOf('id: displayTabRoot');
const end=source.indexOf('property var defaultDisplaySettings:',begin);
assert(begin>=0 && end>begin,'Diagnostic region missing');
const region=source.slice(begin,end);
function extract(marker,from=0){
 const at=region.indexOf(marker,from); assert(at>=0,'Missing '+marker);
 const open=region.indexOf('{',at+marker.length); assert(open>=0,'No opening brace '+marker);
 let depth=1,quote='',line=false,block=false;
 for(let i=open+1;i<region.length;i++){
  const c=region[i],n=region[i+1];
  if(line){if(c==='\n')line=false;continue;}
  if(block){if(c==='*'&&n==='/'){block=false;i++;}continue;}
  if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}
  if(c==='/'&&n==='/'){line=true;i++;continue;}
  if(c==='/'&&n==='*'){block=true;i++;continue;}
  if(c==='"'||c==="'"||c==='`'){quote=c;continue;}
  if(c==='{')depth++;
  if(c==='}'&&--depth===0)return region.slice(open+1,i);
 }
 throw Error('Unbalanced '+marker);
}
const requestBody=extract('function requestDisplayDiagnostic()');
const processAt=region.indexOf('id: displayDiagnosticProcess');
const stdoutBody=extract('onStreamFinished:',processAt);
const exitBody=extract('onExited: (exitCode, exitStatus) =>',processAt);
assert(requestBody.includes('diagnosticBusy || displayDiagnosticProcess.running'),'Serialization guard missing');
assert(exitBody.includes('exitStatus !== 0'),'Abnormal exit guard missing');
const request=new vm.Script('(function(){with(displayTabRoot){'+requestBody+'\n}})');
const stdout=new vm.Script('(function(){'+stdoutBody+'\n})');
const exited=new vm.Script('(function(exitCode,exitStatus){'+exitBody+'\n})');
const base={assessment:'stable-observation',underlyingAssessment:'consistent-observable',executionAuthorized:false,finalizationAuthorized:false,reservationReusable:false,physicalExecutionEnabled:false,physicalRollbackVerified:false,atomicCrossStoreSnapshot:false,commandsExecuted:0};
function fixture(rootPath='/tmp/display-diagnostic-fixture'){
 let starts=0;
 const proc={_running:false};
 Object.defineProperty(proc,'running',{get(){return this._running},set(v){if(v&&!this._running)starts++;this._running=v;}});
 const root={diagnosticRoot:rootPath,diagnosticBlocked:false,diagnosticTimedOut:false,diagnosticBusy:false,diagnosticResponse:'',diagnosticResponseValid:false,diagnosticAssessment:'diagnostic-unavailable',diagnosticUnderlyingAssessment:'',diagnosticMessageKey:'not_requested'};
 const context=vm.createContext({displayTabRoot:root,displayDiagnosticProcess:proc,diagnosticWatchdog:{start(){},stop(){}},diagnosticCancelGrace:{start(){},stop(){}},JSON,Error});
 return {root,proc,context,starts:()=>starts,request:()=>request.runInContext(context,{timeout:1000})(),stdout:text=>stdout.runInContext(context,{timeout:1000}).call({text}),exit:(code,status)=>{proc._running=false;exited.runInContext(context,{timeout:1000})(code,status);}};
}
const results=[];
function test(name,fn){fn();results.push({scenario:name,passed:true});}
test('missing-root-rejected',()=>{const f=fixture('');f.request();assert.equal(f.starts(),0);assert.equal(f.root.diagnosticMessageKey,'not_configured');});
test('relative-root-rejected',()=>{const f=fixture('relative');f.request();assert.equal(f.starts(),0);});
test('single-request-starts',()=>{const f=fixture();f.request();assert.equal(f.starts(),1);assert.equal(f.root.diagnosticBusy,true);});
test('repeated-busy-requests-ignored',()=>{const f=fixture();f.request();for(let i=0;i<5;i++)f.request();assert.equal(f.starts(),1);});
test('running-process-guard',()=>{const f=fixture();f.proc._running=true;f.request();assert.equal(f.starts(),0);});
test('request-clears-stale-response',()=>{const f=fixture();f.root.diagnosticResponse='stale';f.root.diagnosticResponseValid=true;f.request();assert.equal(f.root.diagnosticResponse,'');assert.equal(f.root.diagnosticResponseValid,false);});
test('valid-exit-publishes',()=>{const f=fixture();f.request();f.stdout(JSON.stringify(base));f.exit(0,0);assert.equal(f.root.diagnosticAssessment,'stable-observation');assert.equal(f.root.diagnosticBusy,false);});
test('abnormal-exit-rejected',()=>{const f=fixture();f.request();f.stdout(JSON.stringify(base));f.exit(0,1);assert.equal(f.root.diagnosticMessageKey,'process_failed');});
test('failed-exit-rejected',()=>{const f=fixture();f.request();f.stdout(JSON.stringify(base));f.exit(2,0);assert.equal(f.root.diagnosticAssessment,'observation-failed');});
test('no-stdout-rejected',()=>{const f=fixture();f.request();f.exit(0,0);assert.equal(f.root.diagnosticMessageKey,'process_failed');});
test('retry-after-confirmed-exit',()=>{const f=fixture();f.request();f.exit(2,0);f.request();assert.equal(f.starts(),2);});
test('diagnostic-watchdog-present',()=>{assert(/id:\s*diagnosticWatchdog/.test(region));assert(/id:\s*diagnosticCancelGrace/.test(region));});
test('stdout-guarded-by-lifecycle',()=>{assert(stdoutBody.includes('diagnosticBusy')&&stdoutBody.includes('diagnosticTimedOut')&&stdoutBody.includes('diagnosticBlocked'));});
const findings=[];
console.log(JSON.stringify({version:'0.5.75.1',mode:'source-bound-offline-lifecycle-characterization',selftest:'passed',totalScenarios:results.length,results,hardeningComplete:true,findings,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Simulated QML handlers in Node VM; no Quickshell signal-order proof','No compositor or systemd calls']},null,2));
