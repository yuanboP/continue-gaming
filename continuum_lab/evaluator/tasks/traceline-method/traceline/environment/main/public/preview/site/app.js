/* Presentation only: no classification, scheduling, source lookup, or private state. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KEYS = ['d','f','j','k'];
  const SPEEDS = {d:45,f:50,j:55,k:48};
  const STATIC = {phase:'preview',mode:'dev',session_id:'static-preview',clock_ms:37000,horizon_ms:180000,clock_rate:0,calibration:{lane_keys:KEYS,lane_speed_mm_s:SPEEDS,distance_to_gate_mm:1000,grab_window_ms:350},carriers:[{carrier_id:'c803468b6b610a9f7',lane:'k',sku:'board_a',entry_ms:18155,inspection_url:'assets/inspection-preview.png'}],receipts:[]};
  let observation=STATIC, connected=false, selected='k', imageKey='', polling=false, pendingAction=false;
  const params=new URLSearchParams(location.search);
  const preview=params.get('preview')==='1' || location.protocol==='file:';
  const time = ms => (ms/1000).toFixed(3);
  const due = c => c.entry_ms+1000*observation.calibration.distance_to_gate_mm/observation.calibration.lane_speed_mm_s[c.lane];
  const active = lane => observation.carriers.filter(c=>c.lane===lane).sort((a,b)=>a.entry_ms-b.entry_ms);
  const notice = (message,kind='') => { $('notice').textContent=message; $('notice').className=`notice ${kind}`; };
  async function api(path, payload) {
    const result=await fetch('/api'+path,{cache:'no-store',...(payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})});
    if(!result.ok) throw new Error(`HTTP ${result.status}: ${(await result.text()).slice(0,220)}`);
    return result;
  }
  function render() {
    const phase=observation.phase, running=connected&&phase==='running';
    $('connection').textContent=connected?(observation.mode==='dev'?'已连接 · 练习场景':'已连接 · 正式场景'):'静态预览';
    $('connection-dot').className='dot'+(connected?' live':'');
    $('clock-label').replaceChildren(document.createTextNode(time(observation.clock_ms)+' '));
    const clockSmall=document.createElement('small');clockSmall.textContent=`/ ${time(observation.horizon_ms)} s`;$('clock-label').append(clockSmall);
    $('timeline-fill').style.width=Math.min(100,100*observation.clock_ms/observation.horizon_ms)+'%';
    $('lanes').replaceChildren();
    $('tracks').replaceChildren();
    for(const key of KEYS){
      const carriers=active(key), button=document.createElement('button');
      button.className='lane-button'+(key===selected?' selected':'');button.type='button';button.setAttribute('aria-pressed',String(key===selected));
      const letter=document.createElement('span');letter.className='lane-letter';letter.textContent=key.toUpperCase();
      const meta=document.createElement('span');meta.className='lane-meta';
      const b=document.createElement('b');b.textContent=carriers.length?'检视中':'等待入线';
      const small=document.createElement('small');small.textContent=carriers.length?`过门 ${time(due(carriers[0]))} s`:`${observation.calibration.lane_speed_mm_s[key]} mm/s`;
      meta.append(b,small);button.append(letter,meta);button.addEventListener('click',()=>{selected=key;render();});$('lanes').append(button);
      const row=document.createElement('div');row.className='track-row';
      const label=document.createElement('span');label.className='track-key';label.textContent=key.toUpperCase();
      const track=document.createElement('div');track.className='track';
      for(const c of carriers){const box=document.createElement('span');box.className='carrier'+(key===selected?' selected':'');box.style.left=Math.min(100,Math.max(0,100*(observation.clock_ms-c.entry_ms)/(due(c)-c.entry_ms)))+'%';box.title=c.carrier_id;track.append(box);}
      const speed=document.createElement('span');speed.className='track-speed';speed.textContent=`${observation.calibration.lane_speed_mm_s[key]} mm/s`;
      row.append(label,track,speed);$('tracks').append(row);
    }
    const carrier=active(selected)[0];
    $('inspection-title').textContent=`通道 ${selected.toUpperCase()} · 检视台`;
    $('sku').textContent=carrier?carrier.sku:'—';
    $('empty-view').hidden=Boolean(carrier);$('expand').disabled=!carrier;
    if(carrier){
      const key=observation.session_id+carrier.carrier_id;
      if(key!==imageKey){$('inspection-image').src=connected?'/api'+carrier.inspection_url:carrier.inspection_url;imageKey=key;}
      $('sensor-time').textContent=`入线 ${time(carrier.entry_ms)} s → 预计过门 ${time(due(carrier))} s`;
    }else{$('sensor-time').textContent='等待入线传感器';}
    $('begin').disabled=!connected||phase!=='ready';
    $('export').disabled=!connected||phase!=='finished';
    document.querySelectorAll('.act').forEach(el=>{el.disabled=!running||pendingAction;});
    $('phase-label').textContent={ready:'等待开始',running:'实时运行中',finished:'运行已结束',preview:'预览模式'}[phase]||phase;
    $('control-help').textContent=running?'D / F / J / K 即刻触发对应通道；时钟持续前进。':phase==='finished'?'导出完整记录，评分由独立验证器完成。':'按键仅在已连接的运行场景中生效。';
    $('receipts').replaceChildren();
    const names={target_grab:'缺陷拦截',wrong_grab:'正常品误拦截',empty:'空操作',collision:'执行器冲突',expired:'动作过期',wrong_session:'会话不匹配',not_running:'未运行'};
    for(const r of (observation.receipts||[]).slice(-12).reverse()){
      const tag=document.createElement('span');tag.className='receipt'+(r.result==='target_grab'?'':' bad');tag.textContent=`${time(r.clock_ms)} s · ${r.key.toUpperCase()} · ${names[r.result]||r.result}`;$('receipts').append(tag);
    }
    if(!$('receipts').children.length){const empty=document.createElement('span');empty.className='muted';empty.textContent='尚无动作';$('receipts').append(empty);}
  }
  async function poll(){
    if(polling||preview)return;
    polling=true;
    try{
      observation=await(await api('/observe')).json();connected=true;
      if(!active(selected).length&&observation.carriers.length)selected=observation.carriers[0].lane;
      notice('已连接公开场景接口。界面只显示已经入线的载具与公开传感信息；图像、动作和导出记录均来自原场景服务。','connected');render();
    }catch(error){connected=false;notice('场景未连接。当前仅保留预览或最后一次观测；无法执行动作。'+error.message,'error');render();}
    finally{polling=false;}
  }
  async function act(key){
    if(!connected||observation.phase!=='running'||pendingAction)return;
    pendingAction=true;render();
    const button=document.querySelector(`.act[data-key="${key}"]`);button.classList.add('pressed');
    try{await api('/act',{key,id:'ui-'+crypto.randomUUID(),session_id:observation.session_id});await poll();}
    catch(error){notice('动作未确认：'+error.message,'error');}
    finally{pendingAction=false;button.classList.remove('pressed');render();}
  }
  $('begin').addEventListener('click',async()=>{try{await api('/begin',{});await poll();}catch(error){notice(error.message,'error');}});
  document.querySelectorAll('.act').forEach(button=>button.addEventListener('click',()=>act(button.dataset.key)));
  window.addEventListener('keydown',event=>{if(event.repeat||event.ctrlKey||event.metaKey||event.altKey||['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||$('image-dialog').open)return;const key=event.key.toLowerCase();if(KEYS.includes(key)){event.preventDefault();act(key);}});
  $('export').addEventListener('click',async()=>{try{const blob=await(await api('/export')).blob();const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='session.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message,'error');}});
  $('expand').addEventListener('click',()=>{$('expanded-image').src=$('inspection-image').src;$('image-dialog').showModal();});
  $('close-dialog').addEventListener('click',()=>$('image-dialog').close());
  render();if(!preview){poll();setInterval(poll,400);}
})();
