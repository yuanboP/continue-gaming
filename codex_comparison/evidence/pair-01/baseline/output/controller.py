import requests, time, os, json, sys, pathlib
mode=sys.argv[1]; root=pathlib.Path('/app/output')/mode;root.mkdir(exist_ok=True)
url=os.environ['OPS_URL' if mode=='live' else 'DEV_OPS_URL'];s=requests.Session()
def log(kind,data):
 with open(root/'events.jsonl','a') as f:f.write(json.dumps({'time':time.time(),'kind':kind,'data':data})+'\n')
if mode=='live':log('begin',s.post(url+'/begin',json={}).json())
seen={};done=set();lastpoll=0;anchor=0;clock=0
while True:
 now=time.monotonic()
 if now-lastpoll>.12:
  t=time.monotonic();o=s.get(url+'/observe').json();anchor=(t+time.monotonic())/2;clock=o['clock_ms'];lastpoll=now
  log('observe',o)
  for c in o['carriers']:
   cid=c['carrier_id']
   if cid not in seen:
    c['gate_ms']=c['entry_ms']+1000000/o['calibration']['lane_speed_mm_s'][c['lane']]
    c['index']=len(seen)+1;seen[cid]=c
    im=s.get(url+c['inspection_url']);(root/f"{c['index']:02}.png").write_bytes(im.content)
    log('capture',{'carrier':c,'headers':dict(im.headers)})
    (root/'carriers.json').write_text(json.dumps(list(seen.values()),indent=2))
    print('NEW',c['index'],cid,c['lane'],c['gate_ms'],flush=True)
  if o['phase']=='finished':
   r=s.get(url+'/export');r.raise_for_status();(pathlib.Path('/app/output/session.json') if mode=='live' else root/'session.json').write_bytes(r.content); print('FINISHED',o['receipts'],flush=True);break
 current=clock+(time.monotonic()-anchor)*1000
 for cid,c in seen.items():
  decision=root/f"{c['index']:02}.decision"
  if cid not in done and decision.exists():
   d=decision.read_text().strip()
   if d.startswith('pass'):done.add(cid);log('decision',{'carrier':c,'decision':d})
   elif d.startswith('reject') and current>=c['gate_ms']:
    r=s.post(url+'/act',json={'key':c['lane'],'id':mode+'-'+cid,'session_id':o['session_id'],'expires_ms':c['gate_ms']+340}).json();done.add(cid);log('action',{'decision':d,'receipt':r});print('ACTION',c['index'],r,flush=True)
 time.sleep(.008)
