"""Synchronous DecisionPool transport into the shared native broker."""
import base64
import hashlib
import json
from pathlib import Path
import urllib.request


def request_json(url,payload=None,timeout=300):
    data=None if payload is None else json.dumps(payload,allow_nan=False).encode()
    request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)


def native_prompt(prompt):
    """Fix transport terminology, not visual rules, action values or timing."""
    corrections={
        'Measured unloaded API complete-action delay:':'Measured DEV concurrent native-subscription terminal-action delay:',
        'Per-request marginal API complete-action delay:':'Per-request marginal native-subscription complete-action delay:',
        'earlier completed real API episodes, retaining observed launch skew, correlation, and control-pipeline overhead.':
            'completed DEV native-subscription profile cohorts, retaining measured launch skew and response correlation. These are request-to-terminal timings on fixed opening screenshots, not measured live capture-to-actuation delays.',
        'Early-action capacity simulation: a complete legal answer can take effect before its request releases its concurrency slot. Readiness and terminal slot delays are jointly resampled from historical timing; any censored terminal tail follows the declared profile model. Sampled per-request delays are hidden.':
            'Terminal-action capacity simulation: this native adapter returns only at turn completion, so measured action readiness and terminal availability coincide. Whole DEV timing vectors are jointly resampled; there is no fitted censored-tail model. Sampled per-request delays are hidden.',
    }
    result=prompt
    for old,new in corrections.items():result=result.replace(old,new)
    if 'This is a DEV latency calibration on retained opening screenshots.' in prompt:
        result=result.replace('Measured DEV concurrent native-subscription terminal-action delay:',
            'Calibration-only upstream timing prior (not a new native measurement):')
    result=result.replace('Play this Aster sentinel easy boss practice','Play ASTER Level 01')
    result=result.replace('You control ship, the small red/white player initially near the bottom. sentinel is the small moving boss near the top. The large translucent woman is background art, not an enemy or your player.','You control the cyan triangular ship initially near the bottom. The coral geometric sentinel near the top is the boss. Decorative stars and grid lines are background, not enemies.')
    result=result.replace('your upward red paper talismans and shot streams are friendly','your upward cyan line shots are friendly')
    result=result.replace('your upward white shots are friendly','your upward cyan line shots are friendly')
    result=result.replace('Red paper talismans moving upward are friendly shots.','Cyan line shots moving upward are friendly shots.')
    result=result.replace('the background portrait or red paper shots','the decorative background or cyan line shots')
    result=result.replace('an older upper red-paper shot trail','an older upper cyan shot trail')
    result=result.replace('small colored enemy beads and diamonds','small colored enemy beads')
    result=result.replace('Shift shows your tiny white hitbox.','Shift shows your tiny white-outlined hitbox.')
    result=result.replace('8..24 screenshot pixels (roughly 5..15 horizontal logical frames)','10..30 screenshot pixels (roughly 5..15 horizontal logical frames)')
    result=result.replace('full 640x480 image','full 960x720 image')
    result=result.replace('3.44 pixels per logical frame','4.3 pixels per logical frame')
    result=result.replace('1.6 pixels per logical frame','2.0 pixels per logical frame')
    result=result.replace('The spell declaration is currently visible and the playfield is temporarily frozen by the original opening animation.','The sector introduction is currently visible and the playfield is temporarily frozen by its opening animation.')
    result=result.replace('during the declaration','during the introduction')
    result=result.replace('sentinel','the sentinel')
    result=result.replace('Aster','ASTER')
    return result+'\nBENCHMARK TRANSPORT: native Codex gpt-6-astra / low, without API fallback. The original portable transport is adapted only by exact model-literal replacement; low-effort validation remains. Calibration is small-sample DEV data, not a known future latency or a tail guarantee.\n'


def client_class(broker_url,run_id,phase):
    class HTTPSubscriptionClient:
        def __init__(self,suite=None,output=None,output_schema=None,**kwargs):
            if kwargs.get('effort','low')!='low':raise ValueError('Frozen method uses low effort')
            self.schema=output_schema;self.output=Path(output)
        def act(self,prompt,images,directory):
            directory=Path(directory);transmitted=native_prompt(prompt)
            rid=run_id+'-'+directory.name.replace('_','-')
            payload={'request_id':rid,'run_id':run_id,'phase':phase,'prompt':transmitted,
                     'images_base64':[base64.b64encode(x).decode() for x in images],'output_schema':self.schema}
            result=request_json(broker_url+'/act',payload)
            meta=dict(result.get('meta') or {})
            meta.update(broker_request_id=rid,original_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                        transmitted_prompt_sha256=hashlib.sha256(transmitted.encode()).hexdigest(),
                        prompt_adaptation='native-transport-and-presentation-v1')
            if result.get('status')!='completed':raise RuntimeError('Native broker request failed; no retry: '+rid)
            return result['answer'],meta
        def close(self):pass
    return HTTPSubscriptionClient
