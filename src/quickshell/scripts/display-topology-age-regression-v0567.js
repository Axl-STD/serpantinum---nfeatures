#!/usr/bin/env node
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..');
const state=fs.readFileSync(path.join(root,'guide/display/DisplayMainState.qml'),'utf8');
const tab=fs.readFileSync(path.join(root,'guide/display/DisplayMainTab.qml'),'utf8');
function body(marker,source=state){const p=source.indexOf(marker);assert(p>=0,'missing '+marker);const a=marker.endsWith('{')?p+marker.length-1:source.indexOf('{',p+marker.length);let n=1,i=a+1;for(;i<source.length&&n;i++){if(source[i]==='{')n++;else if(source[i]==='}')n--;}assert(n===0);return source.slice(a+1,i-1);}
const ageCode=body('readonly property int monitorLastSuccessfulAgeMinutes:');
const recordCode=body('function recordSuccessfulMonitorDetection()');
function age(last,clock){return vm.runInNewContext('(function(){with(d){'+ageCode+'}})()',{d:{monitorLastSuccessfulDetectionMs:last,monitorAgeClockMs:clock}});}
function record(now){const d={monitorLastSuccessfulDetectionMs:0,monitorAgeClockMs:0};vm.runInNewContext('with(d){'+recordCode+'}',{d,Date:{now:()=>now}});return d;}
const tests=[
 ['no-success',()=>assert.strictEqual(age(0,0),-1)],
 ['just-now',()=>assert.strictEqual(age(1000,1000),0)],
 ['under-one-minute',()=>assert.strictEqual(age(1000,60999),0)],
 ['one-minute',()=>assert.strictEqual(age(1000,61000),1)],
 ['three-minutes',()=>assert.strictEqual(age(1000,181000),3)],
 ['clock-backwards',()=>assert.strictEqual(age(100000,50000),0)],
 ['record-success',()=>assert.strictEqual(record(123456).monitorLastSuccessfulDetectionMs,123456)],
 ['record-updates-clock',()=>assert.strictEqual(record(123456).monitorAgeClockMs,123456)],
 ['record-only-on-valid-exit',()=>{const e=body('onExited: (exitCode, exitStatus) => {',state.slice(state.indexOf('id: monitorDetector')));const b=body('if (validCompletion) {',e);assert(b.includes('recordSuccessfulMonitorDetection()'));assert.strictEqual((e.match(/recordSuccessfulMonitorDetection\(\)/g)||[]).length,1);}],
 ['timer-only-after-success',()=>{const t=state.slice(state.indexOf('id: monitorAgeRefreshTimer'),state.indexOf('id: monitorDetectionWatchdog'));assert(t.includes('interval: 5000')&&t.includes('repeat: true'));assert(t.includes('running: displayTabRoot.monitorLastSuccessfulDetectionMs > 0'));assert(t.includes('onTriggered: displayTabRoot.monitorAgeClockMs = Date.now()'));assert(!/Process\s*\{|hyprctl/.test(t));}],
 ['ui-uses-age',()=>{assert(tab.includes('visible: displayTabRoot.monitorLastSuccessfulAgeMinutes >= 0'));assert(tab.includes('.replace("%1", String(displayTabRoot.monitorLastSuccessfulAgeMinutes))'));}],
 ['translations',()=>{for(const l of ['en','es']){const d=JSON.parse(fs.readFileSync(path.resolve(root,'../assets/languages',l+'.json'),'utf8')).guide.display.overview;for(const k of ['last_successful_detection','age_just_now','age_minutes_ago'])assert(typeof d[k]==='string'&&d[k].length>0);assert(d.age_minutes_ago.includes('%1'));}}]
];
const results=[];try{for(const [scenario,fn] of tests){fn();results.push({scenario,passed:true});}console.log(JSON.stringify({version:'0.5.67',mode:'offline-source-bound-age-regression',selftest:'passed',totalScenarios:results.length,results,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:['Mocked QML state; not a Quickshell runtime proof','Wall-clock age is presentation-only']},null,2));}catch(e){console.error(JSON.stringify({version:'0.5.67',selftest:'failed',results,error:String(e),physicalExecutionEnabled:false,commandsExecuted:0},null,2));process.exitCode=2;}
