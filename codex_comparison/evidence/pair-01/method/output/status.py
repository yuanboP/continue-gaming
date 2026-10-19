import json, pathlib, collections, sys
p=pathlib.Path(sys.argv[1])
a=[json.loads(x) for x in (p/'actor-calls.jsonl').read_text().splitlines()]
b=[json.loads(x) for x in (p/'plan-events.jsonl').read_text().splitlines()]
print('Events:',dict(collections.Counter(x['event'] for x in a)))
print('Receipts:',dict(collections.Counter(x['body'].get('result') for x in b if x['event']=='atomic_response')))
c=[x for x in a if x['event']=='call_completed']
print('Completed calls:',len(c),'usage present:',sum(x.get('token_usage') is not None for x in c),'max latency:',max([x['wall_seconds'] for x in c],default=0))
for x in a:
 if x['event'] in ('call_failed','invalid_decision','capture_failed','transaction_transport_error'): print(x)
print('Latest decisions:')
for x in [x for x in a if x['event']=='decision_ready'][-4:]:print(x['call_id'],x['decision'])
