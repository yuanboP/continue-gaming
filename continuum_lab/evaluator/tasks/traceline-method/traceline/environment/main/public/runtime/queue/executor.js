/** A Node host for the integrity-checked timed-key kernel.
 * This module neither interprets pixels nor chooses, predicts, or retimes actions.
 */
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';

export const EXECUTOR_SHA256 = '3b0c38c81ae163fc1ff56dc26943484e8121a47f90ce4d3c7f2be221547e1b28';
const SOURCE_URL = new URL('./kernel.js', import.meta.url);
const plain = value => JSON.parse(JSON.stringify(value));

export class TimedKeyExecutor {
  constructor({clock, source = fs.readFileSync(SOURCE_URL, 'utf8')} = {}) {
    if (typeof clock !== 'function') throw new TypeError('A public monotonic clock is required');
    const actualHash = crypto.createHash('sha256').update(source).digest('hex');
    if (actualHash !== EXECUTOR_SHA256) throw new Error('Timed-key kernel integrity mismatch');
    this.clock = clock;
    this.keyEvents = [];
    this.sourceSha256 = actualHash;
    const host = this;
    const context = {
      performance: {now: () => host.clock()},
      KeyboardEvent: class {
        constructor(type, fields) { this.type = type; Object.assign(this, fields); }
      },
      window: {dispatchEvent(event) {
        if (event.type === 'keydown') host.keyEvents.push({key: event.key});
        return true;
      }},
      // The host calls the timed-key kernel drain routine on its own timer.
      // Suppressing this timer avoids an unobservable concurrent drain.
      setInterval: () => 1,
    };
    this.context = vm.createContext(context, {
      name: 'inspection-timed-key-executor',
      codeGeneration: {strings: false, wasm: false},
    });
    new vm.Script(source, {filename: 'kernel.js'}).runInContext(this.context);
    this.executor = this.context.__modelExecutor;
  }

  snapshot(afterSeq = 0) { return plain(this.executor.snapshot(afterSeq)); }

  _collect(operation) {
    if (this.keyEvents.length) throw new Error('Uncollected executor key events');
    const cursor = this.executor.snapshot().cursor;
    let result;
    try { result = operation(); }
    catch (error) { this.keyEvents.length = 0; throw error; }
    const snapshot = this.snapshot(cursor);
    const executed = snapshot.rawacks.filter(row => row.status === 'executed');
    const events = this.keyEvents.splice(0);
    if (executed.length !== events.length || executed.some((row, i) => row.key !== events[i].key)) {
      throw new Error('Executor key event/receipt correspondence failed');
    }
    // IDs are obtained from the original executor's receipts, without editing
    // its keyboard events or reproducing its transaction semantics here.
    return {result: result === undefined ? null : plain(result),
      snapshot, executed};
  }

  submit(transaction) { return this._collect(() => this.executor.submit(transaction)); }
  drain() { return this._collect(() => this.executor.drain()); }
}
