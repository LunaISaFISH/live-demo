"""Record real public HTTP/SSE UI responses from an isolated FitCoach server."""
import json, os, pathlib, sys, urllib.parse
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'prototype'))
import e2e
OUT=ROOT/'evidence/local-demo-2026-10-22/ui-recording.json'
DAY=e2e.SIM_DATE; PID=e2e.PID
q=f'?pid={PID}&as_of={DAY}'
def get(route): return e2e.get(route)
def snapshot():
    d={path:get(path+q) for path in ['/api/profile','/api/plan','/api/memory','/api/signals','/api/transcript','/api/audit','/api/audit/verify']}
    # Only fields consumed by the public UI; never package raw model execution.
    for row in d['/api/audit']:
        for key in ['execution','data_results']: row.pop(key,None)
    return d

def main():
    e2e.post('/api/reset',{'pid':PID})
    e2e.post('/api/clock',{'pid':PID,'as_of':DAY})
    base={p:get(p) for p in ['/api/about','/api/participants','/api/evidence']}
    base['/api/participants']=[p for p in base['/api/participants'] if p['pid']==PID]
    base['/api/snapshot']=get('/api/snapshot'+q)
    base['/api/tests']=json.loads((ROOT/'evidence/local-demo-2026-10-22/e2e-tests.json').read_text())
    base['series']={k:get('/api/monitor-series'+q+'&days=42&kind='+k) for k in base['/api/about']['monitor_metrics']}
    base['inspect']={d:get('/api/inspect'+q+'&start=2020-02-03&end='+DAY+'&domain='+d) for d in ['training','sleep','recovery','physiology','lifestyle','injury']}
    recording={'version':1,'pid':PID,'day':DAY,'base':base,'initial':snapshot(),'steps':[]}
    prompts=[('Check in','checkin',"I'm exhausted and have had no energy lately. Am I training too much?"),
      ('Clarify context','checkin',"It started when I moved house last weekend and carried a lot of heavy things that weren't recorded. I sleep about as long as usual but still wake up with no energy, and have no pain or other discomfort."),
      ('Remember preference','plan','I hate swimming, never suggest it'),
      ('Ask before planning','plan','plan my next month'),
      ('Draft the week','plan',"My goal is to rebuild endurance. I have no current pain. I can train Monday, Wednesday and Friday for up to 45 minutes, have walking and running shoes, and dislike swimming. I've felt fine lately and nothing has changed. Plan my next week."),
      ('Revise the draft','plan','Replace the current plan with exactly one training day: Friday, 10 minutes of easy Walking, with no other day entries.')]
    def save():OUT.write_text(json.dumps(recording,ensure_ascii=False))
    def record(title,mode,message=None,signal=None):
        print('Recording:',title,flush=True)
        events,reply=e2e.turn(message,signal=signal)
        if not reply or reply['summary_status']!='accepted' or any(e['kind']=='error' for e in events):
            raise RuntimeError(f'Failed recording: {title}')
        recording['steps'].append(dict(title=title,mode=mode,type='signal' if signal else 'turn',message=message,signal=signal,events=events,after=snapshot()))
        save()
    for title,mode,msg in prompts:record(title,mode,msg)
    plan=get('/api/plan'+q)
    assert plan['draft'] and len(plan['draft']['days'])==1, plan
    response=e2e.post('/api/plan',dict(pid=PID,as_of=DAY,decision='save'))
    recording['steps'].append(dict(title='Save as my plan',mode='plan',type='save',response=response,after=snapshot()))
    nxt=get('/api/next-signal'+q);sig=nxt['signals'][0]
    signal=dict(day=nxt['day'],kind=sig['kind'])
    record('Review monitoring signal','monitor',signal=signal)
    recording['steps'][-1]['next_signal']=nxt
    record('Safety response','checkin','I had chest pain just now')
    recording['complete']=True
    save();print('COMPLETE:',OUT,flush=True)
if __name__=='__main__':main()
