#!/usr/bin/env node
/** Virtual-clock execution of the timed-key kernel to select a lead.
 * A local timing adapter, not visual training or a live quality test.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) => {
  if (index % 2 === 0) rows.push([value, all[index + 1]]);
  return rows;
}, []));
if (!args['--profile'] || !args['--output']) throw Error('Usage: node simulate_timing.mjs --profile FILE --output NEW_DIR [--executor FILE] [--margin-ms 25]');
const profileBytes = fs.readFileSync(args['--profile']);
const profile = JSON.parse(profileBytes);
const margin = Number(args['--margin-ms'] ?? 25);
const W = profile.calibration?.grab_window_ms;
if (profile.schema !== 'inspection-public-transport-profile-v1' || profile.mode !== 'dev' || profile.clock_rate !== 1 ||
    !Number.isFinite(W) || W <= 0 || !Number.isFinite(margin) || margin <= 0 || margin >= W/2 ||
    !Array.isArray(profile.samples) || profile.samples.length < 4 || profile.complete !== true ||
    profile.samples.length !== profile.requested_samples || profile.failures?.length) {
  throw Error('A complete DEV timing profile and positive feasible margin are required');
}
for (const row of profile.samples) {
  if (![row.apply_delay_ms,row.scheduler_jitter_ms,row.control_rtt_ms].every(Number.isFinite) ||
      row.control_rtt_ms <= 0 || row.scheduler_jitter_ms < 0 ||
      (row.capture_rtt_ms !== null && (!Number.isFinite(row.capture_rtt_ms) || row.capture_rtt_ms <= 0))) {
    throw Error('Invalid or incomplete timing sample');
  }
}
const executorPath = path.resolve(args['--executor'] ?? path.join(here, '../queue/executor.js'));
const {TimedKeyExecutor, EXECUTOR_SHA256} = await import(pathToFileURL(executorPath).href);
const measured = profile.samples.map(row => ({name:'sample-'+row.sample,
  network:row.apply_delay_ms, jitter:Math.max(2,row.scheduler_jitter_ms)}));
const totals = measured.map(row => row.network + row.jitter);
const low = Math.min(0,...totals)-margin, high = Math.max(0,...totals)+margin;
// Stress bounds are explicitly hypothetical envelopes around finite samples.
const scenarios = [...measured, {name:'zero-delay',network:0,jitter:0},
  {name:'lower-envelope',network:low,jitter:0}, {name:'upper-envelope',network:high,jitter:0}];
const candidates = [];
const grid = Math.max(1, Math.min(25, W/14));
const leads = [...new Set([0,...Array.from({length:Math.floor((W-margin)/grid)},(_,i)=>(i+1)*grid),W-margin])];
const trace = [];
for (const lead of leads) {
  let slack = Infinity, passes = 0;
  for (const scenario of scenarios) {
    const gate = 10000, literal = {id:'fixture',key:'d',at_ms:gate-lead,expires_ms:gate+W};
    let clock = gate-2000;
    const executor = new TimedKeyExecutor({clock:()=>clock});
    const admitted = executor.submit({version:1,cancel_ids:[],taps:[literal]});
    clock = literal.at_ms+scenario.jitter;
    const result = executor.drain();
    const executed = result.executed[0];
    const arrival = executed ? executed.actual_ms+scenario.network : null;
    const gap = arrival === null ? -Infinity : Math.min(arrival-(gate-W), (gate+W)-arrival);
    const passed = executed !== undefined && result.executed.length === 1 && gap >= margin && arrival <= literal.expires_ms;
    passes += Number(passed);
    slack = Math.min(slack,gap);
    trace.push({lead_ms:lead,scenario:scenario.name,literal,admission:admitted.result.transaction_status,
      executor_receipts:result.snapshot.rawacks,server_arrival_ms:arrival,physical_slack_ms:Number.isFinite(gap)?gap:null,passed});
  }
  candidates.push({lead_ms:lead,passes,total:scenarios.length,min_slack_ms:Number.isFinite(slack)?slack:null,
    feasible:passes===scenarios.length});
}
const ranked = candidates.filter(row=>row.feasible).sort((a,b)=>b.min_slack_ms-a.min_slack_ms || a.lead_ms-b.lead_ms);
const captures = profile.samples.map(row=>row.capture_rtt_ms).filter(Number.isFinite);
if (!captures.length) throw Error('At least one PNG/control contention sample is required');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const policy = {schema:'inspection-transport-policy-v1',profile_mode:'dev',clock_rate:1,
  profile_sha256:hash(profileBytes),simulator_sha256:hash(fs.readFileSync(fileURLToPath(import.meta.url))),
  executor_host_sha256:hash(fs.readFileSync(executorPath)),kernel_sha256:EXECUTOR_SHA256,
  window_ms:W,margin_ms:margin,feasible:ranked.length>0,recommended_lead_ms:ranked[0]?.lead_ms ?? null,
  delay_low_ms:low,delay_high_ms:high,capture_budget_ms:Math.max(...captures)+margin,
  settle_budget_ms:Math.max(...profile.samples.map(row=>row.control_rtt_ms))+margin,
  profile_samples:profile.samples.length,selection:'Maximum worst-case slack over a fixed lead grid and recorded/envelope scenarios',
  limitations:['Empirical envelopes are not guaranteed latency bounds.',
    'This simulation validates dispatch feasibility only, not image decisions or true model latency.',
    'Capture guard is sized from measured RTT; no counterfactual guard improvement is claimed.',
    'The actor model must itself output the literal at_ms and full-window expires_ms.']};
const output = path.resolve(args['--output']);
fs.mkdirSync(output,{recursive:false});
fs.writeFileSync(path.join(output,'simulation.json'),JSON.stringify({candidates,scenarios,trace},null,2)+'\n',{flag:'wx'});
fs.writeFileSync(path.join(output,'timing-policy.json'),JSON.stringify(policy,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({feasible:policy.feasible,policy:path.join(output,'timing-policy.json'),lead_ms:policy.recommended_lead_ms,
  simulations:trace.length,kernel_sha256:EXECUTOR_SHA256}));
if (!policy.feasible) process.exitCode=2;
