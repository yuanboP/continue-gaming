import subprocess,time,json,pathlib,sys
img=pathlib.Path(sys.argv[1]).resolve(); dest=img.with_suffix(''); prompt='Inspect the attached board comparison. Left is query, right is independent known-normal reference. Reject visible structural defects: bent/broken pins, missing/damaged components, cracks, foreign material or damaging contamination. Normal manufacturing, text, pose, reflections are allowed. Tiny isolated specks, ordinary solder/flux residue, and cosmetic surface variation alone are not grounds for rejection; contamination must be conspicuous or damaging. The reference is a DIFFERENT physical example: exact pixel differences alone are not defects. Small black printing dots, gaps in white silkscreen outlines, and solder-mask/trace-layout variation do not establish broken copper traces. Require clear physical damage. Examine all components. Use only this image; no tools. Respond only JSON: {"reject":true or false,"reason":"brief visual evidence"}.'
meta={'image':str(img),'prompt':prompt,'model':'gpt-6-astra','reasoning_effort':'medium','start':time.time(),'usage':'unavailable until completed'}
meta_path=pathlib.Path(str(dest)+'.model.json');meta_path.write_text(json.dumps(meta,indent=2))
cmd=['codex','exec','--skip-git-repo-check','--ephemeral','-m','gpt-6-astra','-c','model_reasoning_effort="medium"','--json','-i',str(img),'-o',str(dest)+'.answer.txt',prompt]
with open(str(dest)+'.stdout.jsonl','w') as out,open(str(dest)+'.stderr.txt','w') as err:
 p=subprocess.run(cmd,stdout=out,stderr=err)
meta['end']=time.time();meta['returncode']=p.returncode
for line in pathlib.Path(str(dest)+'.stdout.jsonl').read_text().splitlines():
 try:
  e=json.loads(line)
  if 'usage' in e:meta['usage']=e['usage']
 except:pass
meta_path.write_text(json.dumps(meta,indent=2))
try:
 txt=pathlib.Path(str(dest)+'.answer.txt').read_text();d=json.loads(txt[txt.index('{'):txt.rindex('}')+1]);d['decided_wall']=time.time();pathlib.Path(str(dest)+'.decision.json').write_text(json.dumps(d))
except Exception as e:print(e)
print(json.dumps(meta))
