"""Native ChatGPT subscription only; no API fallback or copied credentials."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid


LEAN_BASE_INSTRUCTIONS = (
    "You are a visual game controller. Read the supplied screenshots and game "
    "instructions, then return only the requested structured decision. Make all "
    "movement and resource decisions yourself from visible observations. Do not "
    "use tools, inspect files, execute commands, or access hidden environment state."
)
LEAN_NATIVE_SETTINGS = {
    'features.shell_tool': False,
    'features.multi_agent': False,
    'tools.view_image': False,
    'web_search': 'disabled',
    'personality': 'none',
}


class SubscriptionClient:
    """One sequential native process per actual worker; output must be unique.

    Existing suite configuration supplies native package/bubblewrap locations.
    ASTER_SUBSCRIPTION_CONFIG may select another suite YAML; CODEX home is the
    existing native login directory (override with ASTER_CODEX_HOME). Credentials
    stay there and are never copied or included in logs.
    """
    def __init__(self, suite: Path, output: Path, output_schema: dict | None = None, lean: bool = False, aggressive: bool = False, effort: str = "low"):
        validate_subscription_effort(effort)
        from .wire_schema import schema_hash
        # Snapshot caller-owned schema so concurrent mutation cannot change a turn.
        output_schema = json.loads(json.dumps(output_schema, allow_nan=False)) if output_schema is not None else None
        self.lean = bool(lean)
        self.aggressive = bool(aggressive)
        self.output_schema = output_schema
        self.output_schema_hash = schema_hash(output_schema)
        suite, output = Path(suite).resolve(), Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        sys.path.insert(0, str(suite / 'code'))
        from latency_bench.core.config import load_config
        from latency_bench.policy.codex import CodexPolicyRunner
        config_path = Path(os.environ.get('ASTER_SUBSCRIPTION_CONFIG', str(
            suite / 'outputs/suite-20ep/sol-parallel-20261011T211538Z/configs/asterix_sol.yaml')))
        settings = dict(load_config(config_path)['policy'])
        settings.update(model='gpt-6.1-sol', effort='low', turn_timeout_seconds=120,
                        codex_home=os.environ.get('ASTER_CODEX_HOME', str(Path.home() / '.codex')))
        for key in ('package_root', 'bubblewrap_executable'):
            value = os.environ.get('ASTER_' + key.upper())
            if value:
                settings[key] = value
        for key in ('workspace_root', 'runtime_root'):
            path = output / key
            path.mkdir()
            settings[key] = str(path)

        class NativeRunner(CodexPolicyRunner):
            def _archive_home(self, destination):
                # Shared auth is mounted directly; never archive existing state.
                destination.mkdir()

            def _sandbox_command(self, workspace, runtime):
                command = super()._sandbox_command(workspace, runtime)
                command += ['-c', 'model_provider="openai"', '-c', 'forced_login_method="chatgpt"']
                if aggressive:
                    from .native_envelope import isolated_command
                    command,self.envelope_settings=isolated_command(command,runtime,Path(self.settings['codex_home']))
                elif lean:
                    for key, value in LEAN_NATIVE_SETTINGS.items():
                        command += ['-c', key + '=' + json.dumps(value)]
                return command

            def _start_episode(self, episode_id):
                # Own subprocess env rather than mutating process-global os.environ
                # while 20+ independent workers may be starting concurrently.
                self.episode_id, self.decision_id = episode_id, 0
                self.session_id = str(uuid.uuid4())
                self.episode_dir = self.output / f'episode_{episode_id:03d}'
                self.episode_dir.mkdir()
                self.workspace = Path(self.settings['workspace_root']) / self.session_id
                (self.workspace / 'observations').mkdir(parents=True)
                self.runtime = tempfile.TemporaryDirectory(prefix='codex-', dir=self.settings['runtime_root'])
                command = self._sandbox_command(self.workspace, Path(self.runtime.name))
                self.stderr = (self.episode_dir / 'stderr.log').open('w')
                # Explicit allowlist: neither paid API credentials nor arbitrary
                # endpoint/provider overrides reach the child, even before bwrap.
                child_env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TZ') if key in os.environ}
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self.stderr, text=True, start_new_session=True, env=child_env)
                self.lines = queue.Queue()
                self.reader = threading.Thread(target=self._read_lines, args=(self.process.stdout, self.lines), daemon=True)
                self.reader.start()
                self._initialize_session()

            def _rpc(self, method, params, *args):
                params = dict(params)
                if method == 'thread/start':
                    params['sandbox'] = 'read-only'
                    if aggressive:
                        params['developerInstructions']=''
                    elif lean:
                        # Covers both the suite's startup thread and every act().
                        params['baseInstructions'] = LEAN_BASE_INSTRUCTIONS
                elif method == 'turn/start':
                    params['sandboxPolicy'] = {'type': 'readOnly'}
                    if output_schema is not None:
                        params['outputSchema'] = output_schema
                return super()._rpc(method, params, *args)

            def _receive_event(self, deadline, events):
                event = super()._receive_event(deadline, events)
                item = event.get('params', {}).get('item', {})
                if event.get('method') in ('item/started', 'item/completed') and item.get('type') not in (
                    'agentMessage', 'reasoning', 'userMessage'):
                    raise RuntimeError('Unexpected native tool item: ' + str(item.get('type')))
                return event

        runner = NativeRunner.__new__(NativeRunner)
        runner.settings = settings
        runner.output = output / 'native'
        runner.output.mkdir()
        runner.process, runner.runtime = None, None
        self.runner, self.count = runner, 0
        self._lock = threading.Lock()
        # account/read's identity-bearing event must not persist on disk.
        from io import StringIO
        try:
            runner._start_episode(0)
            account_result, _ = runner._rpc('account/read', {'refreshToken': False}, StringIO(),
                                           time.monotonic() + 30, output / 'account_request.json')
            if (account_result.get('account') or {}).get('type') != 'chatgpt':
                raise RuntimeError('ChatGPT subscription authentication required; no API fallback')
            if aggressive:
                from .native_envelope import effective_subset, VERSION_ADAPTATIONS
                effective,_=runner._rpc('config/read', {'includeLayers':False,'cwd':'/workspace'}, StringIO(),
                    time.monotonic()+30,output/'effective_config_request.json')
                checked=effective_subset(effective['config'],runner.envelope_settings)
                version=subprocess.run([str(Path(settings['package_root'])/'bin/codex'),'--version'],
                    capture_output=True,text=True,check=True,env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL','TZ') if k in os.environ}).stdout.strip()
                (output/'envelope.json').write_text(json.dumps({'variant':'aggressive','native_version':version,
                    'strict_config':True,'effective_config':checked,'version_adaptations':VERSION_ADAPTATIONS,
                    'auth_mount':'original auth.json read-only; no auth copy/archive','isolated_codex_home':True,
                    'model_instructions_sha256':hashlib.sha256((LEAN_BASE_INSTRUCTIONS+'\n').encode()).hexdigest()},indent=2)+'\n')
            (output / 'auth_mode.json').write_text(json.dumps({'type': 'chatgpt', 'model': settings['model'],
                'effort': settings['effort'], 'direct_api_calls': 0}) + '\n')
        except BaseException:
            runner.close()
            raise

    def act(self, prompt: str, images: list[bytes], directory: Path) -> dict:
        if not isinstance(prompt, str) or not 1 <= len(images) <= 3:
            raise ValueError('Require a text prompt and 1..3 PNG screenshots')
        if any(not isinstance(image, bytes) or not image.startswith(b'\x89PNG\r\n\x1a\n') for image in images):
            raise ValueError('Only PNG screenshots are accepted')
        with self._lock:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            runner = self.runner
            started = time.perf_counter()
            try:
                # Even the first decision gets a fresh thread; the startup thread
                # is used solely to validate native model/effort configuration.
                with (directory / 'thread_events.jsonl').open('w') as events:
                    thread, _ = runner._rpc('thread/start', {'model': runner.settings['model'], 'cwd': '/workspace',
                        'approvalPolicy': 'never', 'sandbox': 'read-only', 'ephemeral': True}, events,
                        time.monotonic() + 30, directory / 'thread_request.json')
                if thread.get('model') != runner.settings['model'] or thread.get('reasoningEffort') != runner.settings['effort']:
                    raise RuntimeError('Native model/effort mismatch')
                if thread.get('instructionSources'):
                    raise RuntimeError('Native thread loaded unexpected instruction sources')
                thread_ready=time.perf_counter()
                runner.thread_id = thread['thread']['id']
                attached = []
                for index, image in enumerate(images):
                    path = runner.workspace / 'observations' / f'{self.count:05d}_{index}.png'
                    path.write_bytes(image)
                    attached.append({'path': '/workspace/observations/' + path.name})
                action_start=time.perf_counter()
                answer, result = runner._request_action(prompt, attached, directory)
                meta = {'wall_seconds': time.perf_counter() - started, 'thread_prepare_seconds':thread_ready-started, 'image_prepare_seconds':action_start-thread_ready, 'action_rpc_seconds':time.perf_counter()-action_start, 'token_usage': result.get('tokenUsage'),
                    'image_sha256s': [hashlib.sha256(image).hexdigest() for image in images],
                    'output_schema_sha256': self.output_schema_hash, 'lean_native': self.lean, 'aggressive_native':self.aggressive,
                    'base_instructions_sha256': hashlib.sha256(LEAN_BASE_INSTRUCTIONS.encode()).hexdigest() if self.lean or self.aggressive else None,
                    'unexpected_tool_calls': 0, 'model': runner.settings['model'], 'effort': runner.settings['effort'],
                    'native_envelope_variant':'aggressive' if self.aggressive else 'lean' if self.lean else 'default'}
                if self.lean and not self.aggressive:
                    from .native_envelope import VERSION_ADAPTATIONS
                    meta['known_unsupported_legacy_settings']={'tools.view_image':VERSION_ADAPTATIONS['tools.view_image']}
                self.count += 1
                response = {'answer': answer, 'meta': meta}
                (directory / 'response.json').write_text(json.dumps(response) + '\n')
                return response
            except BaseException:
                # Authentication, rate limit, tool call or protocol failure never
                # converts into a direct API request.
                runner.close()
                raise

    def close(self):
        self.runner.close()


def validate_subscription_effort(effort="low"):
    if effort != "low":raise ValueError("Selected portable policy requires low effort")
    return effort
