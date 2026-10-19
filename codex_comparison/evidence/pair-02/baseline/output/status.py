import json,pathlib,sys,collections,time
p=pathlib.Path('output')/sys.argv[1]
cs=json.loads((p/'carriers.json').read_text())
for c in cs:
 n=c['n'];m=p/f'{n:02}.model.json';d=p/f'{n:02}.decision.json'
 mm=json.loads(m.read_text()) if m.exists() else {};dd=json.loads(d.read_text()) if d.exists() else {}
 print(n,round(mm.get('end',time.time())-mm.get('start',time.time()),1),dd)
es=[json.loads(l) for l in (p/'events.jsonl').read_text().splitlines()]
print('clock',es[-1]['data'].get('clock_ms'));print('actions',collections.Counter(e['data']['response']['result'] for e in es if e['kind']=='action'));print('late',[e for e in es if e['kind']=='late'])
