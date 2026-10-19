"""A single frozen vision-driven episode; simulation and true realtime are separate."""
import argparse, concurrent.futures as cf, hashlib, json, math, os, pathlib, random, threading, time, traceback
from collections import deque
from .environment import BrowserEnv
from .policies import make_prompt
from .scheduler import VirtualScheduler, parse_plan
from .recover_prefix import append_jsonl, durable_write, sync_directory
from .observation_history import ObservationHistory
from .known_plan_feedback import validate_known_plan_feedback, observation_gate_open


def diagnostics(env):
    raw=env.diagnostics()
    return {**raw['state'],'clock':raw['clock']}


def timing_clock(clock):
    """Browser clock evidence only; never add privileged state to prompts."""
    fields=('frame','visual_frame','intro','mode','speed','wall_ms',
            'max_raf_gap_ms','actual_visual_speed','blur_events','paused','status')
    return {field:clock[field] for field in fields if field in clock}


def write(path,value):
    durable_write(path,value)


def validate_episode_effort(config):
    from .wire_schema import validate_schema_order_config
    validate_schema_order_config(config)
    from .subscription import validate_subscription_effort
    effort=config.get('effort','low')
    # DecisionPool's fallback branch creates ApiClient for every non-subscription
    # provider. Match that branch, including malformed/unknown provider names.
    if config.get('provider')!='subscription' and effort!='low':
        raise ValueError('API episode effort currently supports low only; medium requires subscription')
    return validate_subscription_effort(effort)


def remember_model_estimate(memory,result,observed_frame):
    """Keep only the applied model's own old visual report, never engine state."""
    try:
        answer=result.get('answer')
        answer=json.loads(answer) if isinstance(answer,str) else answer
        boss=answer.get('boss') if isinstance(answer,dict) else None
        if not isinstance(boss,list) or len(boss)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in boss):return
        entry={'frame':observed_frame,'boss':list(boss)}
        if memory and observed_frame==memory[-1]['frame']:memory[-1]=entry
        elif not memory or observed_frame>memory[-1]['frame']:memory.append(entry)
    except (ValueError,TypeError):return


def shared_inputs(directory,prompt,images):
    """One immutable durable input bundle, linked into each decision directory."""
    directory.mkdir()
    payloads={'prompt.txt':prompt.encode('utf-8'),**{f'image_{i}.png':image for i,image in enumerate(images)}}
    for name,data in payloads.items():
        with (directory/name).open('xb') as output:
            output.write(data);output.flush();os.fchmod(output.fileno(),0o444);os.fsync(output.fileno())
    sync_directory(directory);sync_directory(directory.parent)
    return directory


class DecisionPool:
    def __init__(self,config,out,key=None):
        validate_episode_effort(config)
        self.config=config;self.out=out;self.key=key;self.local=threading.local();self.clients=[];self.lock=threading.Lock()
        self.timing_origin=None;self.early_results={}
        self.pool=cf.ThreadPoolExecutor(max_workers=config.get('workers',3))
    def take_early(self,directory):
        with self.lock:return self.early_results.pop(str(directory),None)
    def publish_early(self,directory,result):
        # Only complete API-validated JSON reaches this callback. Enforce the
        # frozen episode limits before exposing a durable action to the loop.
        result={**result,'meta':dict(result['meta'])}
        result['plan']=[a.keys+[a.duration] for a in parse_plan(result['answer'])]
        if len(result['plan'])>self.config.get('chunk_size',1) or any(a[4]>self.config.get('max_duration',48) for a in result['plan']):
            raise ValueError('Early action exceeds frozen chunk protocol')
        if self.config.get('decision_brief'):
            from .live_summary import validate_brief
            result['brief']=validate_brief(result['answer'])
        write(directory/'early_result.json',result)
        with self.lock:
            if str(directory) in self.early_results:raise RuntimeError('Duplicate early action publication')
            self.early_results[str(directory)]=result
    def validate_answer(self,answer):
        plan=parse_plan(answer)
        if self.config.get("decision_brief"):
            from .live_summary import validate_brief
            validate_brief(answer)
        if len(plan)>self.config.get("chunk_size",1) or any(a.duration>self.config.get("max_duration",48) for a in plan):
            raise ValueError("Action exceeds frozen chunk protocol")
        return plan

    def prepare_client(self):
        if not hasattr(self.local,'client'):
            with self.lock:
                ident=len(self.clients)
                from .wire_schema import decision_schema
                schema=decision_schema(self.config['candidate'],self.config.get('chunk_size',1),self.config.get('max_duration',48)) if self.config.get('structured') else None
                if self.config.get('decision_brief'):
                    from .live_summary import add_brief_schema
                    schema=add_brief_schema(schema)
                if self.config['provider']=='subscription':
                    from .subscription import SubscriptionClient
                    client=SubscriptionClient(pathlib.Path(self.config['suite']),self.out/f'client_{ident}',
                                              output_schema=schema,lean=self.config.get('lean_native',False),
                                              aggressive=self.config.get('aggressive_native',False),
                                              effort=self.config.get('effort','low'))
                else:
                    from .api_client import ApiClient
                    from .wire_schema import validate_schema_order_config
                    client=ApiClient(self.key,self.config['ledger'],stage='realtime',max_output_tokens=self.config.get('max_output_tokens',128),output_schema=schema,preserve_schema_order=validate_schema_order_config(self.config,schema),budget_limits=self.config.get("budget_limits"))
                self.clients.append(client);self.local.client=client
    def warm(self):
        barrier=threading.Barrier(self.config.get('workers',3))
        def initialize():
            self.prepare_client()
            barrier.wait(timeout=120)
        futures=[self.pool.submit(initialize) for _ in range(self.config.get('workers',3))]
        for future in futures:future.result()

    def decide(self,prompt,images,directory):
        timed=self.config.get('stage_timings',False) or self.config.get('shared_cohort_inputs',False)
        stage={'worker_started_wall_s':time.monotonic()-self.timing_origin} if timed and self.timing_origin is not None else None
        self.prepare_client()
        c=self.local.client
        if stage is not None:stage['client_ready_wall_s']=time.monotonic()-self.timing_origin
        if self.config['provider']=='subscription':r=c.act(prompt,images,directory)
        elif self.config.get('early_action_delivery',False):
            r=c.decide(prompt,images,validate=self.validate_answer,timing_origin=self.timing_origin,
                       on_legal_action=lambda result:self.publish_early(directory,result))
        elif stage is not None:r=c.decide(prompt,images,validate=self.validate_answer,timing_origin=self.timing_origin)
        else:r=c.decide(prompt,images,validate=self.validate_answer)
        if stage is not None:stage['client_returned_wall_s']=time.monotonic()-self.timing_origin
        if isinstance(r,tuple):r={'answer':r[0],'meta':r[1]}
        try:
            r['plan']=[a.keys+[a.duration] for a in parse_plan(r['answer'])]
            if self.config.get('decision_brief'):
                from .live_summary import validate_brief
                r['brief']=validate_brief(r['answer'])
            if len(r['plan'])>self.config.get('chunk_size',1) or any(a[4]>self.config.get('max_duration',48) for a in r['plan']):
                raise ValueError('Action exceeds frozen chunk protocol')
        except (ValueError,TypeError):r['plan']=None;r['invalid_action']=True
        if self.config.get('decision_fanout',1)>1:r['race_completed_ns']=time.monotonic_ns()
        if stage is not None:
            stage.update(r.setdefault('meta',{}).get('stage_timing',{}))
            stage['result_write_started_wall_s']=time.monotonic()-self.timing_origin
            r['meta']['stage_timing']=stage
        write(directory/'result.json',r)
        if stage is not None:stage['result_durable_wall_s']=time.monotonic()-self.timing_origin
        return r
    def submit(self,*a):return self.pool.submit(self.decide,*a)
    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=not self.config.get('early_action_delivery',False) and self.config.get('decision_fanout',1)==1)
        for client in self.clients:
            try:client.close()
            except Exception:pass


def sample_delay(profile,rng,speed,scale):
    samples=profile.get('samples_s') or [profile.get('p50_s',.7)]
    return max(1,math.ceil(rng.choice(samples)*60*speed*scale))


def run(config,out,key=None):
    validate_known_plan_feedback(config)
    validate_episode_effort(config)
    if config.get("decision_brief") and (config.get("mode") not in ("sim","realtime") or config.get("provider") not in ("subscription","api") or not config.get("structured")):
        raise ValueError("Decision brief requires structured subscription or API decisions")
    if config.get("brief_language","zh") not in ("zh","en"):
        raise ValueError("Unknown brief language")
    max_calls=config.get('max_calls')
    if max_calls is not None and (type(max_calls) is not int or max_calls<1):
        raise ValueError('max_calls must be a positive integer')
    history=ObservationHistory(config)
    early=config.get('early_action_delivery',False)
    if early and (config.get('mode','sim')!='realtime' or config.get('provider')!='api' or not config.get('structured')):
        raise ValueError('early_action_delivery requires realtime structured API decisions')
    latency_scale=config.get('latency_scale',1)
    if type(latency_scale) not in (int,float) or not math.isfinite(latency_scale) or latency_scale<=0:
        raise ValueError('latency_scale must be finite and positive')
    memory_points=config.get('visual_memory_points',0)
    if type(memory_points) is not int or not 0<=memory_points<=16:raise ValueError('visual_memory_points must be 0..16')
    visual_memory=deque(maxlen=memory_points or 1)
    fanout=config.get('decision_fanout',1)
    if type(fanout) is not int or fanout<1:raise ValueError('decision_fanout must be a positive integer')
    if fanout>config.get('capacity',8) or fanout>config.get('workers',3):raise ValueError('Fanout needs at least K capacity slots and actual workers')
    shared=config.get('shared_cohort_inputs',False)
    timed=config.get('stage_timings',False) or shared
    cohort_delay=config.get('cohort_delay','independent')
    if cohort_delay not in ('independent','shared','empirical_joint'):raise ValueError('cohort_delay must be independent, shared, or empirical_joint')
    terminal_hold=config.get('simulation_terminal_hold',False)
    if type(terminal_hold) is not bool:raise ValueError('simulation_terminal_hold must be boolean')
    if terminal_hold and (config.get('mode','sim')!='sim' or cohort_delay!='empirical_joint'):
        raise ValueError('simulation_terminal_hold requires empirical_joint simulation')
    out=pathlib.Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    write(out/'config.json',config)
    profile=json.loads(pathlib.Path(config['profile']).read_text())
    if cohort_delay=='empirical_joint' and profile.get('k')!=fanout:raise ValueError('Empirical cohort K must match decision_fanout')
    if terminal_hold and 'joint_terminal_delay_vectors_s' not in profile:
        raise ValueError('simulation_terminal_hold requires paired terminal delay vectors')
    rng=random.Random(config.get('delay_seed',113))
    decisions=[];applied=[];errors=[];pool=None;started=time.monotonic();realpending={};lastobs=-1;lastissued=-1
    scheduler=VirtualScheduler(capacity=config.get('capacity',8),first_valid_per_observation=fanout>1)
    mode=config.get('mode','sim');cadence=config.get('decision_stride',15);seq=0;next_capture=0;next_request=0;drops=0;invalid_actions=0
    summary=None
    replay=[]
    prefix_count=0
    # Create empty logs durably before any request can be submitted.
    for name in ('issued.jsonl','actions.jsonl','completed_prefix.jsonl'):
        with (out/name).open('w') as log:
            log.flush();os.fsync(log.fileno())
    sync_directory(out)
    def flush_completed():
        nonlocal prefix_count
        while prefix_count<len(decisions):
            row,future=decisions[prefix_count]
            if not future.done() or future.cancelled() or future.exception() is not None:break
            append_jsonl(out/'completed_prefix.jsonl',{**row,**future.result()})
            prefix_count+=1
    def record_applied(row):
        applied.append(row)
        append_jsonl(out/'actions.jsonl',row)
    if config.get('replay_prefix'):
        if mode!='sim':raise ValueError('Realtime interrupted episodes cannot be replay-resumed')
        replay=[json.loads(line) for line in pathlib.Path(config['replay_prefix']).read_text().splitlines()]
    try:
        pool=DecisionPool(config,out,key)
        pool.timing_origin=started
        if config.get('warm_clients'):pool.warm()
        with BrowserEnv(config['url'],record_video_dir=str(out/'video') if mode=='realtime' else None,decision_panel=config.get('decision_brief',False) and mode=='realtime',panel_provider=config.get('provider','subscription'),panel_language=config.get('brief_language','zh'),browser_executable=config.get('browser_executable')) as env:
            env.reset(config['seed'],mode=mode,speed=config.get('speed',1))
            state=diagnostics(env);clock=state['clock']
            while state['status']=='playing':
                vframe=clock['visual_frame'];frame=clock['frame']
                if mode=='sim':
                    events=scheduler.advance(vframe)
                    for event in events:
                        if event.get('disposition')=='invalid':
                            if event.get('error_type') in ('ValueError','TypeError','JSONDecodeError'):
                                invalid_actions+=1;continue
                            raise RuntimeError('Subscription decision failed: '+str(event))
                        if event.get('disposition')=='applied':
                            clock=env.apply(event['plan']);record_applied({'visual_frame':vframe,'logical_frame':frame,**event,'apply_clock':timing_clock(clock)})
                            if memory_points:
                                observed,future=decisions[event['id']]
                                remember_model_estimate(visual_memory,future.result(),observed['logical_frame'])
                else:
                    ready=[]
                    for future in list(realpending):
                        row=realpending[future]
                        terminal_ready=future.done()
                        if early and not row.get('early_consumed'):
                            result=pool.take_early(pathlib.Path(row['directory']))
                            if result is not None:
                                row.update(early_consumed=True,early_result=result,
                                           early_legal_ready_ns=result['meta']['early_legal_ready_ns'])
                                if row['visual_frame']>lastobs:
                                    ready.append({**row,'result':result,'race_completed_ns':row['early_legal_ready_ns']})
                                else:row['disposition']='stale'
                        if terminal_ready and (early or fanout==1 or 'race_completed_ns' in row):
                            realpending.pop(future)
                            try:result=future.result()
                            except Exception as exc:
                                row.update(disposition='failed',error=repr(exc));errors.append(row)
                                raise
                            row.update(result=result,completed_wall_s=time.monotonic()-started)
                            if early and row.get('early_consumed'):continue
                            if result.get('invalid_action'):
                                invalid_actions+=1;row['disposition']='invalid';continue
                            if row['visual_frame']>lastobs:ready.append(row)
                            else:row['disposition']='stale'
                    if ready:
                        selected=max(ready,key=lambda x:(x['visual_frame'],-x.get('race_completed_ns',0))) if fanout>1 or early else max(ready,key=lambda x:x['visual_frame']);lastobs=selected['visual_frame']
                        if config.get('decision_brief'):
                            clock=env.apply(selected['result']['plan'],decision={'brief':selected['result']['brief'],'seq':selected['seq'],'observed_frame':selected['visual_frame']})
                        else:clock=env.apply(selected['result']['plan'])
                        for row in ready:
                            row['disposition']='applied' if row is selected else ('stale' if (fanout>1 or early) and row['visual_frame']==selected['visual_frame'] else 'superseded')
                            if early:decisions[row['seq']][0]['disposition']=row['disposition']
                        action={'visual_frame':clock['visual_frame'],'logical_frame':clock['frame'],'observed_frame':selected['visual_frame'],'plan':selected['result']['plan'],'age_frames':clock['visual_frame']-selected['visual_frame'],'apply_clock':timing_clock(clock),'applied_wall_s':time.monotonic()-started}
                        if early:
                            applied_ns=time.monotonic_ns()
                            action.update(seq=selected['seq'],early_legal=bool(selected['result'].get('meta',{}).get('early_legal')),
                                          early_legal_ready_ns=selected.get('early_legal_ready_ns'),applied_monotonic_ns=applied_ns,
                                          capture_monotonic_ns=selected['capture_monotonic_ns'],
                                          capture_started_monotonic_ns=selected['capture_started_monotonic_ns'],
                                          capture_start_to_apply_s=(applied_ns-selected['capture_started_monotonic_ns'])/1e9,
                                          capture_to_apply_s=(applied_ns-selected['capture_monotonic_ns'])/1e9,
                                          capture_clock=selected['capture_clock'])
                        if config.get('decision_brief'):action.update(brief=selected['result']['brief'],seq=selected['seq'])
                        record_applied(action)
                        if memory_points:remember_model_estimate(visual_memory,selected['result'],selected['logical_frame'])
                flush_completed()
                if vframe>=next_capture and (clock['intro']==0 or (config.get('intro_observation') and seq==0)):
                    capture_started_ns=time.monotonic_ns();observation=env.capture();capture_monotonic_ns=time.monotonic_ns();capture_completed=time.monotonic()-started;history.append(observation['frame'],observation['png'])
                    next_capture=history.next_capture_after(vframe,observation)
                    if vframe>=next_request:
                        next_request=vframe+cadence
                        available=(scheduler.capacity-scheduler.inflight>=fanout) if mode=='sim' else len(realpending)+fanout<=config.get('capacity',8)
                        if max_calls is not None:available=available and seq+fanout<=max_calls
                        available=observation_gate_open(config,clock,available,lastissued,
                            scheduler.latest_observation if mode=='sim' else lastobs,
                            lambda issued: scheduler.has_pending_observation(issued) if mode=='sim' else any(row['visual_frame']==issued for row in realpending.values()))
                        if available:
                            selected_images=history.select()
                            images=[x[1] for x in selected_images];image_frames=[x[0] for x in selected_images]
                            ob={**observation,'image_frames':image_frames};ob.pop('png')
                            if memory_points:ob['visual_memory']=list(visual_memory)
                            prompt=make_prompt(config['candidate'],ob,config,profile,clock.get('active_plan'))
                            if config.get('decision_brief'):
                                from .live_summary import brief_prompt
                                prompt=brief_prompt(prompt,config.get('brief_language','zh'))
                            if fanout>1:
                                prompt=prompt.replace('Controller protocol: at most one request is in flight.',f'Controller protocol: each observation races {fanout} independent requests; up to {config.get("capacity",8)} total requests may be in flight.')
                                prompt+=f'\nSame-observation race: {fanout} requests receive identical screenshots and prompt. The first legal answer takes effect; other answers for those screenshots cannot replace it. Every request has its own delay and cost.\n'
                            if config.get('wait_for_winner'):
                                prompt+='\nWinner-gated observations: no new observation cohort is submitted until a legal answer from the latest cohort has actually taken effect, or all candidates in that cohort have finished invalid (in simulation, only at their virtual due events). Slow stale replies from older cohorts may remain in flight. Movement completion is also required before the next cohort when wait_for_move is enabled.\n'
                            if fanout>1 and mode=='sim' and cohort_delay=='empirical_joint':
                                prompt+='\nDelay condition: entire candidate readiness vectors are jointly resampled from earlier completed real API episodes, retaining observed launch skew, correlation, and control-pipeline overhead. These are past measurements, not the delay of this request.\n'
                                if terminal_hold:
                                    prompt+='\nEarly-action capacity simulation: a complete legal answer can take effect before its request releases its concurrency slot. Readiness and terminal slot delays are jointly resampled from historical timing; any censored terminal tail follows the declared profile model. Sampled per-request delays are hidden.\n'
                            elif fanout>1 and mode=='sim' and 'cohort_delay' in config:
                                prompt+=f'\nDelay simulation: this cohort uses {cohort_delay} draws from an unloaded serial API profile; this is a concurrency stress assumption, not calibrated concurrent API performance. Shared means every candidate receives the same sampled delay; independent means separate sampled delays.\n'
                            if mode=='sim' and latency_scale!=1:
                                prompt+=f'\nArtificial latency stress: simulated control delays are {latency_scale:g} times the historical measurements above. This is a declared development assumption, not a new API measurement or the known delay of this request. Account for this factor when predicting threats and alignment.\n'
                            cohort_seq=seq
                            inputs_started=time.monotonic()-started if timed else None
                            bundle=shared_inputs(out/f'cohort_{cohort_seq:05d}_inputs',prompt,images) if shared else None
                            inputs_durable=time.monotonic()-started if shared else None
                            joint_delays=None;terminal_delays=None
                            if mode=='sim' and cohort_delay=='empirical_joint':
                                from .control_delays import sample_cohort_delay_frames, sample_cohort_ready_terminal_frames
                                if terminal_hold:
                                    joint_delays,terminal_delays=sample_cohort_ready_terminal_frames(profile,rng,config.get('speed',1)*latency_scale,image_count=len(images))
                                else:joint_delays=sample_cohort_delay_frames(profile,rng,config.get('speed',1)*latency_scale,image_count=len(images))
                            shared_delay=sample_delay(profile,rng,config.get('speed',1),config.get('latency_scale',1)) if mode=='sim' and cohort_delay=='shared' else None
                            for candidate_index in range(fanout):
                                if early:
                                    # Recheck after capture/input work before admitting
                                    # another paid request when an earlier tail failed.
                                    for _,pending_future in decisions:
                                        if pending_future.done():pending_future.result()
                                directory=out/f'decision_{seq:05d}';directory.mkdir()
                                if shared:
                                    for source in bundle.iterdir():os.link(source,directory/source.name)
                                else:
                                    for i,image in enumerate(images):(directory/f'image_{i}.png').write_bytes(image)
                                    (directory/'prompt.txt').write_text(prompt)
                                    # Sync model inputs before the manifest exposes them.
                                    for input_path in [directory/'prompt.txt',*directory.glob('image_*.png')]:
                                        with input_path.open('rb') as source:os.fsync(source.fileno())
                                row={'seq':seq,'visual_frame':observation['clock']['visual_frame'] if mode=='realtime' else vframe,'logical_frame':observation['frame'] if mode=='realtime' else frame,'capture_clock':timing_clock(observation.get('clock',clock)),'image_frames':image_frames,'image_sha256':[hashlib.sha256(x).hexdigest() for x in images],'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'held':ob['held'],'submitted_wall_s':time.monotonic()-started,'directory':str(directory)}
                                if early:row.update(capture_monotonic_ns=capture_monotonic_ns,capture_started_monotonic_ns=capture_started_ns)
                                if fanout>1:row.update(cohort_id=cohort_seq,fanout_index=candidate_index,decision_fanout=fanout,cohort_delay=cohort_delay)
                                if mode=='sim':row['delay_frames']=joint_delays[candidate_index] if joint_delays is not None else (shared_delay if shared_delay is not None else sample_delay(profile,rng,config.get('speed',1),config.get('latency_scale',1)))
                                if terminal_hold:row['terminal_delay_frames']=terminal_delays[candidate_index]
                                if timed:row['stage_timing']={'capture_completed_wall_s':capture_completed,'inputs_started_wall_s':inputs_started,'inputs_durable_wall_s':inputs_durable if shared else time.monotonic()-started,'manifest_started_wall_s':time.monotonic()-started}
                                if shared:row['shared_input_directory']=str(bundle)
                                write(directory/'request.json',row)
                                sync_directory(out)
                                if timed:row['stage_timing']['manifest_durable_wall_s']=time.monotonic()-started
                                append_jsonl(out/'issued.jsonl',row)
                                if timed:row['stage_timing']['issued_durable_wall_s']=time.monotonic()-started
                                if seq<len(replay):
                                    previous=replay[seq]
                                    if 'prompt_sha256' not in previous:
                                        previous_prompt=pathlib.Path(config['replay_prefix']).parent/f'decision_{seq:05d}'/'prompt.txt'
                                        previous['prompt_sha256']=hashlib.sha256(previous_prompt.read_bytes()).hexdigest()
                                    for field in ('prompt_sha256','seq','visual_frame','logical_frame','image_frames','image_sha256','held','delay_frames'):
                                        if row.get(field)!=previous.get(field):raise ValueError('Replay mismatch '+field+' at '+str(seq))
                                    if terminal_hold and row['terminal_delay_frames']!=previous.get('terminal_delay_frames'):
                                        raise ValueError('Replay mismatch terminal_delay_frames at '+str(seq))
                                    result={'answer':previous['answer'],'meta':previous.get('meta',{}),'plan':previous['plan'],'replayed':True}
                                    write(directory/'result.json',result)
                                    future=cf.Future();future.set_result(result)
                                else:
                                    if timed:row['stage_timing']['future_submit_started_wall_s']=time.monotonic()-started
                                    future=pool.submit(prompt,images,directory)
                                    if timed:row['stage_timing']['future_submit_returned_wall_s']=time.monotonic()-started
                                if mode=='sim':scheduler.submit(vframe,future,row['delay_frames'],terminal_delay_frames=row.get('terminal_delay_frames'))
                                else:realpending[future]=row
                                if fanout>1:
                                    def completed(future, row=row, directory=directory):
                                        if future.cancelled():
                                            row['race_completed_ns']=time.monotonic_ns();write(directory/'error.json',{'error':'cancelled'})
                                        elif future.exception() is not None:
                                            row['race_completed_ns']=time.monotonic_ns();write(directory/'error.json',{'error':repr(future.exception())})
                                        else:row['race_completed_ns']=future.result().get('race_completed_ns',time.monotonic_ns())
                                    future.add_done_callback(completed)
                                decisions.append((row,future));seq+=1
                                flush_completed()
                            lastissued=observation['clock']['visual_frame'] if mode=='realtime' else vframe
                        else:drops+=1
                if mode=='sim':
                    env.advance(1)
                else:
                    time.sleep(.002)
                state=diagnostics(env);clock=state['clock']
                if seq and seq%10==0 and seq!=getattr(run,'_last_progress',None):
                    run._last_progress=seq
                    progress={k:state[k] for k in ('status','tick','hits','score')};progress.update(boss_hp=state['boss']['hp'],lives=state['player']['lives'],calls=seq,wall_s=time.monotonic()-started,drops=drops)
                    write(out/'progress.json',progress);print('PROGRESS '+json.dumps(progress),flush=True)
                limit=config.get('max_wall_s',7200)
                if time.monotonic()-started>limit:raise TimeoutError('Episode wall limit exceeded; incomplete')
                if config.get('max_visual_frames') and clock['visual_frame']>=config['max_visual_frames']:break
                if config.get('max_game_frames') and state['tick']>=config['max_game_frames']:
                    break
            summary={'status':state['status'],'seed':config['seed'],'candidate':config['candidate'],'mode':mode,'provider':config['provider'],'speed':config.get('speed',1),'tick':state['tick'],'game_s':state['tick']/60,'visual_frames':clock['visual_frame'],'wall_s':time.monotonic()-started,'boss_hp':state['boss']['hp'],'hits':state['hits'],'lives':state['player']['lives'],'bombs':state['player']['bombs'],'score':state['score'],'calls':seq,'drops':drops,'applied':len(applied),'invalid_actions':invalid_actions,'replayed_calls':min(seq,len(replay)),'complete':state['status']!='playing','won':state['status']=='won'}
            (out/'final.png').write_bytes(env.capture()['png'])
            write(out/'final_state.json',state);write(out/'actions.json',applied)
            if not early:write(out/'summary.json',summary)
    except BaseException as exc:
        write(out/'failure.json',{'error':repr(exc),'traceback':traceback.format_exc(),'calls':seq,'wall_s':time.monotonic()-started})
        raise
    finally:
        # Persist the currently ready consecutive prefix before draining.
        try:flush_completed()
        finally:
            if pool:pool.close()
        flush_completed()
        terminal_errors=[]
        with (out/'decisions.jsonl').open('w') as log:
            for row,future in decisions:
                try:result=future.result()
                except BaseException as exc:
                    result={'error':repr(exc)};terminal_errors.append({'seq':row['seq'],'error':repr(exc)})
                log.write(json.dumps({**row,**result})+'\n')
            log.flush();os.fsync(log.fileno())
        if early:
            if terminal_errors:
                if summary is None:summary={'calls':seq,'mode':mode}
                summary.update(won=False,complete=False,validation_failure=True,terminal_errors=terminal_errors)
                write(out/'summary.json',summary)
                write(out/'failure.json',{'error':'Terminal decision validation failed','terminal_errors':terminal_errors})
                raise RuntimeError('Terminal decision validation failed: '+repr(terminal_errors))
            if summary is not None:
                summary['terminal_accounting_complete']=True
                write(out/'summary.json',summary)
    print('EPISODE '+json.dumps(summary),flush=True)
    return summary


def main():
    import signal
    def checkpoint_signal(signum,frame):raise KeyboardInterrupt('Operational checkpoint signal '+str(signum))
    signal.signal(signal.SIGINT,checkpoint_signal);signal.signal(signal.SIGTERM,checkpoint_signal)
    p=argparse.ArgumentParser();p.add_argument('--config',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True)
    args=p.parse_args();config=json.loads(args.config.read_text());validate_episode_effort(config);key=None
    if config['provider']=='api':
        import getpass;key=getpass.getpass('API key (memory only): ')
    run(config,args.output,key)

if __name__=='__main__':main()
