import sys,os,time,json,threading,traceback
from pathlib import Path
sys.path.insert(0,'/app')
from ops import OperationsClient
root=Path('/app/output'); live=root/'live';live.mkdir(exist_ok=True)
c=OperationsClient(); lock=threading.Lock(); boards={}; done=set(); offset=None; session=None
log=open(root/'execution.jsonl','a',buffering=1)
def record(kind,data):
 with lock: log.write(json.dumps({'wall_time':time.time(),'monotonic':time.monotonic(),'kind':kind,'data':data})+'\n')
def acquire():
 global offset,session
 while True:
  t0=time.monotonic(); o=c.observe();t1=time.monotonic()
  offset=(t0+t1)/2-o['clock_ms']/1000;session=o['session_id'];record('observation',o)
  for b in o['carriers']:
   if b['carrier_id'] not in boards:
    n=len(boards)+1;b=dict(b);b['number']=n;b['gate_ms']=b['entry_ms']+1000000/o['calibration']['lane_speed_mm_s'][b['lane']];boards[b['carrier_id']]=b
    data,h=c.request(b['inspection_url']);(live/f'{n:02}.png').write_bytes(data);(live/f'{n:02}.json').write_text(json.dumps(b));record('capture',{'board':b,'headers':h})
    print('BOARD',n,b['lane'],round(b['gate_ms']/1000,3),flush=True)
  (root/'status.json').write_text(json.dumps({'clock_ms':o['clock_ms'],'phase':o['phase'],'boards':list(boards.values()),'receipts':o['receipts']}))
  if o['phase']=='finished':
   record('export',c.export(root/'session.json'));print('EXPORTED',flush=True);return
  time.sleep(.12)
def act_loop():
 actuator=OperationsClient()
 while True:
  try:
   decisions=[json.loads(x) for x in (root/'decisions.jsonl').read_text().splitlines() if x]
   for d in decisions:
    if d['decision']!='reject' or d['number'] in done:continue
    b=next((b for b in list(boards.values()) if b['number']==d['number']),None)
    if not b or offset is None:continue
    now=(time.monotonic()-offset)*1000
    if now>=b['gate_ms']:
     done.add(d['number'])
     if now>b['gate_ms']+300:record('late_skipped',{'board':b,'now':now});continue
     receipt=actuator.act(b['lane'],f"reject-{b['number']}",session_id=session,expires_ms=b['gate_ms']+340);record('action',receipt);print('ACTION',b['number'],receipt,flush=True)
  except Exception as e:record('act_error',str(e))
  time.sleep(.005)
(root/'decisions.jsonl').touch()
record('begin',c.begin());threading.Thread(target=act_loop,daemon=True).start()
try:acquire()
except Exception:record('fatal',traceback.format_exc());raise
