#!/usr/bin/env node
'use strict';
// v0.5.68: extracted navigation scheduling and timer callback, simulated only.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.resolve(__dirname, '../guide/display/DisplayMainState.qml'), 'utf8');
function body(marker, from=0) {
  const at = source.indexOf(marker, from);
  if (at < 0) throw Error('Missing marker: ' + marker);
  const open = source.indexOf('{', at + marker.length);
  if (open < 0) throw Error('Missing body');
  let depth=1, quote='', line=false, block=false;
  for(let i=open+1;i<source.length;i++) {
    const c=source[i], n=source[i+1];
    if(line) {if(c==='\n') line=false; continue;}
    if(block) {if(c==='*'&&n==='/'){block=false;i++;}continue;}
    if(quote) {if(c===String.fromCharCode(92)){i++;continue;}if(c===quote)quote='';continue;}
    if(c==='/'&&n==='/'){line=true;i++;continue;}
    if(c==='/'&&n==='*'){block=true;i++;continue;}
    if(c==='"'||c==="'"||c==='`'){quote=c;continue;}
    if(c==='{')depth++;
    if(c==='}'&&--depth===0)return source.slice(open+1,i);
  }
  throw Error('Unbalanced body');
}
const scheduleBody=body('function scheduleNavigationMonitorReload()');
const timerAt=source.indexOf('id: reloadMonitorsDebounceTimer');
const timerBody=body('onTriggered:',timerAt);
const visibleBody=body('onVisibleChanged:',source.indexOf('Component.onCompleted:',timerAt));
const connectionsAt=source.indexOf('target: rootObj',timerAt);
const rootVisibleBody=body('function onVisibleChanged()',connectionsAt);
const currentBody=body('function onCurrentTabChanged()',connectionsAt);
const screenAt=source.indexOf('target: Quickshell',connectionsAt);
const screensBody=body('function onScreensChanged()',screenAt);
assert(screensBody.includes('displayTabRoot.reloadMonitors()'));
assert(!screensBody.includes('scheduleNavigationMonitorReload'));
assert(!rootVisibleBody.includes('displayTabRoot.reloadMonitors()'));
assert(!currentBody.includes('displayTabRoot.reloadMonitors()'));
assert(source.includes('interval: 220'));
function simulate({rootVisible=true,tabVisible=true,current=true,events=['visible'],fire=true}) {
  let restarts=0, stops=0, runs=0, refreshes=0;
  const rootObj={visible:rootVisible,currentTab:current?5:3};
  const timer={restart(){restarts++;},stop(){stops++;}};
  const displayTabRoot={visible:tabVisible,tabIndex:5,
    refreshDisplaySettings(){refreshes++;},reloadMonitors(){runs++;},
    scheduleNavigationMonitorReload(){schedule();}};
  const context=vm.createContext({rootObj,displayTabRoot,reloadMonitorsDebounceTimer:timer,visible:tabVisible});
  const compile=x=>new vm.Script('(function(){'+x+'\n})').runInContext(context,{timeout:1000});
  const schedule=compile(scheduleBody), visible=compile(visibleBody), rootVisibleHandler=compile(rootVisibleBody), tab=compile(currentBody), triggered=compile(timerBody);
  for(const event of events){if(event==='visible')visible();else if(event==='root')rootVisibleHandler();else if(event==='tab')tab();else throw Error(event);}
  if(fire && restarts>0)triggered();
  return {restarts,stops,runs,refreshes};
}
const cases=[
 ['navigation-events-coalesce',()=>{const r=simulate({events:['visible','root','tab']});assert.equal(r.restarts,3);assert.equal(r.runs,1);} ],
 ['single-visible-event',()=>{assert.equal(simulate({events:['visible']}).runs,1);} ],
 ['single-tab-event',()=>{assert.equal(simulate({events:['tab']}).runs,1);} ],
 ['root-visibility-event',()=>{assert.equal(simulate({events:['root']}).runs,1);} ],
 ['hidden-tab-no-schedule',()=>{const r=simulate({tabVisible:false,events:['root','tab']});assert.equal(r.runs,0);assert.equal(r.restarts,0);} ],
 ['hidden-root-no-schedule',()=>{assert.equal(simulate({rootVisible:false,events:['root','tab']}).runs,0);} ],
 ['different-tab-no-request',()=>{assert.equal(simulate({current:false,tabVisible:false,events:['tab']}).runs,0);} ],
 ['hide-stops-debounce',()=>{const r=simulate({tabVisible:false,events:['visible']});assert.equal(r.stops,1);assert.equal(r.runs,0);} ],
 ['hidden-before-timer-fires',()=>{let r=simulate({events:['visible'],fire:false});assert.equal(r.runs,0);assert.equal(r.restarts,1);r=simulate({rootVisible:false,events:['visible'],fire:true});assert.equal(r.runs,0);} ],
 ['screen-event-immediate',()=>{assert(screensBody.includes('displayTabRoot.reloadMonitors()'));}],
 ['serialized-detector-preserved',()=>{assert(source.includes('function startQueuedMonitorDetection()'));assert(source.includes('monitorDetectionInFlight'));assert(source.includes('monitorDetectionWatchdog'));}]
];
const results=[];
for(const [name,fn] of cases){fn();results.push({scenario:name,passed:true});}
console.log(JSON.stringify({version:'0.5.68',mode:'offline-extracted-qml-navigation',selftest:'passed',totalScenarios:results.length,results,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Simulated QML callbacks, not a Quickshell runtime signal-count proof','No compositor commands executed']},null,2));
