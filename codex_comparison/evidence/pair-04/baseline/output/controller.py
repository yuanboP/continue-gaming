import sys,os,time,json,threading, pathlib
sys.path.insert(0,'/app')
from ops import OperationsClient
mode=sys.argv[1]; root=pathlib.Path('/app/output')/mode;root.mkdir(exist_ok=True)
c=OperationsClient(os.environ['DEV_OPS_URL'] if mode=='practice' else os.environ['OPS_URL'])
loglock=threading.Lock(); state={}; stopped=False

def log(kind,data):
 with loglock:
  with (root/'events.jsonl').open('a') as f:f.write(json.dumps({'wall_time':time.time(),'monotonic':time.monotonic(),'kind':kind,'data':data})+'\n')

def gate(item,session,offset):
 target=item['gate_ms']/1000+offset
 decision=root/f"{item['index']:02d}.decision.json"
 while time.monotonic()<target:
  time.sleep(min(.02,max(0,target-time.monotonic())))
 if decision.exists():
  d=json.loads(decision.read_text());log('decision_at_gate',{'item':item,'decision':d})
  if d['reject']:
   receipt=c.act(item['lane'],f"reject-{item['carrier_id']}",session_id=session,expires_ms=item['gate_ms']+300);log('action',receipt); print('ACTION',json.dumps(receipt),flush=True)
 else:log('missing_decision',item)

obs=c.reset(seed=11,speed=1) if mode=='practice' else c.begin();log('begin',obs)
while True:
 t0=time.monotonic();obs=c.observe();t1=time.monotonic();log('observe',obs)
 offset=(t0+t1)/2-obs['clock_ms']/1000
 for item in obs['carriers']:
  cid=item['carrier_id']
  if cid in state:continue
  item=dict(item);item['index']=len(state)+1;item['gate_ms']=item['entry_ms']+1000000/obs['calibration']['lane_speed_mm_s'][item['lane']];state[cid]=item
  (root/'items.json').write_text(json.dumps(list(state.values()),indent=2))
  threading.Thread(target=gate,args=(item,obs['session_id'],offset),daemon=True).start()
  data,headers=c.request(item['inspection_url']);(root/f"{item['index']:02d}.png").write_bytes(data);log('image',{'item':item,'headers':headers});print('NEW',json.dumps(item),flush=True)
 (root/'status.json').write_text(json.dumps({'clock_ms':obs['clock_ms'],'phase':obs['phase'],'receipts':obs['receipts']}))
 if obs['phase']=='finished':
  print(c.export('/app/output/session.json' if mode=='live' else root/'session.json'),flush=True);break
 time.sleep(.12)
