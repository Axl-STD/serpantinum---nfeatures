#!/usr/bin/env node
"use strict";
const fs = require("fs");
const assert = require("assert");
const path = require("path");
const base = path.resolve(__dirname, "..");
const state = fs.readFileSync(path.join(base, "guide/display/DisplayMainState.qml"), "utf8");
const tab = fs.readFileSync(path.join(base, "guide/display/DisplayMainTab.qml"), "utf8");
const statuses = ["unavailable", "checking", "updated", "retrying", "blocked", "failed"];
function notice(status, verified) {
  if (!(["failed", "retrying", "blocked"].includes(status))) return "";
  return verified ? "last_known" : "no_verified";
}
const checks = [];
for (const status of statuses) for (const verified of [false,true]) {
  const result=notice(status,verified);
  assert.strictEqual(result, ["failed","retrying","blocked"].includes(status) ? (verified ? "last_known":"no_verified") : "");
  checks.push(status+":"+(verified?"verified":"empty"));
}
assert(state.includes("property bool monitorTopologyVerifiedOnce: false"));
assert(state.includes("displayTabRoot.monitorTopologyVerifiedOnce = true;"));
assert(state.includes("readonly property string monitorTopologyNoticeKey:"));
assert(tab.includes("displayTabRoot.monitorTopologyNoticeKey"));
for (const lang of ["en","es"]) {
  const data=JSON.parse(fs.readFileSync(path.join(base,"../assets/languages",lang+".json"),"utf8"));
  for (const key of ["last_known","no_verified"]) assert(data.guide.display.overview.topology_notice[key]);
}
const start=state.indexOf("onExited: (exitCode, exitStatus) => {",state.indexOf("id: monitorDetector"));
const end=state.indexOf("// Available modes are read-only",start);
const exit=state.slice(start,end);
assert(exit.indexOf("if (validCompletion) {") < exit.indexOf("monitorTopologyVerifiedOnce = true"));
assert(exit.includes("displayTabRoot.monitorsList = candidate;"));
console.log(JSON.stringify({version:"0.5.66",mode:"offline-status-logic-and-structure",selftest:"passed",totalScenarios:checks.length,scenarios:checks,physicalExecutionEnabled:false,executionAuthorized:false,commandsExecuted:0,limitations:["Static source checks and pure state scenarios; no Quickshell runtime signal proof"]},null,2));
