/** Pure protocol fixture: a fake key sink, no task engine or episode. */
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {performance} from 'node:perf_hooks';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = http.createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

test('HTTP shim forwards original model ID, key and expiry without a future-time API', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sooool-protocol-fixture-'));
  const start = performance.now();
  const clock = () => 1000 + performance.now() - start;
  const received = [];
  const world = http.createServer(async (request, response) => {
    response.setHeader('content-type', 'application/json');
    if (request.url === '/observe') {
      response.end(JSON.stringify({clock_ms: clock(), clock_rate: 1,
        session_id: 'fixture-session', phase: 'running', horizon_ms: 10000}));
      return;
    }
    if (request.url === '/act' && request.method === 'POST') {
      let body = '';
      for await (const chunk of request) body += chunk;
      const action = JSON.parse(body);
      received.push({action, at: clock()});
      response.end(JSON.stringify({id: action.id, key: action.key,
        result: 'fixture_received', clock_ms: clock(), session_id: 'fixture-session'}));
      return;
    }
    response.statusCode = 404;
    response.end('{}');
  });
  world.listen(0, '127.0.0.1');
  await once(world, 'listening');
  const queuePort = await freePort();
  const queueUrl = `http://127.0.0.1:${queuePort}`;
  const child = spawn(process.execPath, [new URL('../runner.js', import.meta.url).pathname,
    'serve', '--url', `http://127.0.0.1:${world.address().port}`,
    '--port', String(queuePort), '--log', path.join(directory, 'trace.jsonl')],
    {stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  try {
    await Promise.race([
      once(child.stdout, 'data'),
      pause(4000).then(() => { throw new Error('Queue startup timeout: ' + stderr); }),
    ]);
    const due = clock() + 180;
    const expiry = due + 1500;
    const transaction = {version: 1, cancel_ids: [],
      taps: [{id: 'model-owned-id', key: 'f', at_ms: due, expires_ms: expiry}]};
    const submit = () => fetch(queueUrl + '/submit', {method: 'POST',
      headers: {'content-type': 'application/json'}, body: JSON.stringify(transaction)});
    assert.equal((await submit()).status, 200);
    assert.equal((await submit()).status, 200);
    assert.equal(received.length, 0);
    for (let i = 0; i < 100 && received.length === 0; i++) await pause(20);
    assert.equal(received.length, 1, stderr);
    assert.deepEqual(received[0].action, {key: 'f', id: 'model-owned-id',
      expires_ms: expiry, session_id: 'fixture-session'});
    assert.ok(received[0].at >= due - 5, 'No key may be sent before the model time');
    assert.ok(received[0].at <= expiry, 'Test fixture should arrive before expiry');
    await pause(30);
    const snapshot = await (await fetch(queueUrl + '/snapshot')).json();
    assert.equal(snapshot.server_receipts[0].body.result, 'fixture_received');
    assert.equal(snapshot.server_receipts[0].id, 'model-owned-id');
    const exiting = once(child, 'exit');
    await fetch(queueUrl + '/stop', {method: 'POST'});
    await exiting;
    const events = fs.readFileSync(path.join(directory, 'trace.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(row => row.event === 'atomic_request').length, 1);
    assert.equal(events.find(row => row.event === 'atomic_request').model_at_ms, due);
    assert.ok(events.some(row => row.event === 'runtime_stop'));
  } finally {
    if (child.exitCode === null) child.kill('SIGTERM');
    await new Promise(resolve => world.close(resolve));
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

test('a stopped public clock is rejected before any actions or queue timer begin', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sooool-ready-clock-fixture-'));
  let actions = 0;
  const world = http.createServer((request, response) => {
    response.setHeader('content-type', 'application/json');
    if (request.url === '/act') actions++;
    response.end(JSON.stringify({clock_ms: 0, clock_rate: 1,
      session_id: 'not-begun-fixture', phase: 'ready', horizon_ms: 10000}));
  });
  world.listen(0, '127.0.0.1');
  await once(world, 'listening');
  const child = spawn(process.execPath, [new URL('../runner.js', import.meta.url).pathname,
    'serve', '--url', `http://127.0.0.1:${world.address().port}`,
    '--port', String(await freePort()), '--log', path.join(directory, 'trace.jsonl')],
    {stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  let stdout = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdout.on('data', chunk => { stdout += chunk; });
  try {
    const [code] = await once(child, 'exit');
    assert.equal(code, 1);
    assert.match(stderr, /phase=running.*Begin/);
    assert.equal(stdout, '');
    assert.equal(actions, 0);
    assert.equal(fs.readFileSync(path.join(directory, 'trace.jsonl'), 'utf8'), '');
  } finally {
    if (child.exitCode === null) child.kill('SIGTERM');
    await new Promise(resolve => world.close(resolve));
    fs.rmSync(directory, {recursive: true, force: true});
  }
});
