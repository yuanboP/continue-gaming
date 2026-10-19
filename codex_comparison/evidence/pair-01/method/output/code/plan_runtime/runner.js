#!/usr/bin/env node
/** Generic time-queue host. It sends keys; it does not solve the visual task. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {performance} from 'node:perf_hooks';
import {TimedKeyExecutor, UPSTREAM_SHA256} from './executor.js';

function options(argv) {
  const command = argv[0] && !argv[0].startsWith('--') ? argv.shift() : 'serve';
  const values = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith('--') || argv[i + 1] === undefined) throw new Error('Options require --name value');
    values[argv[i].slice(2)] = argv[i + 1];
  }
  return {command, values};
}

async function requestJson(url, {method = 'GET', body, timeout = 5000} = {}) {
  const response = await fetch(url, {
    method, signal: AbortSignal.timeout(timeout),
    headers: body === undefined ? {} : {'content-type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  const text = await response.text();
  let value;
  try { value = JSON.parse(text); }
  catch { throw new Error(`HTTP ${response.status}: response was not JSON`); }
  return {ok: response.ok, status: response.status, body: value};
}

function worldUrl(base, endpoint) {
  return new URL(endpoint.replace(/^\//, ''), base.replace(/\/?$/, '/')).href;
}

async function serve(values) {
  const api = values.url || process.env.OPS_URL || process.env.WORLD_URL;
  if (!api) throw new Error('Provide --url WORLD_URL or OPS_URL');
  const port = Number(values.port || 8766);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid local queue port');
  const logPath = path.resolve(values.log || 'plan-events.jsonl');
  fs.mkdirSync(path.dirname(logPath), {recursive: true});
  const logFile = fs.openSync(logPath, 'ax', 0o600);
  let logClosed = false;
  let logSequence = 0;
  function log(event, detail = {}) {
    if (logClosed) return; // A public clock poll may finish during shutdown.
    fs.writeSync(logFile, JSON.stringify({seq: ++logSequence, event,
      host_monotonic_ms: performance.now(), ...detail}) + '\n');
  }
  const initial = await requestJson(worldUrl(api, '/observe'));
  if (!initial.ok) throw new Error(`Cannot read public observation: HTTP ${initial.status}`);
  const observation = initial.body;
  if (observation.phase !== 'running') {
    fs.closeSync(logFile);
    logClosed = true;
    throw new Error('Queue requires phase=running. Begin the selected session before starting this runtime.');
  }
  if (typeof observation.clock_ms !== 'number' || !Number.isFinite(observation.clock_ms) ||
      typeof observation.session_id !== 'string' || !observation.session_id) {
    throw new Error('Public observation requires finite clock_ms and session_id');
  }
  const sessionId = observation.session_id;
  const clockRate = observation.clock_rate === undefined ? 1 : observation.clock_rate;
  if (typeof clockRate !== 'number' || !Number.isFinite(clockRate) || clockRate <= 0) {
    throw new Error('Public clock_rate must be finite and positive');
  }
  let clockOffset = observation.clock_ms - performance.now() * clockRate;
  const clock = () => Math.max(0, performance.now() * clockRate + clockOffset);
  const executor = new TimedKeyExecutor({clock});
  let shuttingDown = false;
  let lastObservation = observation;
  let synchronizationPending = false;
  const inFlight = new Set();
  const serverReceipts = [];
  const endpoint = worldUrl(api, '/act');
  log('runtime_start', {session_id: sessionId, public_observation: observation,
    upstream_sha256: UPSTREAM_SHA256, control_interval_ms: 2,
    clock_source: 'public clock_ms plus local monotonic elapsed time times public clock_rate',
    clock_rate: clockRate,
    no_action_retiming: true});

  async function forward(receipt) {
    const action = {key: receipt.key, id: receipt.id,
      expires_ms: receipt.expires_ms, session_id: sessionId};
    const sentMs = clock();
    log('atomic_request', {action, model_at_ms: receipt.at_ms,
      upstream_executed_ms: receipt.actual_ms, sent_ms: sentMs});
    try {
      const response = await requestJson(endpoint, {method: 'POST', body: action, timeout: 3000});
      const row = {id: receipt.id, key: receipt.key, sent_ms: sentMs,
        received_ms: clock(), ...response};
      serverReceipts.push(row);
      log('atomic_response', row);
    } catch (error) {
      // An uncertain HTTP outcome is never silently treated as execution.
      // Retrying at the model's discretion uses the public API's same ID.
      const row = {id: receipt.id, key: receipt.key, sent_ms: sentMs,
        received_ms: clock(), outcome: 'unknown', error: String(error.message)};
      serverReceipts.push(row);
      log('atomic_transport_error', row);
    }
  }

  function collect(batch, source) {
    if (batch.snapshot.rawacks.length) log('upstream_receipts', {
      source, receipts: batch.snapshot.rawacks, clock_ms: batch.snapshot.clock_ms});
    for (const receipt of batch.executed) {
      const pending = forward(receipt).finally(() => inFlight.delete(pending));
      inFlight.add(pending);
    }
    return batch.result;
  }

  async function syncClock() {
    if (synchronizationPending || shuttingDown) return;
    synchronizationPending = true;
    try {
      const sent = performance.now();
      const response = await requestJson(worldUrl(api, '/observe'), {timeout: 2000});
      const received = performance.now();
      if (!response.ok) { log('clock_sync_error', {http_status: response.status}); return; }
      const next = response.body;
      if (next.session_id !== sessionId || (next.clock_rate ?? 1) !== clockRate) {
        log('session_changed', {original_session_id: sessionId, observed_session_id: next.session_id});
        await stop('session_changed');
        return;
      }
      if (typeof next.clock_ms !== 'number' || !Number.isFinite(next.clock_ms)) {
        log('clock_sync_error', {error: 'Nonfinite public clock'}); return;
      }
      // This synchronizes clocks, not model-authored tap times. The public
      // server clock is conservatively anchored to receipt of its response.
      const previousOffset = clockOffset;
      clockOffset = next.clock_ms - received * clockRate;
      lastObservation = next;
      log('clock_sync', {public_clock_ms: next.clock_ms,
        transport_ms: received - sent, offset_change_ms: clockOffset - previousOffset});
    } catch (error) { log('clock_sync_error', {error: String(error.message)}); }
    finally { synchronizationPending = false; }
  }

  function snapshot() {
    return {session_id: sessionId, public_phase: lastObservation.phase,
      upstream: executor.snapshot(), server_receipts: serverReceipts.slice(-100),
      atomic_requests_in_flight: inFlight.size, log_path: logPath};
  }

  const server = http.createServer(async (request, response) => {
    response.setHeader('content-type', 'application/json');
    const reply = (status, value) => { response.statusCode = status; response.end(JSON.stringify(value)); };
    try {
      if (shuttingDown) return reply(503, {error: 'Queue is stopping'});
      if (request.method === 'GET' && request.url === '/snapshot') return reply(200, snapshot());
      if (request.method === 'POST' && request.url === '/stop') {
        reply(200, {stopping: true});
        await stop('client_stop');
        return;
      }
      if (request.method !== 'POST' || request.url !== '/submit') return reply(404, {error: 'Unknown queue endpoint'});
      let body = '';
      for await (const chunk of request) {
        body += chunk;
        if (Buffer.byteLength(body) > 65536) return reply(413, {error: 'Transaction exceeds 64 KiB'});
      }
      const transaction = JSON.parse(body);
      log('model_transaction', {transaction, received_ms: clock()});
      const result = collect(executor.submit(transaction), 'submit');
      reply(200, {result, atomic_requests_in_flight: inFlight.size});
    } catch (error) {
      log('transaction_error', {error: String(error.message)});
      reply(400, {error: String(error.message)});
    }
  });
  const drainTimer = setInterval(() => {
    if (shuttingDown) return;
    try { collect(executor.drain(), 'timer'); }
    catch (error) { log('runtime_integrity_error', {error: String(error.message)}); void stop('integrity_error'); }
  }, 2);
  const syncTimer = setInterval(() => void syncClock(), 1000);
  async function stop(reason) {
    if (shuttingDown) return;
    shuttingDown = true;
    clearInterval(drainTimer);
    clearInterval(syncTimer);
    server.close();
    await Promise.allSettled([...inFlight]);
    log('runtime_stop', {reason, final_snapshot: snapshot()});
    logClosed = true;
    fs.closeSync(logFile);
  }
  process.once('SIGTERM', () => void stop('SIGTERM'));
  process.once('SIGINT', () => void stop('SIGINT'));
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', resolve);
  });
  process.stdout.write(JSON.stringify({ready: true, queue_url: `http://127.0.0.1:${port}`,
    session_id: sessionId, log_path: logPath}) + '\n');
}

async function main() {
  const {command, values} = options(process.argv.slice(2));
  if (command === 'serve') return await serve(values);
  const queue = values.queue || `http://127.0.0.1:${Number(values.port || 8766)}`;
  if (command === 'submit') {
    if (!values.file) throw new Error('submit requires --file transaction.json');
    const transaction = JSON.parse(fs.readFileSync(values.file, 'utf8'));
    const response = await requestJson(worldUrl(queue, '/submit'), {method: 'POST', body: transaction});
    process.stdout.write(JSON.stringify(response.body) + '\n');
    if (!response.ok) process.exitCode = 1;
  } else if (command === 'snapshot' || command === 'stop') {
    const response = await requestJson(worldUrl(queue, command === 'snapshot' ? '/snapshot' : '/stop'),
      {method: command === 'snapshot' ? 'GET' : 'POST'});
    process.stdout.write(JSON.stringify(response.body) + '\n');
    if (!response.ok) process.exitCode = 1;
  } else throw new Error('Commands: serve, submit --file PATH, snapshot, stop');
}

main().catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
