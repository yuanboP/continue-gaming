import os,time,json,threading,requests,sys,pathlib,concurrent.futures,subprocess
mode=sys.argv[1]; root=pathlib.Path('/app/output')/mode; root.mkdir(exist_ok=True)
url=os.environ['DEV_OPS_URL' if mode.startswith('dev') else 'OPS_URL']; s=requests.Session(); pool=concurrent.futures.ThreadPoolExecutor(4)
def log(kind,data):
 with open(root/'events.jsonl','a') as f:f.write(json.dumps({'wall':time.time(),'kind':kind,'data':data})+'\n')
def fetch(c):
 r=requests.get(url+c['inspection_url']); r.raise_for_status(); (root/f"{c['n']:02d}.png").write_bytes(r.content); log('image',{'n':c['n'],'headers':dict(r.headers)}); subprocess.run([sys.executable,'/app/output/classify.py',str(root/f"{c['n']:02d}.png")],stdout=open(root/f"{c['n']:02d}.runner.log",'w'),stderr=subprocess.STDOUT)
r=s.post(url+('/reset' if mode.startswith('dev') else '/begin'),json={'seed':11,'speed':1} if mode.startswith('dev') else {});log('start',r.json())
seen={};done=set(); anchor=0; base=0; lastpoll=0
while True:
 now=time.monotonic()
 if now-lastpoll>.18:
  t=time.monotonic();o=s.get(url+'/observe').json();end=time.monotonic();base=o['clock_ms'];anchor=(t+end)/2;lastpoll=end;log('observe',o)
  for c in o['carriers']:
   if c['carrier_id'] not in seen:
    c=dict(c);c['n']=len(seen)+1;c['gate_ms']=c['entry_ms']+1000000/o['calibration']['lane_speed_mm_s'][c['lane']];seen[c['carrier_id']]=c
    (root/'carriers.json').write_text(json.dumps(list(seen.values()),indent=2));pool.submit(fetch,c);print(json.dumps(c),flush=True)
  if o['phase']=='finished':
   r=s.get(url+'/export');r.raise_for_status();(pathlib.Path('/app/output/session.json') if mode=='live' else root/'session.json').write_bytes(r.content);log('export',{'status':r.status_code});break
 clock=base+(time.monotonic()-anchor)*1000
 for cid,c in list(seen.items()):
  p=root/f"{c['n']:02d}.decision.json"
  if cid in done or not p.exists():continue
  try:d=json.loads(p.read_text())
  except:continue
  if not d['reject']:done.add(cid);log('accept',{'carrier':c,'decision':d});continue
  if clock>=c['gate_ms']:
   done.add(cid)
   if clock>c['gate_ms']+300:log('late',{'carrier':c,'decision':d,'clock':clock});continue
   a={'key':c['lane'],'id':'reject-'+cid,'session_id':o['session_id'],'expires_ms':c['gate_ms']+340};r=s.post(url+'/act',json=a);log('action',{'request':a,'response':r.json(),'decision':d});print(r.text,flush=True)
 time.sleep(.01)
pool.shutdown()
