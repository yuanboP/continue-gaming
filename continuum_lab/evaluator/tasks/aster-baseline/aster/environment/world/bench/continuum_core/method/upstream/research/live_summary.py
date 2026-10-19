"""Public decision summaries from the same response as executed controls."""
import copy
import json


def add_brief_schema(schema):
    if schema is None:
        raise ValueError('Decision brief requires structured output')
    schema = copy.deepcopy(schema)
    schema['properties']['brief'] = {'type': 'string', 'minLength': 1, 'maxLength': 60}
    schema['required'].append('brief')
    return schema


def validate_brief(answer):
    answer = json.loads(answer) if isinstance(answer, str) else answer
    brief = answer.get('brief') if isinstance(answer, dict) else None
    if not isinstance(brief, str) or not brief.strip() or len(brief) > 60:
        raise ValueError('Missing or invalid public decision summary')
    return brief


def brief_prompt(prompt, language="zh"):
    if language not in ("zh", "en"):raise ValueError("Unknown brief language")
    prompt = prompt.replace('No explanation.', '').replace('no explanation.', '')
    if language == "en":
        return prompt + ('\nAlso return "brief": one short English public decision summary, '
                         'about 6-10 words and at most 60 characters. Describe the visible '
                         'situation and chosen maneuver at a high level, e.g. '
                         '"sentinel drifts left; focus left and avoid center bullets." '
                         'Match YOUR plan in this response. This is a public summary, '
                         'not private reasoning or a step-by-step thought process. '
                         'Do not invent unseen events. Return only the JSON object.\n')
    return prompt + ('\nAlso return "brief": one short Chinese public decision summary, '
                     'preferably 15-30 characters, maximum 60. State the visible situation '
                     'and chosen maneuver at a high level (e.g. "辉夜向左漂移，低速左移维持命中。"). '
                     'This is a concise outward-facing summary, not private reasoning or '
                     'a step-by-step thought process. It must match YOUR plan in this same '
                     'response. Do not invent unseen events. Return only the JSON object.\n')


def install_panel(page, provider="subscription", language="zh"):
    if language not in ("zh", "en"):raise ValueError("Unknown panel language")
    if provider not in ("subscription", "api"):raise ValueError("Unknown panel provider")
    page.add_style_tag(content="""@font-face{font-family:ReplayCJK;src:url('/replay-cjk.otf')}
body{margin:0!important;background:#090810!important;color:#eee;font-family:ReplayCJK,sans-serif}
body>header,body>footer,main>:not(.game-shell){display:none!important}
.game-shell{position:fixed!important;left:24px!important;top:40px!important;width:960px!important;height:720px!important;font-size:21px!important}
.cutin-text em,.arena .human-gauge{font-family:ReplayCJK,serif!important}
#gpt-live{position:fixed;left:1020px;top:48px;width:388px;height:688px;box-sizing:border-box;padding:25px;background:#171322;border:1px solid #44344e;border-radius:16px;font-family:ReplayCJK,sans-serif}
#gpt-live small{color:#c69beb;letter-spacing:1px}#gpt-live h2{font-size:25px;margin:16px 0 12px}#gpt-live p{line-height:1.8;font-size:17px}
#gpt-brief{min-height:125px;font-size:25px!important;color:#fff}#gpt-keys{color:#e6c0ff;font-size:22px!important}
#gpt-clock,#gpt-meta,#gpt-status{color:#aaa1b9;font-size:14px!important}
#gpt-history{border-top:1px solid #44344e;padding-top:14px;white-space:pre-line;font-size:15px!important;color:#bdb5c9}
""")
    page.evaluate('''([provider,language]) => {
        const panel=document.createElement('aside');panel.id='gpt-live';
        panel.innerHTML='<small>GPT · LIVE / SUBSCRIPTION</small><h2>实时决策摘要</h2><p id="gpt-clock"></p><p id="gpt-brief">等待第一份模型响应…</p><p id="gpt-keys">尚未执行模型动作</p><p id="gpt-meta">游戏持续以 1× 运行</p><p id="gpt-status">摘要与动作来自同一份响应；非内部思维链。</p><p id="gpt-history"></p>';
        panel.querySelector('small').textContent='GPT · LIVE / '+provider.toUpperCase();
        window.gptPublicLanguage=language;
        if(language==='en'){
            panel.style.fontFamily='Arial,sans-serif';
            panel.querySelector('#gpt-brief').style.setProperty('font-size','23px','important');
            panel.querySelector('#gpt-brief').style.lineHeight='1.55';
            panel.querySelector('#gpt-history').style.lineHeight='1.5';
            panel.querySelector('h2').textContent='Live decision summary';
            panel.querySelector('#gpt-brief').textContent='Waiting for the first model response…';
            panel.querySelector('#gpt-keys').textContent='No model action applied yet';
            panel.querySelector('#gpt-meta').textContent='Game runs continuously at 1×';
            panel.querySelector('#gpt-status').textContent='Summary and action share one response. Public summary, not private reasoning.';
        }
        document.body.append(panel);window.gptPublicHistory=[];
        setInterval(()=>{const c=research.getClock();document.querySelector('#gpt-clock').textContent=`${language==='en'?'Game':'游戏'} ${(c.frame/60).toFixed(2)}s · ${c.status}`;},100);
    }''', [provider,language])
    page.evaluate('document.fonts.ready')


def apply_with_brief(page, plan, decision):
    validate_brief(decision)
    return page.evaluate('''([plan,d])=>{
        const c=research.apply(plan);
        const en=window.gptPublicLanguage==='en';
        const dirs=[en?'Stay':'停留','↑','↗','→','↘','↓','↙','←','↖'];
        const keys=plan.map(a=>`${dirs[a[0]]} ${a[1]?'Z ':''}${a[2]?'Shift ':''}${a[3]?'X ':''}· ${a[4]}${en?' frames':'帧'}`).join(' / ');
        document.querySelector('#gpt-brief').textContent=d.brief;
        document.querySelector('#gpt-keys').textContent=keys;
        document.querySelector('#gpt-meta').textContent=`${en?'Response':'响应'} #${d.seq} · ${en?'Obs → action':'观测→执行'} ${((c.visual_frame-d.observed_frame)/60).toFixed(2)}s`;
        window.gptPublicHistory.unshift(`${(c.frame/60).toFixed(1)}s  ${d.brief}`);
        document.querySelector('#gpt-history').textContent=window.gptPublicHistory.slice(1,4).join('\\n');
        return c;
    }''', [plan, decision])
