#!/usr/bin/env python3
"""Shared native visual-call broker. No API fallback, game state or controller.

Explicit `serve` starts a local HTTP broker; importing or --help calls no model.
Both benchmark arms may use this helper. Raw native events stay under /tmp;
only inputs, final messages, lifecycle identities and numeric usage are public.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import time
import types

UPSTREAM_SHA = 'd8a5247c721b729c758c53c28d795eafd89ece2dba73687f653ff6b390636ff4'
ADAPTED_SHA = '92dc9a6eb2e77b9af2cb4ee69589ea68e62da60ef331c8afd505403c212ee7de'
MODEL = 'gpt-6-astra'
MODEL_LITERAL_REPLACEMENTS = 5
ID = re.compile(r'[A-Za-z0-9_-]{1,100}')
MAX_BODY = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def adapt_native_source(source):
    """Only replace the exact model literal in memory; preserve all checks."""
    if sha(source)!=UPSTREAM_SHA:raise ValueError('Original subscription source hash differs')
    old=b"'gpt-6.1-sol'";new=b"'gpt-6-astra'"
    count=source.count(old)
    if count!=MODEL_LITERAL_REPLACEMENTS:raise ValueError('Unexpected model literal replacement count')
    adapted=source.replace(old,new)
    if sha(adapted)!=ADAPTED_SHA:raise ValueError('Adapted native source hash differs')
    return adapted,{'kind':'exact-model-literal-replacement-v1','original_model':'gpt-6.1-sol',
        'adapted_model':MODEL,'effort':'low','replacement_count':count,
        'original_source_sha256':UPSTREAM_SHA,'adapted_source_sha256':ADAPTED_SHA,
        'original_file_modified':False}


def native_failure_kind(error):
    """Return only a fixed classification; exception bodies remain private."""
    message=str(error).lower()
    if 'quota guard' in message or 'rate limit' in message:return 'quota_or_rate_limit'
    if 'model/effort mismatch' in message or 'model rerouted' in message:return 'model_configuration_mismatch'
    if any(term in message for term in ('sign in','not authenticated','authentication','unauthorized','login required')):return 'native_authentication'
    if isinstance(error,TimeoutError) or 'timeout' in message or 'timed out' in message:return 'native_timeout'
    if any(term in message for term in ('tool use','did not complete','unexpected native','additional instruction')):return 'native_protocol'
    if isinstance(error,(ConnectionError,BrokenPipeError,EOFError,OSError)):return 'native_transport'
    return 'native_failure_unclassified'


def public_event(event):
    """Never copy private reasoning, account data, errors or raw config."""
    method=event.get('method');p=event.get('params') or {}
    identity={k:p[k] for k in ('threadId','turnId') if isinstance(p.get(k),str)}
    if method=='thread/tokenUsage/updated':
        def numeric(value):
            if type(value) in (int,float) and math.isfinite(value) and value>=0:return value
            if isinstance(value,dict):return {k:numeric(v) for k,v in value.items() if type(v) in (int,float,dict)}
            return None
        return {'method':method,'params':{**identity,'tokenUsage':numeric(p.get('tokenUsage',{}))}}
    if method=='turn/completed':
        turn=p.get('turn') or {}
        return {'method':method,'params':{**identity,'turn':{k:turn.get(k) for k in ('id','status')}}}
    if method=='item/completed' and (p.get('item') or {}).get('type')=='agentMessage':
        item=p['item']
        return {'method':method,'params':{**identity,'item':{k:item.get(k) for k in ('id','type','text')}}}
    return None


def validate_request(value):
    allowed={'request_id','phase','run_id','prompt','images_base64','output_schema'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('Unknown request fields')
    if any(not isinstance(value.get(key),str) or not ID.fullmatch(value[key]) for key in ('request_id','run_id')):
        raise ValueError('Invalid public request/run identity')
    if value.get('phase') not in ('dev','live'):raise ValueError('phase must be dev or live')
    prompt=value.get('prompt')
    if not isinstance(prompt,str) or not 1<=len(prompt)<=100000:raise ValueError('Invalid prompt length')
    encoded=value.get('images_base64')
    if not isinstance(encoded,list) or not 1<=len(encoded)<=3:raise ValueError('Expected one to three PNG images')
    images=[]
    for item in encoded:
        data=base64.b64decode(item,validate=True)
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>4*1024*1024:raise ValueError('Expected bounded PNG')
        images.append(data)
    schema=value.get('output_schema')
    if schema is not None and (not isinstance(schema,dict) or len(canonical(schema))>32768):
        raise ValueError('Invalid output schema')
    return images


class Broker:
    def __init__(self,output,upstream,workers=4,dev_limit=160,live_limit=100):
        if not 1<=workers<=4 or dev_limit<0 or live_limit<1:raise ValueError('Invalid shared limits')
        source=Path(upstream)
        adapted,self.transport_adaptation=adapt_native_source(source.read_bytes())
        module=types.ModuleType('adapted_native_subscription');module.__file__=str(source)
        exec(compile(adapted,str(source),'exec'),module.__dict__)
        self.client_class=module.SubscriptionClient
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.private=Path(tempfile.mkdtemp(prefix='native-private-'));self.private.chmod(0o700)
        self.lock=threading.Lock();self.slots=queue.Queue();self.workers=[None]*workers
        for i in range(workers):self.slots.put(i)
        self.limits={'dev':dev_limit,'live':live_limit};self.counts={'dev':0,'live':0};self.records={}
        self.journal=self.output/'calls.jsonl'
        if self.journal.exists():
            for line in self.journal.read_text().splitlines():
                row=json.loads(line)
                if row['event']=='admitted':
                    self.records[row['request_id']]=row;self.counts[row['phase']]+=1
        self.started=time.time()

    def status(self):
        with self.lock:return {'schema_version':1,'model':MODEL,'effort':'low','upstream_sha256':UPSTREAM_SHA,
            'transport_adaptation':self.transport_adaptation,
            'workers':len(self.workers),'inflight':len(self.workers)-self.slots.qsize(),
            'admitted':dict(self.counts),'limits':dict(self.limits),'direct_api_calls':0}

    def _record(self,row):
        with self.journal.open('a') as stream:
            stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())

    def act(self,payload):
        images=validate_request(payload);rid=payload['request_id'];identity=sha(canonical(payload))
        directory=self.output/'requests'/rid
        with self.lock:
            if rid in self.records:
                if self.records[rid]['request_sha256']!=identity:raise ValueError('Request identity conflict')
                result=directory/'result.json'
                if result.exists():return json.loads(result.read_text())
                raise ValueError('Request already admitted and unresolved; never retry it')
            phase=payload['phase']
            if self.counts[phase]>=self.limits[phase]:raise ValueError('Aggregate '+phase+' request limit reached')
            directory.mkdir(parents=True,exist_ok=False)
            row={'event':'admitted','request_id':rid,'run_id':payload['run_id'],'phase':phase,
                 'request_sha256':identity,'at_unix':time.time()}
            self.records[rid]=row;self.counts[phase]+=1;self._record(row)
        (directory/'prompt.txt').write_text(payload['prompt'])
        for i,data in enumerate(images):(directory/f'image-{i}.png').write_bytes(data)
        request={**row,'prompt_sha256':sha(payload['prompt'].encode()),'image_sha256':[sha(x) for x in images],
                 'output_schema':payload.get('output_schema'),'requested_model':MODEL,'requested_effort':'low',
                 'transport_adaptation':self.transport_adaptation}
        (directory/'request.json').write_text(json.dumps(request,indent=2)+'\n')
        started=time.monotonic();worker=self.slots.get();call_dir=self.private/rid
        call_dir.mkdir(mode=0o700)
        result={'request_id':rid,'phase':phase,'run_id':payload['run_id'],'status':'failed',
                'answer':None,'meta':{'token_usage':None,'usage_unknown':True,'direct_api_calls':0}}
        try:
            client=self.workers[worker]
            if client is None:
                # Never select auth_only_home: the original native login remains in place.
                client=self.client_class(output=self.private/f'worker-{worker}-{rid}',aggressive=True)
                self.workers[worker]=client
            client.schema=json.loads(json.dumps(payload.get('output_schema')))
            model_start=time.monotonic()
            with self.lock:self._record({'event':'native_call_started','request_id':rid,'at_unix':time.time(),'worker':worker})
            answer,metadata=client.act(payload['prompt'],images,call_dir)
            result.update(status='completed',answer=answer)
            result['meta'].update(token_usage=metadata.get('token_usage'),usage_unknown=metadata.get('token_usage') is None,
                model_call_seconds=time.monotonic()-model_start,actual_model=MODEL,actual_effort='low')
        except BaseException as error:
            result['error_type']=type(error).__name__
            result['failure_kind']=native_failure_kind(error)
            client=self.workers[worker]
            if client is not None:
                try:client.close()
                except Exception:pass
            self.workers[worker]=None
        finally:
            try:
                identities=set();usage=None
                source=call_dir/'events.jsonl'
                with (directory/'public-events.jsonl').open('w') as output:
                    if source.exists():
                        with source.open() as events:
                            for line in events:
                                try:event=public_event(json.loads(line))
                                except (ValueError,TypeError):continue
                                if event is None:continue
                                output.write(json.dumps(event)+'\n')
                                params=event['params']
                                if params.get('threadId'):identities.add(params['threadId'])
                                if event['method']=='thread/tokenUsage/updated':usage=params['tokenUsage']
                if usage is not None:result['meta'].update(token_usage=usage,usage_unknown=False)
                result['meta'].update(thread_ids=sorted(identities),request_seconds=time.monotonic()-started,
                                      terminal_unix=time.time(),upstream_sha256=UPSTREAM_SHA,
                                      transport_adaptation=self.transport_adaptation)
                (directory/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
                with self.lock:self._record({'event':'terminal','request_id':rid,'status':result['status'],
                                            'at_unix':time.time(),'usage_unknown':result['meta']['usage_unknown']})
            finally:
                self.slots.put(worker)
        return result


def handler(broker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value):
            data=canonical(value);self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            self.send(200,broker.status()) if self.path in ('/health','/status') else self.send(404,{'error':'not_found'})
        def do_POST(self):
            if self.path!='/act':return self.send(404,{'error':'not_found'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_BODY:raise ValueError('Invalid body size')
                result=broker.act(json.loads(self.rfile.read(length)));self.send(200,result)
            except (ValueError,TypeError,KeyError) as error:self.send(400,{'error_type':type(error).__name__,'error':str(error)[:200]})
            except Exception as error:self.send(500,{'error_type':type(error).__name__})
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--host',default='0.0.0.0');serve.add_argument('--port',type=int,default=8090)
    serve.add_argument('--output',default=os.getenv('MODEL_BROKER_OUTPUT','/app/output/model-broker'))
    serve.add_argument('--upstream',default=os.getenv('NATIVE_SUBSCRIPTION_SOURCE','/app/native_transport/subscription.py'))
    serve.add_argument('--workers',type=int,default=int(os.getenv('MODEL_BROKER_WORKERS','4')))
    serve.add_argument('--dev-limit',type=int,default=int(os.getenv('MODEL_BROKER_DEV_LIMIT','160')))
    serve.add_argument('--live-limit',type=int,default=int(os.getenv('MODEL_BROKER_LIVE_LIMIT','100')))
    args=parser.parse_args();broker=Broker(args.output,args.upstream,args.workers,args.dev_limit,args.live_limit)
    ThreadingHTTPServer((args.host,args.port),handler(broker)).serve_forever()


if __name__=='__main__':main()
