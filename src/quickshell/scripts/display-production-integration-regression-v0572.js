#!/usr/bin/env node
'use strict';
// v0.5.72: production handler integration with simulated JSON. No external processes.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.resolve(__dirname, '../guide/display/DisplayMainState.qml'), 'utf8');
function extract(marker, from = 0) {
  const at = source.indexOf(marker, from);
  if (at < 0) throw Error('Missing QML marker: ' + marker);
  const open = source.indexOf('{', at + marker.length);
  if (open < 0) throw Error('Missing body: ' + marker);
  let depth=1, quote='', line=false, block=false;
  for(let i=open+1;i<source.length;i++) {
    const c=source[i], n=source[i+1];
    if(line){if(c==='\n')line=false;continue;}
    if(block){if(c==='*'&&n==='/'){block=false;i++;}continue;}
    if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}
    if(c==='/'&&n==='/'){line=true;i++;continue;}
    if(c==='/'&&n==='*'){block=true;i++;continue;}
    if(c==='"'||c==="'"||c==='`'){quote=c;continue;}
    if(c==='{')depth++;
    if(c==='}'&&--depth===0)return source.slice(open+1,i);
  }
  throw Error('Unbalanced body: '+marker);
}
const processAt=source.indexOf('id: monitorDetector');
assert(processAt>=0);
const handlers={
  reload:extract('function reloadMonitors()'),
  start:extract('function startQueuedMonitorDetection()'),
  stdout:extract('onStreamFinished:',processAt),
  exited:extract('onExited: (exitCode, exitStatus) =>',processAt),
  timeout:extract('onTriggered:',source.indexOf('id: monitorDetectionWatchdog')),
  grace:extract('onTriggered:',source.indexOf('id: monitorDetectionCancelGrace'))
};
assert(handlers.stdout.includes('monitorDetectionCandidate = mList'));
assert(handlers.exited.includes('exitCode === 0')&&handlers.exited.includes('exitStatus === 0'));
const compile=(body,args='')=>new vm.Script(`(function(${args}){${body}\n})`);
const compiled={reload:compile('with(displayTabRoot){'+handlers.reload+'}'),start:compile('with(displayTabRoot){'+handlers.start+'}'),stdout:compile(handlers.stdout),exited:compile(handlers.exited,'exitCode,exitStatus'),timeout:compile(handlers.timeout),grace:compile(handlers.grace)};
function fixture(){
  const counts={starts:0,exits:0,published:0,reconciled:0,recorded:0,watchStart:0,watchStop:0,graceStart:0,graceStop:0};
  const original=[{name:'OLD',dimensions:'1280x720',scale:1}];
  const pending={OLD:{scale:1.25}};
  const deferred=[];
  const detector={_running:false,get running(){return this._running;},set running(v){if(v&&!this._running)counts.starts++;this._running=v;}};
  const root={compositor:'hyprland',monitorDetectionBlocked:false,monitorDetectionInFlight:false,monitorDetectionQueued:false,monitorDetectionTimedOut:false,monitorDetectionTimeouts:0,monitorDetectionReceivedValidOutput:false,monitorDetectionCandidate:null,monitorDetectionStatus:'unavailable',monitorTopologyVerifiedOnce:false,monitorsList:original,pendingDisplayChanges:pending,pendingMonScaleName:'',selectedMonitorName:'OLD',scaleDebounceTimer:{stop(){}},reconcilePendingDisplayChanges(){counts.reconciled++;},recordSuccessfulMonitorDetection(){counts.recorded++;},ensureDisplaySelection(){},refreshDisplaySettings(){},startQueuedMonitorDetection(){invoke('start');}};
  const context=vm.createContext({displayTabRoot:root,monitorDetector:detector,monitorDetectionWatchdog:{start(){counts.watchStart++;},stop(){counts.watchStop++;}},monitorDetectionCancelGrace:{start(){counts.graceStart++;},stop(){counts.graceStop++;}},Qt:{callLater(fn){deferred.push(fn);}},Array,JSON,Math,Number,String,Object});
  function invoke(name,...args){return compiled[name].runInContext(context,{timeout:1000}).apply(name==='stdout'?{text:args[0]}:undefined,name==='stdout'?[]:args);}
  function flush(){let guard=0;while(deferred.length){assert(++guard<15,'Unbounded deferred scheduling');deferred.shift()();}}
  function stdout(name='NEW'){invoke('stdout',JSON.stringify([{name,width:1920,height:1080,scale:1,refreshRate:60}]));}
  function exit(code=0,status=0){detector._running=false;counts.exits++;invoke('exited',code,status);}
  return {root,detector,counts,original,pending,invoke,flush,stdout,exit};
}
const cases=[];
function test(name,fn){try{fn();cases.push({scenario:name,passed:true});}catch(e){cases.push({scenario:name,passed:false,error:String(e)});}}
function verifyRetained(f){assert.strictEqual(f.root.monitorsList,f.original);assert.strictEqual(f.root.pendingDisplayChanges,f.pending);assert.equal(f.counts.recorded,0);}
test('hyprland-valid-commits-after-exit',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();verifyRetained(f);f.exit();assert.equal(f.root.monitorsList[0].name,'NEW');assert.equal(f.counts.recorded,1);assert.equal(f.root.monitorDetectionStatus,'updated');});
test('nonzero-exit-rejects-staged',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();f.exit(9,0);verifyRetained(f);assert.equal(f.root.monitorDetectionStatus,'failed');});
test('crash-exit-rejects-staged',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();f.exit(15,1);verifyRetained(f);});
test('empty-output-retains',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('stdout','');f.exit();verifyRetained(f);});
test('invalid-json-retains',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('stdout','{broken');f.exit();verifyRetained(f);});
test('empty-array-retains',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('stdout','[]');f.exit();verifyRetained(f);});
test('queued-request-rejects-staged',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();f.invoke('reload');f.exit();verifyRetained(f);f.flush();assert.equal(f.counts.starts,2);});
test('queued-before-stdout-rejected',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('reload');f.stdout();assert.equal(f.root.monitorDetectionCandidate,null);f.exit();verifyRetained(f);});
test('timeout-late-output-rejected',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.stdout();f.exit(15,1);verifyRetained(f);f.flush();assert.equal(f.counts.starts,2);});
test('second-timeout-blocks',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.exit(15,1);f.flush();f.invoke('timeout');f.exit(15,1);f.flush();verifyRetained(f);assert.equal(f.root.monitorDetectionBlocked,true);assert.equal(f.counts.starts,2);});
test('unconfirmed-cancel-blocks',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.invoke('grace');f.invoke('reload');f.flush();verifyRetained(f);assert.equal(f.root.monitorDetectionBlocked,true);assert.equal(f.counts.starts,1);});
test('retry-success-commits-once',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.exit(15,1);f.flush();f.stdout();verifyRetained(f);f.exit();assert.equal(f.root.monitorsList[0].name,'NEW');assert.equal(f.counts.recorded,1);assert.equal(f.root.monitorDetectionTimeouts,0);});
const success=cases.length===12&&cases.every(c=>c.passed);
console.log(JSON.stringify({version:'0.5.72',mode:'offline-production-qml-handlers-simulated-json',selftest:success?'passed':'failed',totalScenarios:cases.length,results:cases,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Actual QML handlers executed in Node VM, not real Quickshell signal delivery','No compositor queries or physical monitor configuration']},null,2));
if(!success)process.exitCode=2;
