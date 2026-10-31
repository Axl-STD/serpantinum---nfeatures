#!/usr/bin/env node
'use strict';
// Source-bound, offline-only diagnostic response audit. No subprocesses or compositor access.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.resolve(__dirname, '../guide/display/DisplayMainState.qml'), 'utf8');
const processAt = source.indexOf('id: displayDiagnosticProcess');
const nextProcess = source.indexOf('id: monitorDetector', processAt);
assert(processAt > 0 && nextProcess > processAt, 'diagnostic process boundaries missing');
const marker = 'onExited: (exitCode, exitStatus) =>';
const start = source.indexOf(marker, processAt);
assert(start > processAt && start < nextProcess, 'diagnostic exit handler missing');
function extract(open) {
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
  throw Error('unbalanced handler');
}
const open=source.indexOf('{',start+marker.length);
const body=extract(open);
assert(body.includes('executionAuthorized !== false') && body.includes('commandsExecuted !== 0'), 'safety validation missing');
const handler=new vm.Script('(function(exitCode,exitStatus){'+body+'\n})', {filename:'DisplayMainState.qml:diagnostic-exited'});
const base={assessment:'stable-observation',underlyingAssessment:'consistent-observable',executionAuthorized:false,finalizationAuthorized:false,reservationReusable:false,physicalExecutionEnabled:false,physicalRollbackVerified:false,atomicCrossStoreSnapshot:false,commandsExecuted:0};
const cases=[
 ['valid-consistent',base,0,0,'stable-observation','consistent'],
 ['valid-identity-conflict',{...base,underlyingAssessment:'identity-conflict'},0,0,'stable-observation','conflict'],
 ['invalid-json','{bad',0,0,'observation-failed','invalid_response'],
 ['nonzero-exit',base,3,0,'observation-failed','process_failed'],
 ['unsafe-execution-authorized',{...base,executionAuthorized:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-finalization-authorized',{...base,finalizationAuthorized:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-reservation-reusable',{...base,reservationReusable:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-physical-enabled',{...base,physicalExecutionEnabled:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-rollback-verified',{...base,physicalRollbackVerified:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-atomic-snapshot',{...base,atomicCrossStoreSnapshot:true},0,0,'observation-failed','invalid_response'],
 ['unsafe-commands-executed',{...base,commandsExecuted:1},0,0,'observation-failed','invalid_response'],
 ['missing-safety-field',Object.fromEntries(Object.entries(base).filter(([k])=>k!=='executionAuthorized')),0,0,'observation-failed','invalid_response'],
 ['unknown-assessment',{...base,assessment:'unknown'},0,0,'observation-failed','invalid_response'],
 ['unknown-underlying',{...base,underlyingAssessment:'unexpected'},0,0,'observation-failed','invalid_response'],
 ['no-stdout',base,0,0,'observation-failed','process_failed',false]
];
const results=[];
for(const [name,payload,code,status,assessment,message,valid=true] of cases){
  const root={diagnosticBusy:true,diagnosticResponseValid:valid,diagnosticResponse:typeof payload==='string'?payload:JSON.stringify(payload),diagnosticAssessment:'checking',diagnosticUnderlyingAssessment:'',diagnosticMessageKey:'reading'};
  const ctx=vm.createContext({displayTabRoot:root,JSON,Error});
  handler.runInContext(ctx,{timeout:1000})(code,status);
  assert.equal(root.diagnosticBusy,false,name+': busy');
  assert.equal(root.diagnosticAssessment,assessment,name+': assessment');
  assert.equal(root.diagnosticMessageKey,message,name+': message');
  results.push({scenario:name,passed:true});
}
// v0.5.74.1: abnormal process termination must fail closed.
const root={diagnosticBusy:true,diagnosticResponseValid:true,diagnosticResponse:JSON.stringify(base),diagnosticAssessment:'checking',diagnosticUnderlyingAssessment:'',diagnosticMessageKey:'reading'};
handler.runInContext(vm.createContext({displayTabRoot:root,JSON,Error}),{timeout:1000})(0,1);
const abnormalExitAccepted=root.diagnosticAssessment==='stable-observation';
assert.equal(abnormalExitAccepted,false,'abnormal exit must be rejected');
assert.equal(root.diagnosticAssessment,'observation-failed');
assert.equal(root.diagnosticMessageKey,'process_failed');
results.push({scenario:'abnormal-exit-rejected',passed:true});
const findings=[];
console.log(JSON.stringify({version:'0.5.74.1',mode:'source-bound-offline-diagnostic-negative-audit',selftest:'passed',totalScenarios:results.length,results,abnormalExitAccepted,securityGatePassed:!abnormalExitAccepted,findings,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0},null,2));
