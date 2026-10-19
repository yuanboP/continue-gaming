#!/usr/bin/env python3
"""Write one fresh Harbor config; does not launch Docker, a task or a model."""
import argparse,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--task',required=True,choices=['aster-baseline','aster-method','traceline-baseline','traceline-method'])
p.add_argument('--job',required=True)
p.add_argument('--jobs-dir',type=Path,default=ROOT/'jobs')
p.add_argument('--output',type=Path)
a=p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',a.job):p.error('Use a simple fresh job name')
if (a.jobs_dir/a.job).exists():raise FileExistsError('Preserve prior attempts; select a fresh job name')
m=json.loads((ROOT/'MANIFEST.json').read_text());t=next(t for t in m['tasks'] if t['id']==a.task)
config={'job_name':a.job,'jobs_dir':str(a.jobs_dir.resolve()),'n_attempts':1,'n_concurrent_trials':1,
'retry':{'max_retries':0},'environment':{'import_path':'harbor_suite.docker_copy:CopyDocker','delete':True},
'agents':[{'name':'codex','model_name':t['model'],'kwargs':{'version':t['codex_version'],'reasoning_effort':t['reasoning_effort']},
'env':{'CODEX_AUTH_JSON_PATH':'${CODEX_AUTH_JSON_PATH}','OPENAI_BASE_URL':'','CODEX_EXTRA_CONFIG':''}}],
 'tasks':[{'path':str((ROOT/t['path']).resolve())}]}
out=a.output or ROOT/'configs'/(a.job+'.json');out.parent.mkdir(parents=True,exist_ok=True)
with out.open('x') as f:json.dump(config,f,indent=2);f.write('\n')
print(out)
