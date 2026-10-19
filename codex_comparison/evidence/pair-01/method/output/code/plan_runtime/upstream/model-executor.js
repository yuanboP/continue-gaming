/* Generic finite timed-key executor. Model timestamps are never corrected.
 * Clock: performance.now(). Native timer and optional drain() use the same clock.
 * No game state, rendering information, music clock or judgment is consulted.
 */
(() => {
  if (globalThis.__modelExecutor) return;
  const commands = new Map(), receipts = [], transactions = new Map();
  let version = -1, sequence = 0;
  const keys = new Set(['d','f','j','k']);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const exactKeys = (obj, expected) => obj && typeof obj === 'object' && !Array.isArray(obj) && Object.keys(obj).sort().join(',') === [...expected].sort().join(',');
  const validId = id => typeof id === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(id);
  function validate(tx) {
    if ((!exactKeys(tx,['version','cancel_ids','taps']) && !exactKeys(tx,['version','cancel_ids','taps','replace_pending'])) || (Object.hasOwn(tx,'replace_pending') && typeof tx.replace_pending!=='boolean') || !Number.isSafeInteger(tx.version) || tx.version < 0) throw Error('Invalid transaction schema/version');
    if (!Array.isArray(tx.cancel_ids) || tx.cancel_ids.length > 12 || !tx.cancel_ids.every(validId) || new Set(tx.cancel_ids).size !== tx.cancel_ids.length) throw Error('Invalid cancel_ids');
    if (!Array.isArray(tx.taps) || tx.taps.length > 12) throw Error('Expected at most 12 finite taps');
    const ids = new Set();
    for (const tap of tx.taps) {
      if (!exactKeys(tap,['id','key','at_ms','expires_ms']) || !validId(tap.id) || !keys.has(tap.key) || !finite(tap.at_ms) || tap.at_ms < 0 || !finite(tap.expires_ms) || tap.expires_ms < tap.at_ms || ids.has(tap.id)) throw Error('Invalid tap schema/id/key/timestamps');
      ids.add(tap.id);
    }
    return {version:tx.version,replace_pending:tx.replace_pending??false,cancel_ids:[...tx.cancel_ids],taps:tx.taps.map(t=>({id:t.id,key:t.key,at_ms:t.at_ms,expires_ms:t.expires_ms}))};
  }
  function receipt(status, command, extra={}) {
    const row={seq:++sequence,status,version,...(command?{id:command.id,key:command.key,at_ms:command.at_ms,expires_ms:command.expires_ms,...(command.admitted_version===undefined?{}:{admitted_version:command.admitted_version})}:{}),...extra};
    receipts.push(row);return row;
  }
  function drain() {
    const due=[...commands.values()].filter(c=>c.status==='pending' && c.at_ms<=performance.now()).sort((a,b)=>a.at_ms-b.at_ms || a.id.localeCompare(b.id));
    for (const command of due) {
      const actual=performance.now();
      if (actual > command.expires_ms) {command.status='expired';receipt('expired',command,{actual_ms:actual});continue;}
      command.status='executed';
      // One instantaneous key tap; the event timing comes only from the command.
      window.dispatchEvent(new KeyboardEvent('keydown',{key:command.key.toLowerCase(),code:'Key'+command.key.toUpperCase(),bubbles:true}));
      window.dispatchEvent(new KeyboardEvent('keyup',{key:command.key.toLowerCase(),code:'Key'+command.key.toUpperCase(),bubbles:true}));
      receipt('executed',command,{actual_ms:actual});
    }
  }
  function snapshot(after_seq=0) {
    if (!Number.isSafeInteger(after_seq) || after_seq<0) throw Error('Invalid receipt cursor');
    return {protocol:1,version,clock_ms:performance.now(),pending:[...commands.values()].filter(c=>c.status==='pending').map(c=>({id:c.id,key:c.key,at_ms:c.at_ms,expires_ms:c.expires_ms,admitted_version:c.admitted_version})),rawacks:receipts.filter(r=>r.seq>after_seq).map(r=>({...r})),cursor:sequence};
  }
  function submit(payload) {
    const tx=validate(payload), received=performance.now(), canonical=JSON.stringify(tx);
    if (transactions.has(tx.version)) {
      if (transactions.get(tx.version)===canonical) return {...snapshot(),transaction_status:'idempotent_retry'};
      receipt('transaction_rejected',null,{received_ms:received,requested_version:tx.version,reason:'version_payload_conflict'});
      return {...snapshot(),transaction_status:'rejected'};
    }
    // An older additive message cannot revoke a newer instruction. Only stale
    // destructive operations are rejected; IDs remain first-writer-wins.
    if (tx.version<version && (tx.replace_pending || tx.cancel_ids.length>0)) {
      receipt('transaction_rejected',null,{received_ms:received,requested_version:tx.version,reason:'stale_destructive_version'});
      return {...snapshot(),transaction_status:'rejected'};
    }
    version=Math.max(version,tx.version);transactions.set(tx.version,canonical);
    if(tx.replace_pending){
      // Exact pending copies retain the instruction; executed IDs never restart.
      const retained=new Set(tx.taps.filter(tap=>{
        const existing=commands.get(tap.id);
        return existing && existing.status==='pending' && ['id','key','at_ms','expires_ms'].every(field=>existing[field]===tap[field]);
      }).map(tap=>tap.id));
      for(const command of commands.values())if(command.status==='pending' && !retained.has(command.id)){command.status='cancelled';receipt('cancelled',command,{received_ms:received,reason:'replace_pending'});}
    }
    for (const id of tx.cancel_ids) {
      const existing=commands.get(id);
      if (existing && existing.status==='pending') {existing.status='cancelled';receipt('cancelled',existing,{received_ms:received});}
      else receipt('cancel_not_pending',existing,{id,received_ms:received});
    }
    for (const tap of tx.taps) {
      const existing=commands.get(tap.id);
      if (existing) {
        const identical=['id','key','at_ms','expires_ms'].every(field=>existing[field]===tap[field]);
        receipt(identical?'idempotent_id':'rejected',tap,{received_ms:received,...(identical?{}:{reason:'id_payload_conflict'})});
        continue;
      }
      const command={...tap,admitted_version:tx.version,status:received>tap.expires_ms?'expired':'pending'};
      commands.set(tap.id,command);
      receipt(command.status==='expired'?'expired_before_admission':'admitted',command,{received_ms:received});
    }
    drain();return {...snapshot(),transaction_status:'accepted'};
  }
  // Timer never reads UI state; the same executor also works on an empty page.
  setInterval(drain,2);
  globalThis.__modelExecutor={submit,snapshot,drain};
})();
