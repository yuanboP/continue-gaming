import sys,json,time
with open('/app/output/decisions.jsonl','a') as f:
 f.write(json.dumps({'number':int(sys.argv[1]),'decision':sys.argv[2],'reason':' '.join(sys.argv[3:]),'wall_time':time.time()})+'\n')
