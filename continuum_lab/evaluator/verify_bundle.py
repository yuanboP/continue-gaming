from pathlib import Path
import hashlib,json,tomllib
R=Path(__file__).resolve().parent
m=json.loads((R/'MANIFEST.json').read_text())
for name,h in m['files_sha256'].items():
 p=(R/name).resolve();assert p.is_relative_to(R.resolve()) and hashlib.sha256(p.read_bytes()).hexdigest()==h,name
for t in m['tasks']:
 d=R/t['path'];r=json.loads((d/'package.json').read_text())
 actual={str(p.relative_to(d)):hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob('*') if p.is_file() and str(p.relative_to(d))!='package.json' and '__pycache__' not in p.parts}
 assert actual==r['files_sha256']
 assert tomllib.loads((d/'task.toml').read_text())['metadata']['canonical_artifact']=='/app/output/session.json'
 for p in d.rglob('integrity.json'):
  for row in json.loads(p.read_text())['runtime_files']:assert hashlib.sha256((p.parent/row['vendored']).read_bytes()).hexdigest()==row['sha256']
print(json.dumps({'passed':True,'tasks':len(m['tasks']),'files':len(m['files_sha256'])}))
