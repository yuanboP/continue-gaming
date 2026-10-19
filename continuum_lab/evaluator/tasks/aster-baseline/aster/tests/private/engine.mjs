export const W = 480, H = 640, FPS = 60;
export const CHART = [
  {start:0,end:20,title:'月之珠 · 静夜',pattern:'ring',every:110,count:16,speed:1.35,color:'#b7a4ff',description:'每 1.83 秒展开 16 发旋转环弹；观察空隙，轻移穿过。'},
  {start:20,end:40,title:'竹取 · 月下五愿',pattern:'fan',every:90,count:7,speed:1.8,color:'#ff9ab4',description:'每 1.5 秒向自机发射七向扇弹；等瞄准后横向移动。'},
  {start:40,end:60,title:'蓬莱 · 不尽之月',pattern:'spiral',every:45,count:8,speed:1.65,color:'#8ee8cc',description:'每 0.75 秒发射八向旋转弹；低速寻找连续通道。'},
];
export const BOMB_COLORS = ['#ff6a9f','#b28bff','#79bfff','#8de9e3','#98ed9d','#ffe38b','#ffb378','#f59be8'];
const DIRECTIONS = [[0,0],[0,-1],[1,-1],[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1]];

export class Game {
  constructor(seed=1) { this.reset(seed); }
  reset(seed=1) {
    this.seed=seed>>>0; this.tick=0;
    this.player={x:240,y:550,lives:3,bombs:3,inv:120,dying:0};
    this.boss={x:240,y:100,hp:3200,maxHp:3200};
    this.bullets=[]; this.shots=[]; this.bombOrbs=[]; this.events=[];
    this.score=0; this.graze=0; this.status='playing';
    this.bombFlash=0; this.bombTimer=0; this.lastBomb=false; this.hits=0;
    return this;
  }
  phase() { return CHART[Math.min(2,Math.floor(this.tick/1200))]; }
  step(a=[0,0,0,0]) {
    if(this.status!=='playing') return {reward:0,terminated:this.status!=='survived',truncated:this.status==='survived'};
    this.events=[];
    let reward=.002;
    const p=this.player,b=this.boss;
    this.tick++; p.inv=Math.max(0,p.inv-1);
    this.bombFlash=Math.max(0,this.bombFlash-1);
    this.bombTimer=Math.max(0,this.bombTimer-1);

    if(p.dying>0) {
      p.dying--;
      if(p.dying===0 && p.lives>0) {
        p.x=240; p.y=550; p.inv=150;
        this.events.push({type:'respawn',x:p.x,y:p.y});
      }
    } else {
      const d=DIRECTIONS[a[0]]||DIRECTIONS[0],speed=a[2]?2:4.3,norm=d[0]&&d[1]?Math.SQRT1_2:1;
      p.x=Math.max(12,Math.min(W-12,p.x+d[0]*speed*norm));
      p.y=Math.max(18,Math.min(H-12,p.y+d[1]*speed*norm));
      if(a[3]&&!this.lastBomb&&p.bombs>0&&this.bombTimer===0) {
        p.bombs--; this.bombTimer=150; this.bombFlash=25; p.inv=180;
        this.events.push({type:'bomb',x:p.x,y:p.y,cleared:this.bullets.map(q=>({x:q.x,y:q.y}))});
        this.bullets=[];
        this.bombOrbs=BOMB_COLORS.map((color,index)=>({x:p.x,y:p.y,age:0,index,color,trail:[]}));
        reward+=.2;
      }
      if(a[1]&&this.tick%5===0) {
        for(const dx of [-8,8]) this.shots.push({x:p.x+dx,y:p.y-12});
      }
    }
    this.lastBomb=!!a[3];
    b.x=240+Math.sin(this.tick/150+this.seed*.01)*75;
    this.shots=this.shots.filter(s=>{
      s.y-=12;
      if(Math.abs(s.x-b.x)<28&&Math.abs(s.y-b.y)<32) {
        b.hp-=5; this.score+=10; reward+=.015; return false;
      }
      return s.y>0;
    });

    // Dream Seal: eight expanding orbs, then staggered homing impacts.
    this.bombOrbs=this.bombOrbs.filter(o=>{
      o.age++; o.trail.push({x:o.x,y:o.y}); if(o.trail.length>10)o.trail.shift();
      if(o.age<42+o.index*4) {
        const angle=o.index*Math.PI/4+o.age*.075,radius=15+Math.min(o.age,45)*2.6;
        o.x=p.x+Math.cos(angle)*radius; o.y=p.y+Math.sin(angle)*radius*.55-25;
      } else {
        const dx=b.x-o.x,dy=b.y-o.y,dist=Math.hypot(dx,dy),speed=8+(o.age-42)*.16;
        o.x+=dx/Math.max(1,dist)*speed; o.y+=dy/Math.max(1,dist)*speed;
        if(dist<speed+24) {
          b.hp-=30; this.score+=60; reward+=.09;
          this.events.push({type:'orb_hit',x:o.x,y:o.y,color:o.color});
          return false;
        }
      }
      return o.age<150;
    });
    const ph=this.phase();
    if(this.tick%ph.every===0) {
      const base=ph.pattern==='fan'?Math.atan2(p.y-b.y,p.x-b.x):this.tick*.008+this.seed*.1;
      for(let i=0;i<ph.count;i++) {
        const angle=ph.pattern==='fan'?base+(i-(ph.count-1)/2)*.15:base+i*2*Math.PI/ph.count;
        this.bullets.push({x:b.x,y:b.y,vx:Math.cos(angle)*ph.speed,vy:Math.sin(angle)*ph.speed,r:5,color:ph.color,grazed:false});
      }
    }
    this.bullets=this.bullets.filter(q=>{
      q.x+=q.vx; q.y+=q.vy;
      if(this.bombOrbs.some(o=>Math.hypot(q.x-o.x,q.y-o.y)<38)) {
        this.events.push({type:'cancel',x:q.x,y:q.y}); return false;
      }
      const dist=Math.hypot(q.x-p.x,q.y-p.y);
      if(dist<q.r+3&&p.inv===0&&p.dying===0&&p.lives>0) {
        p.lives--; this.hits++; p.dying=45; p.inv=45; this.shots=[];
        this.events.push({type:'crash',x:p.x,y:p.y}); reward-=1; return false;
      }
      if(dist<22&&!q.grazed&&p.dying===0) { q.grazed=true; this.graze++; this.score+=25; reward+=.03; }
      return q.x>-24&&q.x<W+24&&q.y>-24&&q.y<H+24;
    });
    // A clear zone around the crash lets the player see the explosion and re-entry.
    if(this.events.some(e=>e.type==='crash')) this.bullets=this.bullets.filter(q=>Math.hypot(q.x-p.x,q.y-p.y)>90);
    let terminated=false,truncated=false;
    if(p.lives<=0&&(p.dying===0||this.tick>=3600)) { this.status='lost'; terminated=true; reward-=3; }
    else if(b.hp<=0) { b.hp=0; this.status='won'; terminated=true; reward+=5; }
    else if(this.tick>=3600) { this.status='survived'; truncated=true; reward+=2; }
    return {reward,terminated,truncated};
  }
  snapshot() {
    return {tick:this.tick,player:{...this.player},boss:{...this.boss},bullets:this.bullets,shots:this.shots,
      bombOrbs:this.bombOrbs,bombTimer:this.bombTimer,events:this.events,
      score:this.score,graze:this.graze,status:this.status,phase:this.phase().title,hits:this.hits};
  }
}
