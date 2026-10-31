#!/usr/bin/env node
'use strict';
// v0.5.70: source-bound, offline concurrency regression. No external processes.
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
function test(name,fn){fn();cases.push({scenario:name,passed:true});}
test('initial-request-one-run',()=>{const f=fixture();f.invoke('reload');f.flush();assert.equal(f.counts.starts,1);assert.equal(f.root.monitorDetectionInFlight,true);});
test('five-requests-coalesce-to-one-followup',()=>{const f=fixture();f.invoke('reload');f.flush();for(let i=0;i<5;i++)f.invoke('reload');assert.equal(f.counts.starts,1);f.stdout();f.exit();assert.strictEqual(f.root.monitorsList,f.original);f.flush();assert.equal(f.counts.starts,2);assert.equal(f.root.monitorDetectionQueued,false);});
test('queued-stdout-is-rejected',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('reload');f.stdout();assert.equal(f.root.monitorDetectionCandidate,null);f.exit();assert.strictEqual(f.root.monitorsList,f.original);});
test('staged-before-new-request-not-published',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();assert(f.root.monitorDetectionCandidate);f.invoke('reload');f.exit();assert.strictEqual(f.root.monitorsList,f.original);assert.equal(f.counts.recorded,0);});
test('latest-success-publishes-once',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('reload');f.exit();f.flush();f.stdout();f.exit();assert.equal(f.root.monitorsList[0].name,'NEW');assert.equal(f.counts.reconciled,1);assert.equal(f.counts.recorded,1);assert.equal(f.root.monitorDetectionStatus,'updated');});
test('repeated-start-guard-prevents-overlap',()=>{const f=fixture();f.invoke('reload');f.flush();f.root.monitorDetectionQueued=true;f.invoke('start');f.invoke('start');assert.equal(f.counts.starts,1);});
test('first-timeout-cancels-and-queues',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');assert.equal(f.root.monitorDetectionTimedOut,true);assert.equal(f.root.monitorDetectionQueued,true);assert.equal(f.root.monitorDetectionTimeouts,1);assert.equal(f.detector.running,false);assert.equal(f.counts.graceStart,1);});
test('timeout-late-stdout-retains',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.stdout();assert.equal(f.root.monitorDetectionCandidate,null);f.exit(15,1);assert.strictEqual(f.root.monitorsList,f.original);});
test('timeout-retry-only-after-exit',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.flush();assert.equal(f.counts.starts,1);f.exit(15,1);f.flush();assert.equal(f.counts.starts,2);});
test('second-timeout-blocks',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.exit(15,1);f.flush();f.invoke('timeout');assert.equal(f.root.monitorDetectionBlocked,true);assert.equal(f.root.monitorDetectionQueued,false);f.exit(15,1);f.flush();assert.equal(f.counts.starts,2);});
test('unconfirmed-cancellation-blocks',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.invoke('grace');assert.equal(f.root.monitorDetectionBlocked,true);f.invoke('reload');f.flush();assert.equal(f.counts.starts,1);});
test('nonzero-exit-does-not-publish',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();f.exit(7,0);assert.strictEqual(f.root.monitorsList,f.original);assert.equal(f.counts.recorded,0);});
test('successful-retry-resets-timeouts',()=>{const f=fixture();f.invoke('reload');f.flush();f.invoke('timeout');f.exit(15,1);f.flush();f.stdout();f.exit();assert.equal(f.root.monitorDetectionTimeouts,0);assert.equal(f.root.monitorDetectionStatus,'updated');});
test('pending-proposals-retained-on-superseded',()=>{const f=fixture();f.invoke('reload');f.flush();f.stdout();f.invoke('reload');f.exit();assert.strictEqual(f.root.pendingDisplayChanges,f.pending);assert.equal(f.counts.reconciled,0);});
test('blocked-reload-does-not-start',()=>{const f=fixture();f.root.monitorDetectionBlocked=true;f.invoke('reload');f.flush();assert.equal(f.counts.starts,0);});
console.log(JSON.stringify({version:'0.5.70',mode:'offline-extracted-qml-concurrency',selftest:'passed',totalScenarios:cases.length,results:cases,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Mocked QML callbacks and scheduling; not proof of real Quickshell event ordering','No compositor commands or physical configuration executed']},null,2));
