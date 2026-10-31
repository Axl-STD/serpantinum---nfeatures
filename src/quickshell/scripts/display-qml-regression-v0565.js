#!/usr/bin/env node
'use strict';
// v0.5.65: isolated QML handler regression; no compositor or subprocess execution.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const qmlPath = path.resolve(__dirname, '../guide/display/DisplayMainState.qml');
const source = fs.readFileSync(qmlPath, 'utf8');
function bodyAfter(marker, from = 0) {
  const at = source.indexOf(marker, from);
  if (at < 0 || source.indexOf(marker, at + 1) >= 0 && marker === 'id: monitorDetector') throw Error('Missing or ambiguous QML marker: ' + marker);
  const open = source.indexOf('{', at + marker.length);
  if (open < 0) throw Error('Missing handler body');
  let depth = 1, quote = '', line = false, block = false;
  for (let i = open + 1; i < source.length; i++) {
    const c = source[i], n = source[i+1];
    if (line) { if (c === '\n') line = false; continue; }
    if (block) { if (c === '*' && n === '/') { block = false; i++; } continue; }
    if (quote) { if (c === '\\') { i++; continue; } if (c === quote) quote = ''; continue; }
    if (c === '/' && n === '/') { line = true; i++; continue; }
    if (c === '/' && n === '*') { block = true; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '{') depth++;
    if (c === '}' && --depth === 0) return source.slice(open+1, i);
  }
  throw Error('Unbalanced QML handler: ' + marker);
}
const processStart = source.indexOf('id: monitorDetector');
if (processStart < 0) throw Error('Detector missing');
const stdoutStart = source.indexOf('onStreamFinished:', processStart);
const exitStart = source.indexOf('onExited: (exitCode, exitStatus) =>', stdoutStart);
if (stdoutStart < 0 || exitStart < 0) throw Error('Detector callbacks missing');
const stdoutBody = bodyAfter('onStreamFinished:', processStart);
const exitBody = bodyAfter('onExited: (exitCode, exitStatus) =>', stdoutStart);
if (!stdoutBody.includes('monitorDetectionCandidate = mList') || stdoutBody.includes('monitorsList = mList')) throw Error('Deferred staging contract changed');
if (!exitBody.includes('monitorsList = candidate') || !exitBody.includes('exitCode === 0') || !exitBody.includes('exitStatus === 0')) throw Error('Exit validation contract changed');
const stdout = new vm.Script('(function(){' + stdoutBody + '\n})', {filename:'DisplayMainState.qml:stdout'});
const exited = new vm.Script('(function(exitCode,exitStatus){' + exitBody + '\n})', {filename:'DisplayMainState.qml:exited'});
const cases = [
 ['success-publishes', 'valid', 0, 0, false, false, false, true, 'updated'],
 ['nonzero-exit-retains', 'valid', 7, 0, false, false, false, false, 'failed'],
 ['crash-status-retains', 'valid', 15, 1, false, false, false, false, 'failed'],
 ['timeout-retains', 'valid', 15, 1, true, false, false, false, 'retrying'],
 ['queued-retains', 'valid', 0, 0, false, true, false, false, 'checking'],
 ['blocked-retains', 'valid', 0, 0, false, false, true, false, 'blocked'],
 ['empty-retains', '', 0, 0, false, false, false, false, 'failed'],
 ['invalid-json-retains', '{bad', 0, 0, false, false, false, false, 'failed'],
 ['empty-array-retains', '[]', 0, 0, false, false, false, false, 'failed'],
 ['successful-retry-resets', 'valid', 0, 0, false, false, false, true, 'updated'],
];
const results = [];
for (const [name, output, code, status, timedOut, queued, blocked, publish, expectedStatus] of cases) {
  const original = [{name:'OLD', dimensions:'1280x720', scale:1}];
  const pending = {OLD:{scale:1.25}};
  const counters = {reconcile:0, refresh:0, select:0, stop:0, later:0};
  const root = {
    compositor:'hyprland', monitorDetectionInFlight:true, monitorDetectionQueued:queued,
    monitorDetectionTimedOut:timedOut, monitorDetectionBlocked:blocked,
    monitorDetectionTimeouts: name === 'successful-retry-resets' ? 1 : 0,
    monitorDetectionReceivedValidOutput:false, monitorDetectionCandidate:null,
    monitorDetectionStatus:'checking', monitorsList:original,
    pendingDisplayChanges:pending, pendingMonScaleName:'', selectedMonitorName:'OLD',
    scaleDebounceTimer:{stop() { counters.stop++; }},
    reconcilePendingDisplayChanges(list) { counters.reconcile++; assert.equal(list[0].name,'NEW'); },
    ensureDisplaySelection() { counters.select++; },
    refreshDisplaySettings() { counters.refresh++; },
    startQueuedMonitorDetection() { counters.later++; }
  };
  const context = vm.createContext({displayTabRoot:root,
    monitorDetectionWatchdog:{stop(){}}, monitorDetectionCancelGrace:{stop(){}},
    Qt:{callLater(fn){ counters.later++; }}, Array, JSON, Math, Number, String, Object});
  const outputText = output === 'valid' ? JSON.stringify([{name:'NEW',width:1920,height:1080,scale:1,refreshRate:60}]) : output;
  stdout.runInContext(context, {timeout:1000}).call({text:outputText});
  assert.strictEqual(root.monitorsList, original, name + ': stdout published early');
  assert.strictEqual(counters.reconcile,0,name + ': stdout reconciled early');
  exited.runInContext(context, {timeout:1000})(code,status);
  assert.equal(root.monitorsList[0].name, publish ? 'NEW' : 'OLD',name);
  assert.equal(counters.reconcile,publish ? 1 : 0,name);
  assert.equal(counters.refresh,publish ? 1 : 0,name);
  assert.equal(root.monitorDetectionStatus,expectedStatus,name);
  assert.equal(root.monitorDetectionCandidate,null,name);
  assert.equal(root.monitorDetectionReceivedValidOutput,false,name);
  assert.equal(root.monitorDetectionInFlight,false,name);
  if (!publish) assert.strictEqual(root.pendingDisplayChanges,pending,name);
  if (name === 'successful-retry-resets') assert.equal(root.monitorDetectionTimeouts,0);
  results.push({scenario:name,passed:true});
}
console.log(JSON.stringify({version:'0.5.65',mode:'offline-extracted-qml-handlers',selftest:'passed',totalScenarios:results.length,results,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Mocked QML events, not a Quickshell runtime proof','No compositor commands executed']},null,2));
