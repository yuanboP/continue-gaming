import test from 'node:test';
import assert from 'node:assert/strict';
import {TimedKeyExecutor} from '../executor.js';

function fixture() {
  let now = 0;
  return {executor: new TimedKeyExecutor({clock: () => now}), at: ms => { now = ms; }};
}
const tap = (id, key = 'd', at_ms = 100, expires_ms = 120) => ({id, key, at_ms, expires_ms});
const tx = (version, taps, extra = {}) => ({version, cancel_ids: [], taps, ...extra});

test('absolute model time is forwarded unchanged and only once', () => {
  const f = fixture();
  assert.equal(f.executor.submit(tx(1, [tap('one')])).executed.length, 0);
  f.at(99.99);
  assert.equal(f.executor.drain().executed.length, 0);
  f.at(100);
  const result = f.executor.drain();
  assert.equal(result.executed.length, 1);
  assert.deepEqual({...result.executed[0]}, {
    seq: 2, status: 'executed', version: 1, id: 'one', key: 'd',
    at_ms: 100, expires_ms: 120, admitted_version: 1, actual_ms: 100,
  });
  assert.equal(f.executor.drain().executed.length, 0);
});

test('expired commands never produce physical key events', () => {
  const f = fixture();
  f.executor.submit(tx(1, [tap('late')]));
  f.at(121);
  const result = f.executor.drain();
  assert.equal(result.executed.length, 0);
  assert.equal(result.snapshot.rawacks[0].status, 'expired');
  const old = f.executor.submit(tx(2, [tap('already-late')]));
  assert.equal(old.executed.length, 0);
  assert.equal(old.snapshot.rawacks[0].status, 'expired_before_admission');
});

test('immediately due additions still preserve event to ID correspondence', () => {
  const f = fixture();
  f.at(110);
  const result = f.executor.submit(tx(1, [tap('due-now', 'j')]));
  assert.equal(result.executed.length, 1);
  assert.equal(result.executed[0].id, 'due-now');
  assert.equal(result.executed[0].key, 'j');
  assert.equal(result.executed[0].at_ms, 100);
  assert.equal(result.executed[0].actual_ms, 110);
});

test('idempotent transactions and IDs cannot execute twice', () => {
  const f = fixture();
  const transaction = tx(1, [tap('once')]);
  f.executor.submit(transaction);
  assert.equal(f.executor.submit(transaction).result.transaction_status, 'idempotent_retry');
  assert.equal(f.executor.submit(tx(1, [tap('different')])).result.transaction_status, 'rejected');
  f.at(100);
  assert.equal(f.executor.drain().executed.length, 1);
  const repeated = f.executor.submit(tx(2, [tap('once')]));
  assert.equal(repeated.executed.length, 0);
  assert.equal(repeated.snapshot.rawacks[0].status, 'idempotent_id');
});

test('stale cancellation is rejected while stale pure additions remain legal', () => {
  const f = fixture();
  f.executor.submit(tx(5, [tap('newer', 'd')]));
  assert.equal(f.executor.submit(tx(3, [], {cancel_ids: ['newer']})).result.transaction_status, 'rejected');
  assert.equal(f.executor.submit(tx(4, [tap('older-add', 'f')])).result.transaction_status, 'accepted');
  f.at(100);
  assert.deepEqual(f.executor.drain().executed.map(row => row.id), ['newer', 'older-add']);
});

test('replace_pending retains exact copies and cancels other pending commands', () => {
  const f = fixture();
  f.executor.submit(tx(1, [tap('keep', 'f'), tap('cancel', 'd')]));
  const replacement = f.executor.submit(tx(2, [tap('keep', 'f'), tap('add', 'j')], {replace_pending: true}));
  assert.ok(replacement.snapshot.rawacks.some(row => row.id === 'cancel' && row.status === 'cancelled'));
  f.at(100);
  assert.deepEqual(f.executor.drain().executed.map(row => row.id), ['add', 'keep']);
});

test('batch ID and key correspondence follows upstream time then ID ordering', () => {
  const f = fixture();
  f.executor.submit(tx(1, [tap('z', 'k', 110, 150), tap('b', 'f', 100, 150), tap('a', 'd', 100, 150)]));
  f.at(120);
  assert.deepEqual(f.executor.drain().executed.map(row => [row.id, row.key]), [['a', 'd'], ['b', 'f'], ['z', 'k']]);
});

test('invalid schemas and modified upstream source are rejected', () => {
  const f = fixture();
  assert.throws(() => f.executor.submit(tx(1, [tap('bad', 'x')])), /Invalid tap/);
  assert.throws(() => f.executor.submit(tx(1, [tap('nan', 'd', NaN)])), /Invalid tap/);
  assert.throws(() => new TimedKeyExecutor({clock: () => 0, source: 'changed'}), /integrity mismatch/);
});
