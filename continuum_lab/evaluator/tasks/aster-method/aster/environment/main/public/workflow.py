#!/usr/bin/env python3
"""Explicit native provided runtime workflow client. Run only inside the Harbor task.

The gateway owns the original runner and the game; this client never reads
private engine state. No operation or model call occurs on import or --help.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid


def request(url,path,payload=None,timeout=30):
    body=None if payload is None else json.dumps(payload,allow_nan=False).encode()
    req=urllib.request.Request(url.rstrip('/')+path,data=body,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)


def save_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def export_trace(url,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(url.rstrip('/')+'/trace',timeout=120) as response, path.open('xb') as target:
        while chunk:=response.read(1024*1024):target.write(chunk)


def execute(args):
    directory=Path(args.output);log=directory/'workflow';log.mkdir(parents=True,exist_ok=True)
    url=args.live_url if args.operation=='live' else args.dev_url
    if args.operation=='status':
        if not args.run_id:raise ValueError('--run-id required for status')
        url=args.live_url if args.live else args.dev_url
        return request(url,'/workflow/status?id='+urllib.parse.quote(args.run_id))
    run_id=args.run_id or args.operation+'-'+uuid.uuid4().hex
    payload={'operation':args.operation,'run_id':run_id}
    if args.operation in ('simulate','freeze'):payload['config_id']=args.config
    if args.operation=='live' and args.freeze_run_id:payload['freeze_run_id']=args.freeze_run_id
    # Persist the intended operation before dispatch; never silently retry POST.
    save_new(log/(run_id+'-request.json'),{'url':url,'request':payload,'dispatch_unix':time.time()})
    response=request(url,'/workflow/run',payload)
    if response.get('run_id')!=run_id:raise ValueError('Gateway run identity mismatch')
    deadline=time.monotonic()+args.timeout
    while response.get('status')=='running':
        if time.monotonic()>=deadline:raise TimeoutError('Coordinator continues on server; inspect status; do not restart')
        time.sleep(1)
        response=request(url,'/workflow/status?id='+urllib.parse.quote(run_id))
    save_new(log/(run_id+'-result.json'),response)
    if args.operation in ('profile','simulate'):
        # Even a failed operation retains its actual terminal world evidence.
        try:export_trace(url,log/(run_id+'-trace.zip'))
        except Exception as error:save_new(log/(run_id+'-trace-error.json'),{'error_type':type(error).__name__})
    if args.operation=='live':
        # A failed official attempt still submits its signed artifact when one
        # exists; failure is not turned into a replacement episode.
        try:
            from game import Game
            artifacts=Game(url).export(directory)
            save_new(log/(run_id+'-artifacts.json'),artifacts)
        except Exception as error:save_new(log/(run_id+'-export-error.json'),{'error_type':type(error).__name__})
    if response.get('status')!='completed':raise RuntimeError('Workflow failed; evidence retained at '+str(log/(run_id+'-result.json')))
    if args.operation=='freeze':
        save_new(log/'frozen-workflow.json',response['result']['freeze_bundle'])
        save_new(log/'freeze-reference.json',{'freeze_run_id':run_id})
    return response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('profile','simulate','freeze','live','status'))
    parser.add_argument('--dev-url',default=os.getenv('DEV_GAME_URL','http://dev:8080'))
    parser.add_argument('--live-url',default=os.getenv('GAME_URL','http://world:8080'))
    parser.add_argument('--output',default='/app/output')
    parser.add_argument('--config',choices=('initial','single_frame'),default='initial')
    parser.add_argument('--run-id');parser.add_argument('--freeze-run-id')
    parser.add_argument('--live',action='store_true',help='Query LIVE status rather than DEV status')
    parser.add_argument('--timeout',type=float,default=1100)
    args=parser.parse_args()
    if args.timeout<=0:parser.error('--timeout must be positive')
    print(json.dumps(execute(args),ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
