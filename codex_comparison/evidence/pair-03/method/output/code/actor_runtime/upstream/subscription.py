"""Portable native Codex subscription transport; game pixels only, no API fallback."""
from __future__ import annotations
import base64
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

BASE = 'You are a visual game controller. Read the supplied screenshots and game instructions, then return only the requested structured decision. Make all movement and resource decisions yourself from visible observations. Do not use tools, inspect files, execute commands, or access hidden environment state.'
MINIMAL = 'Follow the latest user task policy and requested output exactly. Use brief reasoning sufficient for a correct decision. Preserve necessary checks, but avoid redundant verification, exhaustive alternatives, or expanding the task. Return only the requested output, then stop. Do not force reasoning to zero.'

def retain_input(path, payload):
    """Shared cohort inputs may be hardlinked and read-only; never rewrite them."""
    path=Path(path)
    if path.exists():
        if path.read_bytes()!=payload:raise ValueError('Immutable input mismatch: '+path.name)
    else:path.write_bytes(payload)

class SubscriptionClient:
    def __init__(self, suite=None, output=None, output_schema=None, lean=False, aggressive=False, system=None, **kwargs):
        self.output = Path(output or tempfile.mkdtemp(prefix='sol-native-'))
        self.output.mkdir(parents=True, exist_ok=True)
        self.schema = output_schema
        self.service_tier = kwargs.get('service_tier')
        self.base = system or (BASE if aggressive or lean else MINIMAL)
        self.timeout = float(os.environ.get('SOL_TURN_TIMEOUT', '180'))
        self.counter = 0
        self.rpc_id = 0
        self.events = queue.Queue()
        self.pending = deque()
        executable = os.environ.get('SOL_CODEX_BIN') or shutil.which('codex')
        if not executable:
            raise RuntimeError('Install Codex CLI and sign in with ChatGPT first; no API fallback.')
        self._workspace = tempfile.TemporaryDirectory(prefix='sol-pixels-')
        self.workspace = Path(self._workspace.name)
        settings = {'model':'gpt-6.1-sol', 'model_reasoning_effort':'low',
            'forced_login_method':'chatgpt', 'model_provider':'openai',
            'features.apps':False, 'features.plugins':False, 'features.shell_tool':False,
            'features.multi_agent':False, 'features.view_image':False,
            'web_search':'disabled', 'memories.use_memories':False,
            'memories.generate_memories':False, 'project_doc_max_bytes':0,
            'history.persistence':'none', 'personality':'none',
            'log_dir':str(self.workspace/'logs'), 'sqlite_home':str(self.workspace/'state')}
        # Match the original minimal harness: omit all extra coding-agent context.
        settings.update({'developer_instructions':'', 'skills.include_instructions':False,
            'include_apps_instructions':False, 'include_collaboration_mode_instructions':False,
            'include_environment_context':False, 'include_permissions_instructions':False,
            'mcp_servers':{}})
        for feature in ('unified_exec','multi_agent_v2','browser_use','browser_use_external',
            'browser_use_full_cdp_access','computer_use','image_generation','sleep_tool',
            'skill_search','workspace_dependencies','code_mode_host','recommended_plugins',
            'goals','worktrees','skill_mcp_dependency_install','code_mode','remote_plugin',
            'plugin_sharing','memories','hooks','in_app_browser','in_app_chat',
            'in_app_dictation','in_app_local_automation','in_app_updates','in_app_voice',
            'realtime_conversation','tool_suggest','auth_elicitation','tool_call_mcp_elicitation',
            'shell_snapshot'):
            settings['features.'+feature]=False
        system_path=self.workspace/'system.txt';system_path.write_text(self.base)
        settings['model_instructions_file']=str(system_path)
        settings['features.skip_host_skill_discovery']=True
        if self.service_tier == 'default':
            settings['service_tier']='default'
            settings['features.fast_mode']=False
        command = [executable, 'app-server', '--stdio']
        for key,value in settings.items():
            command += ['-c', key+'='+json.dumps(value)]
        child_env = {k:v for k,v in os.environ.items() if k not in (
            'OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','OPENAI_API_BASE')}
        if os.environ.get('SOL_CODEX_HOME'):
            child_env['CODEX_HOME'] = os.environ['SOL_CODEX_HOME']
        if kwargs.get('auth_only_home',False):
            source=Path(child_env.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
            self._auth_home=tempfile.TemporaryDirectory(prefix='sol-auth-only-')
            auth_target=Path(self._auth_home.name)/'auth.json'
            shutil.copyfile(source,auth_target);auth_target.chmod(0o600)
            child_env['CODEX_HOME']=self._auth_home.name

        self.stderr = (self.output/'native-stderr.log').open('w')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, cwd=self.workspace, env=child_env)
        threading.Thread(target=self._reader,daemon=True).start()
        try:
            self._rpc('initialize', {'clientInfo':{'name':'sol_realtime','title':'SOL Realtime','version':'0.1.0'}})
            self._send({'method':'initialized','params':{}})
            auth = self._rpc('account/read',{'refreshToken':False})
            if (auth.get('account') or {}).get('type') != 'chatgpt':
                raise RuntimeError('ChatGPT subscription login required; API authentication is rejected.')
            if self.service_tier == 'default':
                config=self._rpc('config/read',{'includeLayers':False})['config']
                if config.get('service_tier') != 'default' or config.get('features',{}).get('fast_mode') is not False:
                    raise RuntimeError('Native configuration did not disable Fast for default-tier simulation')
                (self.output/'subscription-settings.json').write_text(json.dumps({
                    'requested_service_tier':'default','native_config_service_tier':config['service_tier'],
                    'native_config_fast_mode':config['features']['fast_mode']},indent=2))
            (self.output/'transport.json').write_text(json.dumps({'auth':'chatgpt',
                'model':'gpt-6.1-sol','effort':'low','base_instructions_sha256':hashlib.sha256(self.base.encode()).hexdigest(),
                'base_instructions':self.base,'direct_api_calls':0,'adapter':'portable app-server'},indent=2))
        except BaseException:
            self.close()
            raise

    def _reader(self):
        for line in self.process.stdout:
            try:self.events.put(json.loads(line))
            except json.JSONDecodeError:continue
        self.events.put(None)

    def _send(self, row):
        self.process.stdin.write(json.dumps(row)+'\n')
        self.process.stdin.flush()

    def _receive(self, deadline):
        remaining = deadline-time.monotonic()
        if remaining <= 0:raise TimeoutError('Codex native turn timed out')
        try:event = self.events.get(timeout=remaining)
        except queue.Empty:raise TimeoutError('Codex native turn timed out')
        if event is None:raise RuntimeError('Codex app-server exited; see native-stderr.log')
        if 'id' in event and 'method' in event:
            self._send({'id':event['id'],'error':{'code':-32601,'message':'Interactive tools disabled'}})
            raise RuntimeError('Unexpected tool or approval request: '+event['method'])
        return event

    def _rpc(self,method,params):
        rid=self.rpc_id;self.rpc_id+=1
        self._send({'id':rid,'method':method,'params':params})
        deadline=time.monotonic()+self.timeout
        while True:
            event=self._receive(deadline)
            if event.get('id')==rid and 'method' not in event:
                if 'error' in event:raise RuntimeError(method+': '+json.dumps(event['error']))
                return event['result']
            self.pending.append(event)

    def quota(self, stop_percent=80):
        r=self._rpc('account/rateLimits/read',{})
        limits=r.get('rateLimits') or {}
        summary={}
        for key in ('primary','secondary'):
            v=limits.get(key) or {}
            if 'usedPercent' in v:
                summary[key]={'usedPercent':v['usedPercent'],'resetsAt':v.get('resetsAt')}
                if v['usedPercent']>=stop_percent:
                    raise RuntimeError('Subscription quota guard reached: '+key)
        return summary

    def act(self,prompt,images,directory=None):
        directory=Path(directory or self.output/f'call-{self.counter:05d}')
        directory.mkdir(parents=True,exist_ok=True)
        started=time.monotonic()
        quota=self.quota(int(os.environ.get('SOL_QUOTA_STOP_PERCENT','80')))
        r=self._rpc('thread/start',{'model':'gpt-6.1-sol','cwd':str(self.workspace),
            'approvalPolicy':'never','sandbox':'read-only','ephemeral':True,
            'developerInstructions':'',
            **({'serviceTier':self.service_tier} if self.service_tier else {})})
        if r.get('model')!='gpt-6.1-sol' or r.get('reasoningEffort')!='low':
            raise RuntimeError('Native model/effort mismatch')
        if self.service_tier == 'default':
            (directory/'subscription-settings.json').write_text(json.dumps({
                'native_thread_service_tier':r['serviceTier'],
                'instruction_sources':r['instructionSources']},indent=2))
            # Codex 0.162.1 returns null for an explicitly selected default-tier thread.
            if r['serviceTier'] not in (None, 'default') or r['instructionSources']:
                raise RuntimeError('Unexpected native tier or additional instruction sources')
        tid=r['thread']['id']
        contents=[{'type':'text','text':prompt}]
        for i,data in enumerate(images):
            target=self.workspace/f'{self.counter:05d}-{i}.png';target.write_bytes(data)
            retain_input(directory/f'image_{i}.png',data)
            contents.append({'type':'localImage','path':str(target)})
        retain_input(directory/'prompt.txt',prompt.encode())
        params={'threadId':tid,'model':'gpt-6.1-sol','effort':'low',
            'approvalPolicy':'never','sandboxPolicy':{'type':'readOnly'},'input':contents}
        if self.schema is not None:params['outputSchema']=self.schema
        if self.service_tier:params['serviceTier']=self.service_tier
        r=self._rpc('turn/start',params);turn=r['turn']['id'];messages=[];usage=None
        deadline=time.monotonic()+self.timeout
        try:
            with (directory/'events.jsonl').open('w')as log:
                while True:
                    event=self.pending.popleft()if self.pending else self._receive(deadline)
                    method=event.get('method','');p=event.get('params')or {}
                    if p.get('threadId',tid)!=tid or p.get('turnId',turn)!=turn:continue
                    if method in ('item/started','item/completed'):
                        if p['item']['type']not in ('agentMessage','reasoning','userMessage'):
                            raise RuntimeError('Policy attempted tool use: '+p['item']['type'])
                    if method=='model/rerouted':raise RuntimeError('Model rerouted')
                    if method=='item/completed'and p['item']['type']=='agentMessage':messages.append(p['item']['text'])
                    if method=='thread/tokenUsage/updated':usage=p['tokenUsage']
                    # Exclude all account/identity notifications from archived evidence.
                    if method.startswith(('item/','turn/','thread/tokenUsage')):log.write(json.dumps(event)+'\n')
                    if method=='turn/completed':
                        if p['turn']['status']!='completed' or not messages:raise RuntimeError('Native turn did not complete: '+str(p['turn'].get('error')))
                        break
            meta={'wall_seconds':time.monotonic()-started,'token_usage':usage,'auth':'chatgpt','quota_before':quota,'direct_api_calls':0}
            (directory/'result.json').write_text(json.dumps({'answer':messages[-1],'meta':meta},indent=2))
            self.counter+=1
            return messages[-1],meta
        finally:
            try:self._rpc('thread/unsubscribe',{'threadId':tid})
            except Exception:pass

    def close(self):
        if getattr(self,'process',None):
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if getattr(self,'stderr',None):self.stderr.close()
        if getattr(self,'_workspace',None):self._workspace.cleanup()
        if getattr(self,'_auth_home',None):self._auth_home.cleanup()

class AsyncProvider:
    async def start(self):
        import asyncio
        root=os.environ.get('SOL_NATIVE_LOG_ROOT')
        output=Path(tempfile.mkdtemp(prefix='worker-',dir=root)) if root else None
        system=(Path(__file__).resolve().parents[1]/'environments/rhythm/experiments/rhythm/outer/minimal-system.txt').read_text()
        self.client=await asyncio.to_thread(SubscriptionClient,output=output,system=system,service_tier='priority',auth_only_home=True)
        self.configuration={'auth':'chatgpt','adapter':'portable app-server'}
        return self
    async def act(self,prompt,images):
        import asyncio
        return await asyncio.to_thread(self.client.act,prompt,images)
    async def close(self):
        import asyncio
        if getattr(self,'client',None):await asyncio.to_thread(self.client.close)
