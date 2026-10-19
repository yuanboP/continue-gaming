"""Original frontend visual environment; no policy or physics changes."""
from __future__ import annotations
import base64
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class BrowserEnv:
    def __init__(self, url: str, browser=None, *, headless=True, record_video_dir=None, decision_panel=False, panel_provider="subscription", panel_language="zh", browser_executable=None):
        from playwright.sync_api import sync_playwright
        self._playwright = None
        self._owns_browser = browser is None
        if browser is None:
            self._playwright = sync_playwright().start()
            browser = self._playwright.chromium.launch(headless=headless, executable_path=browser_executable, args=[
                '--no-sandbox', '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'])
        self.browser = browser
        size={'width':1440,'height':800} if decision_panel else {'width':900,'height':720}
        options = dict(viewport=size, device_scale_factor=1)
        if record_video_dir:
            options.update(record_video_dir=str(record_video_dir), record_video_size=size)
        self.context = browser.new_context(**options)
        self.page = self.context.new_page()
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query)); query['research'] = '1'
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        self.page.goto(target, wait_until='domcontentloaded', timeout=120000)
        self.page.wait_for_function('window.research?.ready()', timeout=120000)
        if decision_panel:
            from .live_summary import install_panel
            install_panel(self.page, panel_provider, panel_language)
        self.mode = 'sim'

    def reset(self, seed=1, mode='sim', speed=1):
        result = self.page.evaluate('([seed,mode,speed]) => research.reset(seed,mode,speed)', [seed, mode, speed])
        self.mode = mode
        return result

    def apply(self, plan, decision=None):
        """Replace pending chunks [direction,Z,Shift,X,logical_frames].

        Directions: 0 idle, 1 up, 2 NE, 3 right, 4 SE, 5 down, 6 SW,
        7 left, 8 NW. Expiry releases movement/X; Z/Shift persist.
        X uses original held edges: release X between presses.
        """
        if decision is not None:
            from .live_summary import apply_with_brief
            return apply_with_brief(self.page,plan,decision)
        return self.page.evaluate('(plan) => research.apply(plan)', plan)

    def advance(self, frames):
        """Advance visual frames including cinematic pauses; simulation only."""
        return self.page.evaluate('(frames) => research.step(frames)', frames)

    def advance_to(self, frame):
        if self.mode != 'sim':
            raise ValueError('advance_to is simulation-only')
        return self.page.evaluate('''target=>{
            let clock=research.getClock();
            if(target<clock.frame)throw new Error('Cannot reverse clock');
            while(clock.frame<target&&clock.status==='playing')
                clock=research.step(Math.min(10000,Math.max(1,target-clock.frame+clock.intro)));
            return clock;
        }''', frame)

    def capture(self):
        """Atomic native canvas/HUD copy with matching frame metadata."""
        result = self.page.evaluate('research.capture()')
        result['png'] = base64.b64decode(result['png'].split(',', 1)[1])
        return result

    def clock(self):
        return self.page.evaluate('research.getClock()')

    def diagnostics(self):
        """Privileged state for offline analysis, never pass to player model."""
        return self.page.evaluate('research.diagnostics()')

    def close(self):
        self.context.close()
        if self._owns_browser:
            self.browser.close()
            self._playwright.stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
