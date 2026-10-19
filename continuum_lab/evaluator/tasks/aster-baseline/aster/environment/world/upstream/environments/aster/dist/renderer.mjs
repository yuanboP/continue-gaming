export const STAGES = [
  {name:'环流', english:'RADIAL CURRENT', color:'#b7a4ff'},
  {name:'追迹', english:'TRACKING FIELD', color:'#ff9ab4'},
  {name:'旋光', english:'SPIRAL LIGHT', color:'#8ee8cc'},
];

const FONT='"Helvetica Neue", Arial, "PingFang SC", sans-serif';
const PALETTE={paper:'#ecede3',muted:'#93a5ab',line:'#324249',green:'#bce9d4',coral:'#f0aa92'};
const TAU=Math.PI*2;

function text(c,value,x,y,size=12,color=PALETTE.paper,weight=400,align='left'){
  c.font=`${weight} ${size}px ${FONT}`;c.fillStyle=color;c.textAlign=align;c.textBaseline='alphabetic';c.fillText(value,x,y);
}
function line(c,x1,y1,x2,y2,color=PALETTE.line){c.strokeStyle=color;c.lineWidth=1;c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);c.stroke();}
function circle(c,x,y,r,fill,stroke=null){c.beginPath();c.arc(x,y,r,0,TAU);if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=1;c.stroke();}}
function polygon(c,points,fill,stroke=null){c.beginPath();points.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.closePath();if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=1;c.stroke();}}
function hex(c,x,y,r,angle=0,fill=null,stroke=PALETTE.coral){polygon(c,Array.from({length:6},(_,i)=>[x+Math.cos(i*TAU/6+angle)*r,y+Math.sin(i*TAU/6+angle)*r]),fill,stroke);}

function arena(c,g,state){
  c.save();c.translate(28,48);c.beginPath();c.rect(0,0,480,640);c.clip();
  const bg=c.createLinearGradient(0,0,480,640);bg.addColorStop(0,'#1b2a32');bg.addColorStop(.6,'#14232c');bg.addColorStop(1,'#14202a');c.fillStyle=bg;c.fillRect(0,0,480,640);
  c.strokeStyle='#69828b15';c.lineWidth=1;
  for(let x=0;x<=480;x+=40)line(c,x+.5,0,x+.5,640,'#69828b15');
  const scroll=(state.visualTick*.18)%40;for(let y=scroll;y<640;y+=40)line(c,0,y,480,y,'#69828b15');
  circle(c,240,100,175,null,'#7c949b12');circle(c,240,100,110,null,'#7c949b12');
  for(let i=0;i<28;i++){const x=(i*137.3+31)%480,y=(i*79.7+state.visualTick*.12)%640;c.fillStyle=i%3?'#9bb4bc48':'#cbdedc75';c.fillRect(x,y,1,1);}
  const b=g.boss,stage=STAGES[state.phaseIndex];
  if(b.hp>0){
    circle(c,b.x,b.y,35,null,'#ecb39a30');hex(c,b.x,b.y,25,state.visualTick*.006,'#3e383b',PALETTE.coral);hex(c,b.x,b.y,13,-state.visualTick*.012,'#f0aa9222',PALETTE.coral);circle(c,b.x,b.y,4,PALETTE.coral);
    line(c,b.x-38,b.y,b.x-30,b.y,PALETTE.coral);line(c,b.x+30,b.y,b.x+38,b.y,PALETTE.coral);
  }
  for(const s of g.shots){c.fillStyle='#bce9d4cc';c.fillRect(s.x-1,s.y-8,2,12);}
  for(const q of g.bullets){circle(c,q.x,q.y,q.r+2,null,q.color+'55');circle(c,q.x,q.y,q.r,q.color);circle(c,q.x-1.2,q.y-1.4,1.3,'#ffffffa8');}
  for(const o of g.bombOrbs){for(let i=0;i<o.trail.length;i++)circle(c,o.trail[i].x,o.trail[i].y,2+i*.3,o.color+Math.round((i+1)/o.trail.length*55).toString(16).padStart(2,'0'));circle(c,o.x,o.y,10,o.color);circle(c,o.x,o.y,15,null,o.color+'80');}
  const p=g.player;
  if(p.dying===0&&p.lives>0){
    c.save();if(p.inv>0)c.globalAlpha=.55+.35*(Math.floor(state.visualTick/5)%2);
    polygon(c,[[p.x,p.y-13],[p.x+11,p.y+10],[p.x,p.y+5],[p.x-11,p.y+10]],'#c7ecdb','#eefbf4');
    polygon(c,[[p.x-3,p.y+10],[p.x,p.y+17+(state.visualTick%5)],[p.x+3,p.y+10]],'#9fcac055');
    c.restore();
    if(state.held[2]){circle(c,p.x,p.y,18,null,'#bce9d466');circle(c,p.x,p.y,3,'#152a30','#f9fff9');}
    if(p.inv>0)circle(c,p.x,p.y,24,null,'#bce9d444');
  }
  if(p.dying>0){const r=(45-p.dying)*1.2;circle(c,p.x,p.y,r,null,'#f0aa92aa');circle(c,p.x,p.y,r*.7,null,'#f0aa9244');}
  if(g.bombFlash>0){c.fillStyle=`rgba(188,233,212,${g.bombFlash/25*.20})`;c.fillRect(0,0,480,640);}
  if(state.intro>0&&state.started){
    c.fillStyle='#12232bd9';c.fillRect(0,229,480,151);line(c,38,230,442,230,stage.color+'88');
    text(c,`SECTOR 0${state.phaseIndex+1}`,240,263,11,stage.color,600,'center');text(c,stage.name,240,312,34,PALETTE.paper,400,'center');text(c,stage.english,240,344,11,PALETTE.muted,400,'center');
  }
  if(!state.started||state.paused||g.status!=='playing'){
    c.fillStyle='#0e1c24dc';c.fillRect(0,0,480,640);
    const terminal=g.status!=='playing';
    const title=!state.started?'穿过星航':state.paused?'航行暂停':g.status==='lost'?'信号中断':g.status==='won'?'目标已击破':'目标未击破';
    const subtitle=!state.started?'FIND YOUR LINE THROUGH THE LIGHT':state.paused?'TAKE A BREATH':g.status==='won'?'MISSION COMPLETE':'MISSION NOT PASSED';
    hex(c,240,206,40,state.visualTick*.003,null,PALETTE.green);hex(c,240,206,24,-state.visualTick*.004,null,PALETTE.green+'55');circle(c,240,206,5,PALETTE.green);
    text(c,title,240,290,34,PALETTE.paper,400,'center');text(c,subtitle,240,319,10,PALETTE.muted,500,'center');
    if(!state.started){text(c,'六十秒内击破目标；仅存活不算通过。',240,367,13,PALETTE.muted,400,'center');text(c,'按 Z 或点击「开始航行」',240,407,12,PALETTE.green,500,'center');}
    else if(state.paused)text(c,'按 Esc 继续',240,369,12,PALETTE.green,500,'center');
    else if(terminal){text(c,`SCORE  ${String(g.score).padStart(6,'0')}`,240,367,18,PALETTE.green,500,'center');text(c,'按 Z 或点击「重新开始」',240,407,12,PALETTE.muted,400,'center');}
  }
  c.restore();c.strokeStyle='#6b858c50';c.lineWidth=1;c.strokeRect(28.5,48.5,479,639);
}

export function render(canvas,g,state){
  const c=canvas.getContext('2d');c.clearRect(0,0,960,720);c.fillStyle='#111a20';c.fillRect(0,0,960,720);
  text(c,'ASTER',28,29,15,PALETTE.paper,600);text(c,'星 航',91,28,12,PALETTE.muted);text(c,'FLIGHT STUDY / 01',508,27,9,PALETTE.muted,400,'right');
  arena(c,g,state);
  const x=550,right=924,width=374,stage=STAGES[state.phaseIndex];
  text(c,'星航',x,80,32,PALETTE.paper,400);text(c,'A QUIET WAY THROUGH',x,105,10,PALETTE.muted,500);
  circle(c,918,69,4,g.status==='lost'?PALETTE.coral:PALETTE.green);line(c,x,129,right,129);
  text(c,'SCORE',x,158,10,PALETTE.muted,500);text(c,String(g.score).padStart(6,'0'),x,208,47,PALETTE.paper,300);
  text(c,'HULL / 机体',x,249,10,PALETTE.muted,500);text(c,'PULSE / 清屏',x+210,249,10,PALETTE.muted,500);
  for(let i=0;i<3;i++){const active=i<g.player.lives;polygon(c,[[x+i*27+6,265],[x+i*27+12,276],[x+i*27,276]],active?PALETTE.green:'#3a4b52');hex(c,x+218+i*27,272,6,0,i<g.player.bombs?PALETTE.coral:null,i<g.player.bombs?PALETTE.coral:'#3a4b52');}
  line(c,x,303,right,303);
  text(c,'TARGET',x,330,10,PALETTE.muted,500);text(c,`${Math.ceil(g.boss.hp/g.boss.maxHp*100)}%`,right,330,12,PALETTE.coral,500,'right');
  c.fillStyle='#2b3940';c.fillRect(x,344,width,4);c.fillStyle=PALETTE.coral;c.fillRect(x,344,width*Math.max(0,g.boss.hp/g.boss.maxHp),4);
  text(c,'ELAPSED',x,384,10,PALETTE.muted,500);text(c,`${(g.tick/60).toFixed(1).padStart(4,'0')} / 60.0 s`,right,384,16,PALETTE.paper,400,'right');
  c.fillStyle='#2b3940';c.fillRect(x,399,width,2);c.fillStyle=PALETTE.green;c.fillRect(x,399,width*Math.min(1,g.tick/3600),2);
  text(c,'SECTORS',x,436,10,PALETTE.muted,500);
  STAGES.forEach((s,i)=>{const sx=x+i*128;line(c,sx,453,sx+115,453,i===state.phaseIndex?s.color:'#3a4b52');text(c,`0${i+1}`,sx,477,10,i===state.phaseIndex?s.color:PALETTE.muted);text(c,s.name,sx+26,477,13,i===state.phaseIndex?PALETTE.paper:PALETTE.muted);});
  text(c,'GRAZE',x,527,10,PALETTE.muted,500);text(c,String(g.graze).padStart(3,'0'),x+74,527,14,PALETTE.paper,500);
  text(c,'HITS',x+210,527,10,PALETTE.muted,500);text(c,String(g.hits).padStart(2,'0'),right,527,14,PALETTE.paper,500,'right');line(c,x,552,right,552);
  text(c,'↑ ↓ ← →',x,582,14,PALETTE.paper,500);text(c,'移动',x+101,582,11,PALETTE.muted);text(c,'Z',x+210,582,14,PALETTE.paper,500);text(c,'射击',x+245,582,11,PALETTE.muted);
  text(c,'SHIFT',x,611,12,PALETTE.paper,500);text(c,'精细移动',x+101,611,11,PALETTE.muted);text(c,'X',x+210,611,14,PALETTE.paper,500);text(c,'清屏',x+245,611,11,PALETTE.muted);
  text(c,'60 秒内击破目标。',x,656,12,PALETTE.green);text(c,'仅存活不算通过；目标生命值必须归零。',x,677,11,PALETTE.muted);
  text(c,`0${state.phaseIndex+1} / ${stage.english}`,28,707,9,PALETTE.muted,500);text(c,state.paused?'PAUSED':!state.started?'READY':g.status==='playing'?'IN FLIGHT':g.status==='won'?'COMPLETE':'NOT PASSED',508,707,9,PALETTE.muted,500,'right');
}
