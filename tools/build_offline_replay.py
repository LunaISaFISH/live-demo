"""Package the exact live UI with recorded HTTP responses and SSE events."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def validate(r):
    if not r.get('complete') or len(r.get('steps',[]))!=9:
        raise ValueError('A complete recorded workflow is required')
    if [s['type'] for s in r['steps']] != ['turn']*6+['save','signal','turn']:
        raise ValueError('Missing human save or monitoring step')
    for s in r['steps']:
        if s['type']!='save' and (not any(e['kind']=='reply' for e in s['events']) or s['events'][-1]['kind']!='done' or any(e['kind']=='error' for e in s['events'])):
            raise ValueError('Incomplete event stream')
def render(r):
    validate(r)
    web=ROOT/'prototype/web'
    html=(web/'index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+(web/'style.css').read_text()+'</style>')
    banner='''<section id="replay-banner" style="padding:12px 24px;background:#f1e5bc;border-bottom:1px solid #b4a263;display:flex;gap:12px;align-items:center;flex-wrap:wrap"><strong>Recorded replay · 原版界面回放</strong><span>真实录制 · 加速播放 · 不调用模型</span><label>跳至步骤 <select id="replay-step"></select></label><button id="replay-restart">重新开始</button><span id="replay-hint" role="status"></span></section>'''
    html=html.replace('<body>','<body>'+banner)
    data=json.dumps(r,ensure_ascii=False).replace('<','\\u003c')
    scripts='<script>window.RECORDING='+data+';</script><script>'+(ROOT/'tools/replay-adapter.js').read_text()+'</script><script>'+(web/'app.js').read_text()+'</script><script>'+(ROOT/'tools/replay-controls.js').read_text()+'</script>'
    return html.replace('<script src="/app.js"></script>',scripts)
if __name__=='__main__':
    r=json.loads((ROOT/'evidence/local-demo-2026-10-22/ui-recording.json').read_text())
    (ROOT/'Offline Demo.html').write_text(render(r))
    print('Built: exact live HTML/CSS/JS, nine recorded steps, embedded responses, zero network fetches.')
