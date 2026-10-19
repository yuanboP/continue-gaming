import sys, os, time, json, pathlib, threading, traceback
sys.path.insert(0,'/app')
from ops import OperationsClient
mode=sys.argv[1]; root=pathlib.Path('/app/output')/mode; root.mkdir(exist_ok=True)
c=OperationsClient(os.environ['DEV_OPS_URL'] if mode=='practice' else None,timeout=3)
log=open(root/'events.jsonl','a',buffering=1)
def record(kind,**kw): log.write(json.dumps(dict(kind=kind,utc=time.time(),**kw))+'\n')
obs=c.reset(seed=11,speed=1) if mode=='practice' else c.begin()
record('start',observation=obs)
seen={}; fired=set(); last=-1e9

def capture(b,n):
 try:
  data,headers=c.request(b['inspection_url']); (root/f'{n:02}.png').write_bytes(data); record('image',index=n,headers=headers)
 except Exception as e: record('image_error',error=str(e),index=n)
while True:
 try:
  t0=time.monotonic(); o=c.observe(); t1=time.monotonic(); clock=o['clock_ms']; rate=o['clock_rate']
  record('observe',observation=o)
  for b in o['carriers']:
   cid=b['carrier_id']
   if cid not in seen:
    n=len(seen)+1; b=dict(b,index=n,gate_ms=b['entry_ms']+1000000/o['calibration']['lane_speed_mm_s'][b['lane']]); seen[cid]=b
    (root/f'{n:02}.json').write_text(json.dumps(b)); threading.Thread(target=capture,args=(b,n),daemon=True).start(); print('NEW',n,b,flush=True)
  for cid,b in seen.items():
   p=root/f"{b['index']:02}.decision.json"
   if cid in fired or not p.exists(): continue
   try: d=json.loads(p.read_text())
   except Exception: continue
   if not d['reject']: fired.add(cid); record('accept',board=b,decision=d); continue
   now=clock+(time.monotonic()-t1)*1000*rate
   if now>=b['gate_ms']-15:
    fired.add(cid)
    if now>b['gate_ms']+300: record('missed_late',board=b,decision=d); continue
    if now-last<850: record('cooldown_block',board=b); continue
    r=c.act(b['lane'],'reject-'+cid,session_id=o['session_id'],expires_ms=b['gate_ms']+340); last=r.get('clock_ms',now); record('action',board=b,decision=d,receipt=r); print('ACTION',b['index'],r,flush=True)
  (root/'status.json').write_text(json.dumps(dict(clock_ms=clock,phase=o['phase'],seen=len(seen),receipts=o['receipts'])))
  if o['phase']=='finished':
   path='/app/output/session.json' if mode=='live' else str(root/'session.json'); result=c.export(path); record('export',result=result); print('EXPORTED',result,flush=True); break
  time.sleep(.035)
 except Exception as e: record('error',error=str(e),trace=traceback.format_exc()); time.sleep(.05)
