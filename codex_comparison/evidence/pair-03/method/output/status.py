import json,sys
from collections import Counter
from pathlib import Path
p=Path(sys.argv[1])
a=[json.loads(x) for x in (p/'actor-calls.jsonl').read_text().splitlines()]
b=[json.loads(x) for x in (p/'plan-events.jsonl').read_text().splitlines()]
print('Events:',dict(Counter(x['event'] for x in a)))
print('Actions:',dict(Counter(x['body'].get('result') for x in b if x['event']=='atomic_response')))
c=[x for x in a if x['event']=='call_completed']
print('Completed calls:',len(c))
if c:print('Last completion:',c[-1])
for x in a:
 if x['event'] in ['call_failed','invalid_decision','capture_failed']:print(x)
