"""Trusted orchestration of the unchanged native Aster research runner.

No browser/model starts on import. The gateway calls async run(adapter, request).
Only the world process imports this module; diagnostics never cross to a model.
"""
from __future__ import annotations
import asyncio
import concurrent.futures as cf
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import urllib.parse
import uuid
from .http_transport import client_class,request_json
from .upstream.research import run_episode,subscription
from .upstream.research.policies import make_prompt
from .upstream.research.wire_schema import decision_schema
from .upstream.research.live_summary import add_brief_schema,brief_prompt,validate_brief
from .upstream.research.scheduler import parse_plan

HERE=Path(__file__).resolve().parent
SAFE_ID=re.compile(r'[A-Za-z0-9_-]{1,60}')
_lock=None


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(value).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(canonical(value)+b'\n');temporary.replace(path)
def conf(adapter,name,default=None):
    config=adapter.config
    return config.get(name,default) if isinstance(config,dict) else getattr(config,name,default)
def quantile(values,q):
    values=sorted(values)
    if not values:raise ValueError('Empty latency stratum')
    position=(len(values)-1)*q;lo=math.floor(position);hi=math.ceil(position)
    return values[lo]+(values[hi]-values[lo])*(position-lo)


def source_hashes():
    attribution=json.loads((HERE/'integrity.json').read_text())
    for row in attribution['runtime_files']:
        if digest((HERE/row['vendored']).read_bytes())!=row['sha256']:raise ValueError('Runtime source changed: '+row['vendored'])
    return {p.relative_to(HERE).as_posix():digest(p.read_bytes()) for p in sorted(HERE.rglob('*'))
            if p.is_file() and p.suffix in ('.py','.json') and '__pycache__' not in p.parts}


def public_summary(summary):
    allowed=('status','candidate','mode','provider','speed','tick','game_s','visual_frames','wall_s',
             'boss_hp','hits','lives','bombs','score','calls','drops','applied','invalid_actions','complete','won')
    return {key:summary[key] for key in allowed if key in summary}


def register_public(adapter,out,run_id):
    """Explicit output projection; no engine state, source tree or raw events."""
    plain={'request.json','public-result.json','public-failure.json','profile.json',
           'raw-cohorts.json','freeze-bundle.json'}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name in plain:
            adapter.register_public_artifact(f'method/{run_id}/{path.name}',path.read_bytes())
    episode=out/'episode'
    if not episode.exists():return
    # Upstream request rows contain absolute private output directories. Remove
    # only those locations; retain timing, dispositions, literal decisions.
    def project(value):
        if isinstance(value,dict):
            return {key:project(item) for key,item in value.items()
                    if key not in ('directory','shared_input_directory')}
        if isinstance(value,list):return [project(item) for item in value]
        return value
    for name in ('issued.jsonl','decisions.jsonl','actions.jsonl','actions.json'):
        path=episode/name
        if not path.exists():continue
        if path.suffix=='.jsonl':
            data=b''.join(canonical(project(json.loads(line)))+b'\n' for line in path.read_text().splitlines() if line.strip())
        else:data=canonical(project(json.loads(path.read_text())))+b'\n'
        adapter.register_public_artifact(f'method/{run_id}/episode/{name}',data)
    path=episode/'summary.json'
    if path.exists():adapter.register_public_artifact(f'method/{run_id}/episode/summary.json',canonical(public_summary(json.loads(path.read_text()))))


class SyncBrowserProxy:
    """Move sync runner calls onto the gateway loop, never a second browser."""
    def __init__(self,adapter,loop,run_kind):self.adapter,self.loop,self.run_kind=adapter,loop,run_kind
    def call(self,name,*args,**kwargs):
        return asyncio.run_coroutine_threadsafe(getattr(self.adapter,name)(*args,**kwargs),self.loop).result(timeout=60)
    def reset(self,seed=1,mode='sim',speed=1):return self.call('reset',seed=seed,mode=mode,speed=speed,run_kind=self.run_kind)
    def apply(self,plan,decision=None):return self.call('apply',plan,decision=decision)
    def advance(self,frames):return self.call('advance',frames)
    def capture(self):return self.call('capture')
    def clock(self):return self.call('clock')
    def diagnostics(self):return self.call('diagnostics')
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def make_config(config_id,profile_path,seed,*,live=False):
    catalog=json.loads((HERE/'candidates.json').read_text())
    if config_id not in catalog['candidates']:raise ValueError('Unregistered candidate')
    return {**catalog['common'],**catalog['candidates'][config_id],
            'candidate':catalog['candidate_policy'],'mode':'realtime' if live else 'sim',
            'profile':str(profile_path),'seed':seed,'delay_seed':7319,
            'cohort_delay':'independent' if live else 'empirical_joint',
            'simulation_terminal_hold':not live,'suite':'unused-http-broker','url':'gateway-owned-browser',
            'max_calls':catalog['limits']['live_calls'] if live else catalog['limits']['calls_per_simulation']}


def run_original(adapter,loop,operation,config,out,run_id):
    old_env,old_client=run_episode.BrowserEnv,subscription.SubscriptionClient
    run_episode.BrowserEnv=lambda *args,**kwargs:SyncBrowserProxy(adapter,loop,'live' if operation=='live' else 'dev')
    subscription.SubscriptionClient=client_class(conf(adapter,'broker_url','http://main:8090'),run_id,'live' if operation=='live' else 'dev')
    try:return run_episode.run(config,out)
    finally:run_episode.BrowserEnv=old_env;subscription.SubscriptionClient=old_client


def build_profile(records):
    groups=[];counts=[];raw=[]
    for cohort in records:
        if len(cohort['calls'])!=4:raise ValueError('Incomplete calibration cohort')
        vector=[]
        for row in cohort['calls']:
            if row.get('status')!='completed' or not row.get('valid_action'):
                raise ValueError('Failed/invalid native calibration retained; no fitted profile')
            delay=row['elapsed_s']
            if type(delay) not in (int,float) or not math.isfinite(delay) or delay<=0:raise ValueError('Invalid calibration duration')
            vector.append(delay);raw.append(delay)
        groups.append(vector);counts.append(cohort['image_count'])
    if counts!=[1,2,3,3]:raise ValueError('Expected fixed calibration strata 1,2,3,3')
    return {'kind':'native_terminal_cohort_profile','version':1,'k':4,'samples_s':raw,
        'p50_s':quantile(raw,.5),'p95_s':quantile(raw,.95),'joint_delay_vectors_s':groups,
        'joint_terminal_delay_vectors_s':groups,'image_counts':counts,'apply_overhead_s':0.0,
        'model':'gpt-6-astra','effort':'low','readiness_equals_terminal':True,
        'raw_cohorts_sha256':digest(canonical(records)),
        'limits':['Four DEV opening-image cohorts only; image strata have one or two groups.',
                  'Fixed image-count order is confounded with first-cohort worker initialization; no independent warmup sample.',
                  'Includes actual native/broker request latency; physical actuation overhead is not measured.',
                  'No early-action callback: readiness equals terminal availability. Future tails are unknown.']}


def profile_sync(adapter,loop,out,run_id):
    proxy=SyncBrowserProxy(adapter,loop,'profile')
    clock=proxy.reset(conf(adapter,'dev_seed'),mode='sim',speed=1)
    while clock.get('intro',0)>0:clock=proxy.advance(min(10000,max(1,clock['intro'])))
    images=[]
    for index in range(3):
        if index:proxy.advance(20)
        images.append(proxy.capture())
    prior=json.loads((HERE/'upstream/prior-profile.json').read_text())
    # Keep only the prior's marginal scalar context for the first measurement.
    # Do not transplant historical API actuator/control-age claims into native.
    prior_context={key:prior[key] for key in ('p50_s','p95_s')}
    config=make_config('initial',HERE/'upstream/prior-profile.json',conf(adapter,'dev_seed'))
    schema=add_brief_schema(decision_schema(config['candidate'],1,48))
    Client=client_class(conf(adapter,'broker_url','http://main:8090'),run_id,'dev')
    cohorts=[]
    for number,count in enumerate((1,2,3,3)):
        frames=images[-count:];observation={**frames[-1],'image_frames':[f['frame'] for f in frames]}
        prompt=brief_prompt(make_prompt(config['candidate'],observation,config,prior_context,None),'en')
        prompt=prompt.replace('Controller protocol: at most one request is in flight.',
            'Controller protocol: each observation races 4 independent requests; up to 4 total requests may be in flight.')
        prompt+='\nSame-observation race: 4 requests receive identical screenshots and prompt. The first legal answer takes effect; other answers for those screenshots cannot replace it. Every request has its own delay and cost.\n'
        prompt+='\nWinner-gated observations: no new observation cohort is submitted until a legal answer from the latest cohort has actually taken effect, or all candidates in that cohort have finished invalid (in simulation, only at their virtual due events). Slow stale replies from older cohorts may remain in flight. Movement completion is also required before the next cohort when wait_for_move is enabled.\n'
        prompt+='\nThis is a DEV latency calibration on retained opening screenshots. No returned action is executed. Historical delay statistics above are an upstream prior, not this new measurement.\n'
        start=time.monotonic();cohort={'cohort':number,'image_count':count,'prompt_sha256':digest(prompt.encode()),
             'image_sha256':[digest(f['png']) for f in frames],'calls':[]}
        def one(slot):
            directory=out/f'profile-{number}-{slot}';directory.mkdir()
            sent=time.monotonic();row={'slot':slot,'launch_offset_s':sent-start,'status':'failed','valid_action':False}
            try:
                answer,meta=Client(output=directory,output_schema=schema).act(prompt,[f['png'] for f in frames],directory)
                plan=parse_plan(answer)
                if len(plan)!=1 or plan[0].duration>48:raise ValueError('Profile plan outside frozen schema')
                validate_brief(answer)
                row.update(status='completed',valid_action=True,answer=answer,meta=meta)
            except Exception as error:row['error_type']=type(error).__name__
            row['elapsed_s']=time.monotonic()-start
            save(directory/'timing.json',row);return row
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(one,slot) for slot in range(4)]
            cohort['calls']=[future.result() for future in futures]
        cohorts.append(cohort);save(out/'raw-cohorts.json',cohorts)
    profile=build_profile(cohorts);save(out/'profile.json',profile)
    return {'profile':profile,'profile_sha256':digest(canonical(profile)),'raw_cohorts':cohorts}


def validate_bundle(bundle,current):
    if not isinstance(bundle,dict) or bundle.get('schema_version')!='aster-freeze-v1':raise ValueError('Wrong freeze bundle')
    if bundle.get('source_sha256')!=current:raise ValueError('Frozen method sources differ')
    if digest(canonical(bundle.get('profile')))!=bundle.get('profile_sha256'):raise ValueError('Frozen profile differs')
    expected=make_config(bundle.get('config_id'),'<profile>',0)
    if bundle.get('config')!={k:v for k,v in expected.items() if k not in ('profile','seed','delay_seed','url','suite','mode','max_calls','cohort_delay','simulation_terminal_hold')}:
        raise ValueError('Frozen candidate differs')
    body={k:v for k,v in bundle.items() if k!='freeze_id'}
    if bundle.get('freeze_id')!=digest(canonical(body)):raise ValueError('Freeze identity differs')
    return bundle


async def run(adapter,request):
    """Run one trusted workflow operation; gateway owns background lifecycle."""
    global _lock
    if _lock is None:_lock=asyncio.Lock()
    async with _lock:
        allowed={'operation','config_id','run_id','freeze_run_id'}
        if not isinstance(request,dict) or set(request)-allowed:raise ValueError('Unknown workflow fields')
        operation=request.get('operation');role=conf(adapter,'role')
        if operation not in ('profile','simulate','freeze','live'):raise ValueError('Unknown operation')
        if role not in ('dev','live'):raise ValueError('Trusted service role must be dev or live')
        if (operation=='live')!=(role=='live'):raise ValueError('Operation belongs on the other DEV/LIVE service')
        run_id=request.get('run_id') or 'method-'+uuid.uuid4().hex
        if not SAFE_ID.fullmatch(run_id):raise ValueError('Invalid run identity')
        root=Path(conf(adapter,'run_root'))/'method';root.mkdir(parents=True,exist_ok=True)
        state_path=root/'state.json';state=json.loads(state_path.read_text()) if state_path.exists() else {'profile':None,'simulations':[],'freeze':None,'live_started':False,'profile_started':False}
        current=source_hashes();out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=False)
        save(out/'request.json',{'run_id':run_id,'operation':operation,'config_id':request.get('config_id'),'started_at':now(),'source_sha256':current})
        loop=asyncio.get_running_loop()
        if operation=='profile':
            if state['profile_started']:raise ValueError('Calibration may run once; failed attempts are retained')
            state['profile_started']=True;save(state_path,state)
            try:
                result=await asyncio.to_thread(profile_sync,adapter,loop,out,run_id)
            except BaseException as error:
                save(out/'public-failure.json',{'operation':operation,'run_id':run_id,'error_type':type(error).__name__,'failed_at':now()})
                await adapter.abort('profile_failed');register_public(adapter,out,run_id)
                raise
            else:await adapter.abort('profile_completed')
            state['profile']={**result,'run_id':run_id};save(state_path,state)
        elif operation=='simulate':
            if not state['profile'] or state['freeze']:raise ValueError('DEV profile is required and freeze must not exist')
            if len(state['simulations'])>=2:raise ValueError('Two DEV simulations already dispatched')
            config_id=request.get('config_id','initial');profile=state['profile']['profile']
            profile_path=out/'profile.json';save(profile_path,profile)
            seeds=conf(adapter,'dev_seeds',[17,17])
            if seeds!=[17,17]:raise ValueError('Both registered DEV candidates use seed 17')
            if config_id in {record['config_id'] for record in state['simulations']}:
                raise ValueError('Each candidate may be simulated once; failures are retained')
            config=make_config(config_id,profile_path,seeds[len(state['simulations'])])
            record={'run_id':run_id,'config_id':config_id,'status':'dispatched'};state['simulations'].append(record);save(state_path,state)
            try:summary=await asyncio.to_thread(run_original,adapter,loop,operation,config,out/'episode',run_id)
            except BaseException as error:
                record.update(status='failed',error_type=type(error).__name__);save(state_path,state)
                save(out/'public-failure.json',{'operation':operation,**record,'failed_at':now()})
                await adapter.abort('simulation_failed');register_public(adapter,out,run_id)
                raise
            if not summary.get('complete'):await adapter.abort('simulation_incomplete')
            record.update(status='completed',summary=public_summary(summary));save(state_path,state)
            result={'run_id':run_id,'config_id':config_id,'summary':record['summary'],'profile_sha256':state['profile']['profile_sha256']}
        elif operation=='freeze':
            if state['freeze']:raise ValueError('Freeze already exists')
            config_id=request.get('config_id','initial')
            expected_candidates=set(json.loads((HERE/'candidates.json').read_text())['candidates'])
            if len(state['simulations'])!=2 or {r['config_id'] for r in state['simulations']}!=expected_candidates:
                raise ValueError('Both registered candidates must be simulated before freeze')
            if not all(r['status']=='completed' and r['summary'].get('complete') for r in state['simulations']):
                raise ValueError('Both DEV simulations must be complete before freeze')
            config=make_config(config_id,'<profile>',0)
            config={k:v for k,v in config.items() if k not in ('profile','seed','delay_seed','url','suite','mode','max_calls','cohort_delay','simulation_terminal_hold')}
            bundle={'schema_version':'aster-freeze-v1','config_id':config_id,'config':config,
                    'profile':state['profile']['profile'],'profile_sha256':state['profile']['profile_sha256'],
                    'source_sha256':current,'dev_summaries':state['simulations'],'created_at':now()}
            bundle['freeze_id']=digest(canonical(bundle));validate_bundle(bundle,current)
            state['freeze']={'run_id':run_id,'bundle':bundle};save(state_path,state)
            result={'run_id':run_id,'freeze_bundle':bundle}
        else:
            if state['live_started']:raise ValueError('Live already dispatched; never retry')
            trusted=conf(adapter,'frozen_bundle_path')
            if trusted:bundle=json.loads(Path(trusted).read_text())
            else:
                freeze_run_id=request.get('freeze_run_id','')
                if not SAFE_ID.fullmatch(freeze_run_id):raise ValueError('freeze_run_id required')
                url=conf(adapter,'dev_url','http://dev:8080')+'/workflow/status?id='+urllib.parse.quote(freeze_run_id)
                response=await asyncio.to_thread(request_json,url,None,20)
                if response.get('status')!='completed':raise ValueError('DEV freeze is not complete')
                bundle=response['result']['freeze_bundle']
            validate_bundle(bundle,current)
            await adapter.bind_method_bundle(bundle)
            state['live_started']=True;save(state_path,state);save(out/'freeze-bundle.json',bundle)
            profile_path=out/'profile.json';save(profile_path,bundle['profile'])
            config=make_config(bundle['config_id'],profile_path,conf(adapter,'live_seed'),live=True)
            try:summary=await asyncio.to_thread(run_original,adapter,loop,operation,config,out/'episode',run_id)
            except BaseException as error:
                save(out/'public-failure.json',{'operation':operation,'run_id':run_id,'error_type':type(error).__name__,'failed_at':now()})
                await adapter.abort('method_failed');register_public(adapter,out,run_id)
                raise
            if not summary.get('complete'):await adapter.abort('method_incomplete')
            result={'run_id':run_id,'config_id':bundle['config_id'],'summary':public_summary(summary),
                    'freeze_id':bundle['freeze_id'],'profile_sha256':bundle['profile_sha256']}
        if source_hashes()!=current:raise ValueError('Method sources changed during workflow operation')
        result.update(operation=operation,source_sha256=current,completed_at=now())
        save(out/'public-result.json',result)
        register_public(adapter,out,run_id)
        return result
