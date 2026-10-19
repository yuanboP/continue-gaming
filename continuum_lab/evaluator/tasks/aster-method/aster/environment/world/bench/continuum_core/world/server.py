"""Private browser world. Importing this module never starts a browser/episode."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import importlib
import io
import mimetypes
import os
from pathlib import Path, PurePosixPath
import time
import uuid
import zipfile

from .common import (ENGINE_SHA256, SCHEMA, canonical_json, contract_from_config,
                     public_clock, sign, strict_json, validate_identifier,
                     validate_plan, validate_seed, validate_controller_manifest)


class WorldError(ValueError):
    pass


class AsterWorld:
    def __init__(self, config: dict, key: bytes, upstream_root: Path):
        self.config = dict(config)
        self.config.setdefault("role", "dev" if config.get("dev") else "live")
        self.dev = self.config["role"] == "dev"
        self.config.setdefault("live_seed", config.get("seed"))
        self.config.setdefault("dev_seed", config.get("seed", 1) if self.dev else 1)
        self.seed = validate_seed(self.config["dev_seed"] if self.dev else self.config["live_seed"])
        self.config.setdefault("broker_url", "http://main:8090")
        self.config.setdefault("dev_url", "http://dev:8080")
        self.root = Path(config.get("artifact_root", "/tmp/world-audit"))
        self.config.setdefault("run_root", str(self.root / "method"))
        if not isinstance(config.get("task_id"), str) or not config["task_id"]:
            raise ValueError("Private task_id is required")
        if len(key) < 32:
            raise ValueError("Signing key too short")
        self.key = key
        self.dist = (upstream_root / "environments/aster/dist").resolve()
        self.contract = contract_from_config(config)
        self.lock = asyncio.Lock()
        self.begin_count = 0
        self.episode_id = None
        self.run_kind = None
        self.request_ids = set()
        self.captures = []
        self.events = []
        self.episodes = []
        self.public_artifacts = {}
        self.envelope = None
        self.audit = None
        self.ready = False
        self.page = None
        self.browser = None
        self.context = None
        self.playwright = None
        self.static_runner = None
        self.monitor_task = None
        self.method_runs = {}
        self.active_method = None
        self.sources = {}
        self.controller_commitment = None
        self.method_bundle_binding = None
        self.expected_controller_manifest = (validate_controller_manifest(config["expected_controller_manifest"])
                                            if config.get("expected_controller_manifest") is not None else None)
        self.video = None
        self.video_path = None
        self.context_closed = False
        self.recording_clock = {"kind":"approximate_host_anchor_not_video_pts",
            "note":"Host context/page creation brackets only; not a frame-accurate video/media time origin.",
            "width":960,"height":720}

    async def start(self):
        from aiohttp import web
        from playwright.async_api import async_playwright

        engine = self.dist / "engine.mjs"
        if hashlib.sha256(engine.read_bytes()).hexdigest() != ENGINE_SHA256:
            raise WorldError("Original engine hash mismatch")
        if self.config.get("engine_sha256", ENGINE_SHA256) != ENGINE_SHA256:
            raise WorldError("Configured engine hash mismatch")
        # A complete source identity, including the actual renderer and assets.
        for path in sorted(self.dist.rglob("*")):
            if path.is_file():
                if path.is_symlink() or not path.resolve().is_relative_to(self.dist):
                    raise WorldError("Symlink in private game distribution")
                self.sources[path.relative_to(self.dist).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ("bootstrap.mjs", "instrument.mjs"):
            self.sources["bench/" + name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        app = web.Application()
        app.router.add_get("/{path:.*}", self._static)
        self.static_runner = web.AppRunner(app, access_log=None)
        await self.static_runner.setup()
        site = web.TCPSite(self.static_runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True, executable_path=self.config.get("browser_executable"),
            args=["--no-sandbox", "--disable-background-timer-throttling",
                  "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"])
        self.recording_clock["context_creation_started_monotonic"] = time.monotonic()
        self.recording_clock["context_creation_started_utc"] = datetime.now(timezone.utc).isoformat()
        self.context = await self.browser.new_context(viewport={"width":960,"height":720}, device_scale_factor=1,
            record_video_dir=str(self.root / "native-video"), record_video_size={"width":960,"height":720})
        self.recording_clock["context_created_monotonic"] = time.monotonic()
        self.recording_clock["context_created_utc"] = datetime.now(timezone.utc).isoformat()
        self.recording_clock["page_creation_started_monotonic"] = time.monotonic()
        self.page = await self.context.new_page()
        self.recording_clock["page_created_monotonic"] = time.monotonic()
        self.recording_clock["page_created_utc"] = datetime.now(timezone.utc).isoformat()
        self.recording_clock["recording_start_monotonic_approx"] = (
            self.recording_clock["page_creation_started_monotonic"] + self.recording_clock["page_created_monotonic"]) / 2
        self.video = self.page.video
        # No navigation or resource fetch outside the private local game server.
        async def restrict(route):
            if route.request.url.startswith(f"http://127.0.0.1:{port}/"):
                await route.continue_()
            else:
                await route.abort()
        await self.page.route("**/*", restrict)
        await self.page.goto(f"http://127.0.0.1:{port}/?research=1", wait_until="domcontentloaded", timeout=120000)
        await self.page.wait_for_function("window.__benchReady && window.research?.ready()", timeout=120000)
        self.ready = True
        self.monitor_task = asyncio.create_task(self._monitor())

    async def _static(self, request):
        from aiohttp import web
        rel = request.match_info["path"] or "index.html"
        if rel == "bench-bootstrap.mjs":
            return web.Response(body=Path(__file__).with_name("bootstrap.mjs").read_bytes(), content_type="application/javascript")
        if rel == "bench-instrument.mjs":
            return web.Response(body=Path(__file__).with_name("instrument.mjs").read_bytes(), content_type="application/javascript")
        path = (self.dist / rel).resolve()
        if not path.is_relative_to(self.dist) or not path.is_file():
            raise web.HTTPNotFound()
        if rel == "index.html":
            source = path.read_text()
            old = '<script type="module" src="app.mjs"></script>'
            if source.count(old) != 1:
                raise WorldError("Native HTML bootstrap marker changed")
            return web.Response(text=source.replace(old, '<script type="module" src="bench-bootstrap.mjs"></script>'), content_type="text/html")
        return web.FileResponse(path)

    async def reset(self, seed=None, mode="realtime", speed=1, run_kind="live"):
        if type(speed) not in (int, float) or speed != 1:
            raise WorldError("Only native speed 1 is permitted")
        if mode not in ("sim", "realtime"):
            raise WorldError("Invalid mode")
        if not self.dev and (mode != "realtime" or run_kind != "live"):
            raise WorldError("Live world allows one realtime live episode only")
        selected_seed = self.seed if seed is None else validate_seed(seed)
        if not self.dev and selected_seed != self.seed:
            raise WorldError("Live seed is fixed privately")
        async with self.lock:
            if not self.ready:
                raise WorldError("World not ready")
            if self.context_closed:
                raise WorldError("Video finalized; this browser cannot start another episode")
            if not self.dev and self.config.get("require_controller_commitment", False) and self.controller_commitment is None:
                raise WorldError("Freeze the controller manifest before live begin")
            maximum = int(self.config.get("max_dev_resets", 3)) if self.dev else 1
            if self.begin_count >= maximum:
                raise WorldError("Episode reset quota exhausted")
            if self.episode_id:
                if self.envelope is None:
                    await self.page.evaluate("__benchAudit.seal('development_reset')")
                    await self._freeze_locked()
                self.episodes.append({"episode_id":self.episode_id,
                                      "artifact_sha256":hashlib.sha256(canonical_json(self.envelope)).hexdigest()})
            self.begin_count += 1
            self.seed = selected_seed
            self.episode_id = uuid.uuid4().hex
            self.run_kind = run_kind
            self.mode = mode
            self.episode_dir = self.root / self.episode_id
            (self.episode_dir / "captures").mkdir(parents=True, exist_ok=False)
            self.envelope = self.audit = None
            self.captures = []
            self.events = []
            # Safe coordinator artifacts persist across DEV profile/practice
            # resets so no failed calibration or earlier model call disappears.
            self.request_ids = set()
            self.started_monotonic = time.monotonic()
            self.started_utc = datetime.now(timezone.utc).isoformat()
            result = await self.page.evaluate("""args => {
                __benchAudit.begin(args.run_kind);
                const c=research.reset(args.seed,args.mode,1);
                __benchAudit.initialized(); return c;
            }""", {"seed":selected_seed,"mode":mode,"run_kind":run_kind})
            self._event("begin", {"episode_id":self.episode_id,"clock":public_clock(result)})
            return public_clock(result)

    async def freeze_controller(self, manifest):
        manifest = validate_controller_manifest(manifest)
        async with self.lock:
            if self.dev:
                raise WorldError("Controller commitment belongs to the live world")
            if self.begin_count:
                raise WorldError("Controller must be committed before begin")
            if self.expected_controller_manifest is not None and manifest != self.expected_controller_manifest:
                raise WorldError("Controller differs from the registered holdout manifest")
            if self.controller_commitment is not None:
                if manifest != self.controller_commitment["manifest"]:
                    raise WorldError("Controller commitment is immutable")
                return self.controller_commitment
            clock = public_clock(await self.page.evaluate("research.getClock()"))
            self.controller_commitment = {"schema_version":"native-controller-commitment-v1",
                "manifest":manifest,"manifest_sha256":hashlib.sha256(canonical_json(manifest)).hexdigest(),
                "received_utc":datetime.now(timezone.utc).isoformat(),
                "received_monotonic":time.monotonic(),"public_clock":clock}
            return self.controller_commitment

    async def bind_method_bundle(self, bundle):
        """Trusted coordinator-only proof of the exact validated live bundle."""
        if not isinstance(bundle, dict) or not isinstance(bundle.get("freeze_id"), str):
            raise WorldError("Invalid method freeze bundle identity")
        freeze_id = bundle["freeze_id"]
        if len(freeze_id) != 64 or any(c not in "0123456789abcdef" for c in freeze_id):
            raise WorldError("Invalid method freeze ID")
        raw = canonical_json(bundle)
        if len(raw) > 1024*1024:
            raise WorldError("Method bundle exceeds 1MiB")
        binding = {"freeze_id":freeze_id,"canonical_sha256":hashlib.sha256(raw).hexdigest()}
        async with self.lock:
            if self.dev or self.config.get("arm", self.config.get("mode")) != "method" or self.begin_count:
                raise WorldError("Method bundle binding is live-method-only before begin")
            expected = self.config.get("expected_method_bundle_sha256")
            if expected is not None and binding["canonical_sha256"] != expected:
                raise WorldError("Method bundle differs from registered canonical hash")
            if self.method_bundle_binding is not None and self.method_bundle_binding != binding:
                raise WorldError("Method bundle binding is immutable")
            self.method_bundle_binding = binding
            return binding

    def _event(self, kind, fields):
        event = {"event":kind,"index":len(self.events),"server_monotonic":time.monotonic(),**fields}
        self.events.append(event)
        if self.episode_id:
            with (self.episode_dir / "public-events.jsonl").open("ab") as f:
                f.write(canonical_json(event) + b"\n")

    async def clock(self):
        async with self.lock:
            if self.audit is not None and self.audit.get("final_clock"):
                return public_clock(self.audit["final_clock"])
            return public_clock(await self.page.evaluate("research.getClock()"))

    async def capture(self):
        async with self.lock:
            if self.context_closed:
                raise WorldError("Native video finalized; take final captures before requesting video")
            if len(self.captures) >= int(self.config.get("max_captures", 1500)):
                raise WorldError("Capture quota exhausted; no silent image omission")
            result = await self.page.evaluate("research.capture()")
            data = base64.b64decode(result["png"].split(",", 1)[1], validate=True)
            digest = hashlib.sha256(data).hexdigest()
            image_id = f"{len(self.captures):06d}-{digest[:16]}"
            record = {"image_id":image_id,"sha256":digest,"bytes":len(data),
                      "frame":result["frame"],"held":result["held"],"clock":public_clock(result["clock"])}
            # Ready captures are not episode input and have their own non-secret store.
            directory = self.episode_dir if self.episode_id else self.root / "ready"
            (directory / "captures").mkdir(parents=True, exist_ok=True)
            (directory / "captures" / f"{image_id}.png").write_bytes(data)
            self.captures.append(record)
            self._event("capture", record)
            await self._freeze_locked()
            self._refresh_export_locked()
            return {"png":data,"frame":result["frame"],"held":result["held"],
                    "clock":public_clock(result["clock"]),"image_id":image_id,"image_sha256":digest}

    async def apply(self, plan, decision=None, *, request_id=None):
        validate_plan(plan)
        request_id = validate_identifier(request_id or uuid.uuid4().hex)
        async with self.lock:
            if not self.episode_id:
                raise WorldError("Call begin before act")
            await self._freeze_locked()
            if self.envelope is not None:
                raise WorldError("Episode already ended")
            if request_id in self.request_ids:
                raise WorldError("Duplicate action request identity")
            self.request_ids.add(request_id)
            result = await self.page.evaluate("""args => {
                __benchAudit.request_id=args.id;
                try { return research.apply(args.plan); }
                finally { __benchAudit.request_id=null; }
            }""", {"id":request_id,"plan":plan})
            # The upstream runner supplies only its visible brief/sequence here.
            # Preserve the causal link without changing the native plan itself.
            visible_decision = ({key:decision[key] for key in ("brief","seq","observed_frame") if key in decision}
                                if isinstance(decision, dict) else None)
            if len(canonical_json(visible_decision)) > 16384:
                visible_decision = {"omitted":"oversize_visible_metadata"}
            self._event("apply", {"request_id":request_id,"plan":plan,"clock":public_clock(result),
                                  "decision":visible_decision})
            return public_clock(result)

    async def advance(self, frames):
        if not self.dev or not self.episode_id or self.mode != "sim":
            raise WorldError("Manual advance is restricted to DEV simulation")
        if type(frames) is not int or not 0 <= frames <= 10000:
            raise WorldError("Invalid simulation frame count")
        async with self.lock:
            result = await self.page.evaluate("frames=>research.step(frames)", frames)
            self._event("advance", {"frames":frames,"clock":public_clock(result)})
            await self._freeze_locked()
            return public_clock(result)

    async def diagnostics(self):
        # Python-only trusted runner; never exposed as an HTTP route.
        async with self.lock:
            if self.context_closed and self.audit is not None:
                return {"clock":self.audit["final_clock"],"state":self.audit["final"]}
            return await self.page.evaluate("research.diagnostics()")

    async def _freeze_locked(self):
        if not self.episode_id or self.envelope is not None:
            return
        terminal = await self.page.evaluate("""() => {
            __benchAudit.sealIfTerminal(); return __benchAudit.sealed;
        }""")
        if not terminal:
            return
        self.audit = await self.page.evaluate("__benchAudit.snapshot()")
        payload = {"schema_version":SCHEMA,"task_id":self.config["task_id"],
                   "seed":self.seed,"mode":"dev" if self.dev else "live",
                   "episode_id":self.episode_id,"run_kind":self.run_kind,
                   "controller_commitment":self.controller_commitment,
                   "method_bundle_binding":self.method_bundle_binding,
                   "contract":self.contract,"source_sha256":self.sources,
                   "started_utc":self.started_utc,"started_monotonic":self.started_monotonic,
                   "exported_utc":datetime.now(timezone.utc).isoformat(),
                   "recording_clock":{**self.recording_clock,
                       "episode_reset_started_monotonic":self.started_monotonic,
                       "episode_begin_video_offset_seconds_approx":self.started_monotonic-self.recording_clock.get(
                           "recording_start_monotonic_approx", self.started_monotonic)},
                   "audit":self.audit,"captures":list(self.captures),
                   "gateway_events":list(self.events),"prior_dev_episodes":list(self.episodes)}
        self.envelope = sign(payload, self.key)
        (self.episode_dir / "session.json").write_bytes(canonical_json(self.envelope) + b"\n")

    def _refresh_export_locked(self):
        # Terminal physics/clock never change. Captures obtained after the
        # terminal frame are still retained and authenticated as such.
        if self.envelope is not None:
            payload = dict(self.envelope["payload"])
            payload["captures"] = list(self.captures)
            payload["gateway_events"] = list(self.events)
            self.envelope = sign(payload, self.key)
            (self.episode_dir / "session.json").write_bytes(canonical_json(self.envelope)+b"\n")

    async def finish(self):
        async with self.lock:
            await self._freeze_locked()
            return self.envelope

    async def abort(self, reason):
        async with self.lock:
            if self.episode_id and self.envelope is None:
                await self.page.evaluate("reason=>__benchAudit.seal(reason)", str(reason)[:200])
                await self._freeze_locked()

    async def _monitor(self):
        while True:
            await asyncio.sleep(.1)
            async with self.lock:
                if self.episode_id and self.envelope is None:
                    if time.monotonic() - self.started_monotonic > float(self.config.get("max_episode_wall_seconds", 180)):
                        await self.page.evaluate("__benchAudit.seal('wall_watchdog_exceeded')")
                    await self._freeze_locked()

    def register_public_artifact(self, relative_path, data):
        rel = PurePosixPath(relative_path)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "method":
            raise WorldError("Public artifact must be beneath method/")
        if rel.suffix not in (".json", ".jsonl", ".txt", ".md", ".png", ".py", ".mjs", ".js"):
            raise WorldError("Unsupported public artifact suffix")
        if any(word in str(rel).lower() for word in ("auth", "secret", "signing", "reasoning", "diagnostic")):
            raise WorldError("Sensitive artifact name rejected")
        raw = data.encode() if isinstance(data, str) else bytes(data)
        if len(raw) > 32*1024*1024 or sum(len(v) for v in self.public_artifacts.values()) + len(raw) > 256*1024*1024:
            raise WorldError("Public method artifact quota exceeded")
        name = rel.as_posix()
        if name in self.public_artifacts and self.public_artifacts[name] != raw:
            raise WorldError("Public artifact overwrite rejected")
        self.public_artifacts[name] = raw

    async def trace(self):
        async with self.lock:
            await self._freeze_locked()
            if self.envelope is None:
                raise WorldError("Audit export is available only after episode end")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("session.json", canonical_json(self.envelope) + b"\n")
                event_file = self.episode_dir / "public-events.jsonl"
                if event_file.exists():
                    z.write(event_file, "public-events.jsonl")
                for record in self.captures:
                    name = f"captures/{record['image_id']}.png"
                    z.write(self.episode_dir / name, name)
                for name, value in sorted(self.public_artifacts.items()):
                    z.writestr(name, value)
                # Preserve earlier DEV profile/practice evidence too. Only
                # explicit session/log/capture names, never arbitrary files.
                for previous in self.episodes:
                    ident = validate_identifier(previous["episode_id"])
                    directory = self.root / ident
                    for name in ("session.json", "public-events.jsonl"):
                        path = directory / name
                        if path.is_file():
                            z.write(path, f"prior-dev/{ident}/{name}")
                    for path in sorted((directory / "captures").glob("*.png")):
                        if path.is_file() and not path.is_symlink():
                            z.write(path, f"prior-dev/{ident}/captures/{path.name}")
            return buffer.getvalue()

    async def finish_video(self):
        async with self.lock:
            await self._freeze_locked()
            if self.envelope is None:
                raise WorldError("Video finalization is allowed only after episode end")
            if self.active_method and not self.active_method.done():
                raise WorldError("Wait for coordinator completion before finalizing video")
            if self.video_path is None:
                await self.context.close()
                self.context_closed = True
                self.video_path = Path(await self.video.path())
            return self.video_path

    async def close(self):
        if self.monitor_task:
            self.monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor_task
        if self.context and not self.context_closed:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        if self.static_runner:
            await self.static_runner.cleanup()


def make_gateway(world):
    from aiohttp import web

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({"error":type(exc).__name__,"message":str(exc)}, status=409)

    app = web.Application(client_max_size=2*1024*1024, middlewares=[errors])

    async def body(request):
        result = strict_json(await request.read() or b"{}")
        if not isinstance(result, dict):
            raise WorldError("Expected JSON object")
        return result

    async def health(request):
        return web.json_response({"ready":world.ready,"schema_version":SCHEMA,
                                  "role":"dev" if world.dev else "live"}, status=200 if world.ready else 503)

    async def begin(request):
        data = await body(request)
        if data:
            raise WorldError("Begin has no configurable live parameters")
        result = await world.reset(run_kind="dev" if world.dev else "live")
        return web.json_response({"episode_id":world.episode_id,"clock":result})

    async def reset(request):
        if not world.dev:
            raise WorldError("Live reset is forbidden")
        data = await body(request)
        if set(data) - {"seed", "mode", "speed"}:
            raise WorldError("Unknown DEV reset field")
        result = await world.reset(data.get("seed"), data.get("mode", "realtime"), data.get("speed", 1), "dev")
        return web.json_response({"episode_id":world.episode_id,"clock":result})

    async def freeze_controller(request):
        data = await body(request)
        if set(data) != {"manifest"}:
            raise WorldError("Expected only controller manifest")
        return web.json_response(await world.freeze_controller(data["manifest"]))

    async def advance(request):
        data = await body(request)
        frames = data.get("frames")
        if set(data) != {"frames"} or type(frames) is not int or not 1 <= frames <= 600:
            raise WorldError("Expected DEV frames integer 1..600")
        return web.json_response({"clock":await world.advance(frames)})

    async def observe(request):
        result = await world.capture()
        result["image_base64"] = base64.b64encode(result.pop("png")).decode()
        result["status"] = "ready" if not world.episode_id else result["clock"]["status"]
        return web.json_response(result)

    async def act(request):
        data = await body(request)
        if set(data) - {"plan", "id"}:
            raise WorldError("Unknown action field")
        identifier = validate_identifier(data.get("id") or uuid.uuid4().hex)
        result = await world.apply(data["plan"], request_id=identifier)
        return web.json_response({"id":identifier,"clock":result})

    async def status(request):
        clock = await world.clock()
        return web.json_response({"status":"ready" if not world.episode_id else clock["status"],
                                  "episode_id":world.episode_id,"clock":clock,
                                  "ended":world.envelope is not None,"terminal":world.envelope is not None,
                                  "reset_count":world.begin_count})

    async def export(request):
        result = await world.finish()
        if result is None:
            raise WorldError("Export denied before episode end")
        return web.Response(body=canonical_json(result)+b"\n", content_type="application/json")

    async def trace(request):
        return web.Response(body=await world.trace(), content_type="application/zip")

    async def image(request):
        image_id = validate_identifier(request.match_info["image_id"])
        if image_id not in {x["image_id"] for x in world.captures}:
            raise web.HTTPNotFound()
        directory = world.episode_dir if world.episode_id else world.root / "ready"
        return web.FileResponse(directory / "captures" / f"{image_id}.png")

    async def video(request):
        return web.FileResponse(await world.finish_video(), headers={"Content-Type":"video/webm"})

    async def continuum_run(request):
        if world.config.get("arm", world.config.get("mode")) != "method":
            raise WorldError("Workflow coordinator is available only in the method arm")
        data = await body(request)
        if (data.get("operation") == "live" and world.config.get("require_controller_commitment", False)
                and world.controller_commitment is None):
            raise WorldError("Freeze the controller manifest before dispatching live workflow")
        if world.active_method and not world.active_method.done():
            raise WorldError("A coordinator run is already active")
        run_id = validate_identifier(data.get("run_id") or uuid.uuid4().hex)
        if run_id in world.method_runs:
            raise WorldError("Coordinator run ID already used")
        module = importlib.import_module("continuum_core.method.coordinator")
        world.method_runs[run_id] = {"run_id":run_id,"status":"running"}
        async def perform():
            try:
                result = await module.run(world, {**data,"run_id":run_id})
                canonical_json(result)
                world.method_runs[run_id] = {"run_id":run_id,"status":"completed","result":result}
            except Exception as exc:
                world.method_runs[run_id] = {"run_id":run_id,"status":"failed","error_type":type(exc).__name__,"message":str(exc)}
        world.active_method = asyncio.create_task(perform())
        return web.json_response(world.method_runs[run_id], status=202)

    async def continuum_status(request):
        run_id = validate_identifier(request.query.get("id", ""))
        if run_id not in world.method_runs:
            raise web.HTTPNotFound()
        return web.json_response(world.method_runs[run_id])

    for path, handler in (("/health",health),("/observe",observe),("/status",status),
                          ("/export",export),("/trace",trace),("/video",video),("/workflow/status",continuum_status)):
        app.router.add_get(path, handler)
    for path, handler in (("/begin",begin),("/reset",reset),("/freeze_controller",freeze_controller),
                          ("/advance",advance),("/act",act),("/workflow/run",continuum_run)):
        app.router.add_post(path, handler)
    app.router.add_get("/images/{image_id}.png", image)
    return app


async def serve():
    from aiohttp import web
    config = strict_json(Path(os.environ.get("GAME_CONFIG", "/private/config.json")).read_bytes())
    key = Path(os.environ.get("SIGNING_KEY_FILE", "/private/signing.key")).read_bytes()
    world = AsterWorld(config, key, Path(os.environ.get("GAME_ASSET_ROOT", "/opt/game")))
    await world.start()
    runner = web.AppRunner(make_gateway(world), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await world.close()


if __name__ == "__main__":
    asyncio.run(serve())
