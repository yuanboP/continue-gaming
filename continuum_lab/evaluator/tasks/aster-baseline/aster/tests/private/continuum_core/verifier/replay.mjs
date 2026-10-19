// No policy or alternative physics. Replays the recorded actions through the
// exact original Game implementation whose SHA is checked by verify.py.
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

export function equivalent(a, b, path = '$', errors = []) {
  if (typeof a === 'number' && typeof b === 'number') {
    if (!Number.isFinite(a) || !Number.isFinite(b) ||
        Math.abs(a-b) > 1e-9*Math.max(1,Math.abs(a),Math.abs(b))) errors.push(path);
  } else if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) errors.push(path);
    else a.forEach((value,index) => equivalent(value,b[index],`${path}[${index}]`,errors));
  } else if (a !== null && b !== null && typeof a === 'object' && typeof b === 'object') {
    const ka=Object.keys(a).sort(), kb=Object.keys(b).sort();
    if (ka.join('\0') !== kb.join('\0')) errors.push(path+'.keys');
    else ka.forEach(key => equivalent(a[key],b[key],`${path}.${key}`,errors));
  } else if (a !== b) errors.push(path);
  return errors;
}

export function validateAction(value) {
  return Array.isArray(value) && value.length===4 && value.every(Number.isInteger) &&
    value[0]>=0 && value[0]<=8 && value.slice(1).every(v=>v===0||v===1);
}

export function replay(Game, payload) {
  const audit=payload.audit, errors=[];
  const game=new Game(payload.seed);
  equivalent(game.snapshot(),audit.initial,'initial',errors);
  for (let i=0;i<audit.ticks.length;i++) {
    const record=audit.ticks[i];
    if (record.index!==i || record.tick_before!==i || !validateAction(record.action) || game.status!=='playing') {
      errors.push(`ticks[${i}].identity_or_action`); break;
    }
    const result=game.step(record.action);
    const after={tick:game.tick,status:game.status,lives:game.player.lives,bombs:game.player.bombs,
      hp:game.boss.hp,score:game.score,graze:game.graze,hits:game.hits};
    equivalent(after,record.after,`ticks[${i}].after`,errors);
    equivalent(result,record.result,`ticks[${i}].result`,errors);
  }
  equivalent(game.snapshot(),audit.final,'final',errors);
  return {agreement:errors.length===0,errors:errors.slice(0,100),error_count:errors.length,
    replayed_ticks:game.tick,status:game.status,lives:game.player.lives,bombs:game.player.bombs,
    boss_hp:game.boss.hp,score:game.score,graze:game.graze,hits:game.hits};
}

async function main() {
  const payload=JSON.parse(readFileSync(process.argv[2],'utf8'));
  const {Game}=await import(pathToFileURL(process.argv[3]).href);
  process.stdout.write(JSON.stringify(replay(Game,payload))+'\n');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {process.stderr.write(String(error)+'\n');process.exitCode=1;});
}
