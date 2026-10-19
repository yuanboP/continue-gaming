import {Game,FPS} from './engine.mjs';
import {render} from './renderer.mjs';

const canvas=document.getElementById('game'),game=new Game(1),keys=new Set();
const researchEnabled=new URLSearchParams(location.search).get('research')==='1';
if(researchEnabled)document.body.dataset.research='true';
let started=false,paused=false,last=performance.now(),acc=0,visualTick=0,intro=0,phaseIndex=0;
let mode='sim',speed=1,plan=[],remaining=0,held=[0,0,0,0],actionStart=0;
let wallStart=0,maxGap=0,blurCount=0,human=false,humanEdges=[],humanActions=[],humanLastAction=null;
const directions=[[0,0],[0,-1],[1,-1],[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1]];
const allowed=['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','KeyZ','KeyX','ShiftLeft','ShiftRight','Escape'];
const button=id=>document.getElementById(id);

function action(){
  const x=Number(keys.has('ArrowRight'))-Number(keys.has('ArrowLeft'));
  const y=Number(keys.has('ArrowDown'))-Number(keys.has('ArrowUp'));
  return [directions.findIndex(d=>d[0]===x&&d[1]===y),+keys.has('KeyZ'),+(keys.has('ShiftLeft')||keys.has('ShiftRight')),+keys.has('KeyX')];
}
function setKeys(a){held=a.slice(0,4);keys.clear();const [x,y]=directions[held[0]];if(x<0)keys.add('ArrowLeft');if(x>0)keys.add('ArrowRight');if(y<0)keys.add('ArrowUp');if(y>0)keys.add('ArrowDown');if(held[1])keys.add('KeyZ');if(held[2])keys.add('ShiftLeft');if(held[3])keys.add('KeyX');}
function nextAction(){if(remaining>0)return;const next=plan.shift();if(next){setKeys(next);remaining=next[4];actionStart=game.tick;}else setKeys([0,held[1],held[2],0]);}
function draw(){render(canvas,game,{started,paused,visualTick,intro,phaseIndex,held:researchEnabled?held:action()});}
function reset(seed=1){game.reset(seed);started=true;paused=false;keys.clear();visualTick=0;intro=165;phaseIndex=0;acc=0;last=performance.now();button('pause').disabled=false;button('pause').textContent='暂停';button('start').textContent='航行中';draw();}
function togglePause(){if(!started||game.status!=='playing'||researchEnabled)return;paused=!paused;keys.clear();acc=0;last=performance.now();button('pause').textContent=paused?'继续':'暂停';draw();}
function simulate(){
  if(!started||paused||game.status!=='playing')return;
  if(researchEnabled&&!human&&intro===0)nextAction();
  visualTick++;
  if(intro>0){intro--;return;}
  const act=action();
  if(human){held=[...act];if(!humanLastAction||act.some((v,i)=>v!==humanLastAction[i])){humanActions.push({visual_frame:visualTick-1,logical_frame:game.tick,action:[...act],wall_ms:performance.now()-wallStart});humanLastAction=[...act];}}
  game.step(act);
  if(researchEnabled&&!human&&remaining>0){remaining--;if(remaining===0)nextAction();}
  if(game.status!=='playing'){if(researchEnabled){plan=[];remaining=0;setKeys([0,0,0,0]);}button('start').textContent='再次航行';button('pause').disabled=true;return;}
  const next=Math.min(2,Math.floor(game.tick/1200));if(next!==phaseIndex){phaseIndex=next;intro=135;}
}
function frame(now){
  const elapsed=Math.max(0,(now-last)/1000);last=now;
  if(researchEnabled&&started)maxGap=Math.max(maxGap,elapsed);
  if(started&&!paused&&game.status==='playing'){
    acc+=researchEnabled?(mode==='realtime'?elapsed*speed:0):Math.min(.1,elapsed);
    while(acc>=1/FPS&&game.status==='playing'){simulate();acc-=1/FPS;}
  }else{acc=0;if(!started&&!researchEnabled)visualTick++;}
  if(!researchEnabled||mode==='realtime')draw();
  requestAnimationFrame(frame);
}
function getClock(){const wall=wallStart?performance.now()-wallStart:0;return {frame:game.tick,visual_frame:visualTick,intro,mode,speed,wall_ms:wall,max_raf_gap_ms:maxGap*1000,actual_visual_speed:wall?visualTick/(wall*.06):0,blur_events:blurCount,paused,status:game.status,held:[...held],remaining,active_plan:remaining?{action:[...held],start:actionStart,end:game.tick+remaining}:null,pending_plan:plan.map(a=>[...a])};}
function capture(asCanvas=false){draw();const image=document.createElement('canvas');image.width=960;image.height=720;image.getContext('2d').drawImage(canvas,0,0);const clock=getClock();return {...(asCanvas?{canvas:image}:{png:image.toDataURL('image/png')}),frame:clock.frame,held:[...held],clock};}
function keyEdge(code,down,reason='keyboard'){if(!human)return;held=action();humanEdges.push({code,down,reason,visual_frame:visualTick,logical_frame:game.tick,wall_ms:performance.now()-wallStart,held:[...held],paused});}
button('start').onclick=()=>{if(!researchEnabled){if(paused)togglePause();else reset(1);}};button('restart').onclick=()=>{if(!researchEnabled)reset(1);};button('pause').onclick=togglePause;
window.addEventListener('keydown',e=>{if(!allowed.includes(e.code)||['INPUT','TEXTAREA'].includes(e.target.tagName))return;e.preventDefault();if(e.code==='Escape'){if(!e.repeat)togglePause();return;}if(researchEnabled&&!human)return;if(e.code==='KeyZ'&&(!started||game.status!=='playing')){if(!researchEnabled&&!e.repeat)reset(1);return;}const changed=!keys.has(e.code);keys.add(e.code);if(changed)keyEdge(e.code,true);});
window.addEventListener('keyup',e=>{if(researchEnabled&&!human)return;keys.delete(e.code);if(allowed.includes(e.code))keyEdge(e.code,false);});
function blur(){if(researchEnabled){blurCount++;if(human){for(const code of [...keys]){keys.delete(code);keyEdge(code,false,'blur');}}return;}keys.clear();if(started&&!paused&&game.status==='playing')togglePause();}
window.addEventListener('blur',blur);document.addEventListener('visibilitychange',()=>{if(document.hidden)blur();});

if(researchEnabled){
  window.research={
    ready:()=>true,
    reset(seed=1,nextMode='sim',nextSpeed=1){if(!['sim','realtime'].includes(nextMode)||!(nextSpeed>0&&nextSpeed<=4))throw new Error('Invalid research clock');mode=nextMode;speed=nextSpeed;plan=[];remaining=0;held=[0,0,0,0];actionStart=0;reset(seed);setKeys(held);wallStart=performance.now();maxGap=0;blurCount=0;last=performance.now();acc=0;draw();return getClock();},
    apply(next){if(human)throw new Error('AI plans disabled while human controls are active');if(!Array.isArray(next)||next.length>16)throw new Error('Expected at most 16 action chunks');for(const a of next)if(!Array.isArray(a)||a.length!==5||!a.every(Number.isInteger)||a[0]<0||a[0]>8||a.slice(1,4).some(v=>v!==0&&v!==1)||a[4]<1||a[4]>600)throw new Error('Invalid action chunk');plan=next.map(a=>[...a]);remaining=0;nextAction();draw();return getClock();},
    step(frames=1){if(mode!=='sim')throw new Error('Manual stepping is disabled in realtime mode');if(!Number.isInteger(frames)||frames<0||frames>10000)throw new Error('Invalid frame count');for(let i=0;i<frames&&game.status==='playing';i++)simulate();draw();return getClock();},
    setHuman(enabled){human=!!enabled;plan=[];remaining=0;setKeys([0,0,0,0]);humanEdges=[];humanActions=[];humanLastAction=null;return getClock();},
    setHumanAction(a){if(!human||!Array.isArray(a)||a.length!==4||!a.every(Number.isInteger)||a[0]<0||a[0]>8||a.slice(1).some(v=>v!==0&&v!==1))throw new Error('Invalid human replay action');setKeys(a);return getClock();},
    drainHumanEvents(){const result={keys:humanEdges,actions:humanActions};humanEdges=[];humanActions=[];return result;},
    captureCanvas:()=>capture(true),capture,getClock,
    diagnostics:()=>({clock:getClock(),state:structuredClone(game.snapshot())})
  };
  window.research.advance=window.research.step;
}
draw();requestAnimationFrame(frame);
