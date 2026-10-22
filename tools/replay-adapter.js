// All requests terminate here. No backend, account, network, or model is used.
window.REPLAY = {index:0, state:RECORDING.initial, pending:null, playing:false};
window.fetch = async function(path, options={}) {
  const u=new URL(path,'http://replay.invalid'), p=u.pathname;
  const r=REPLAY, s=RECORDING.steps[r.index];
  const body=options.body ? JSON.parse(options.body) : {};
  const response=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
  const reject=()=>response({error:'这是录制回放，仅支持顶部提示的本步操作。请用顶部菜单选择录制步骤。'},409);
  if (options.method==='POST') {
    if (p==='/api/clock' && body.as_of===RECORDING.day) return response({as_of:RECORDING.day});
    if (p==='/api/turn' && s?.type==='turn' && body.message===s.message && !r.pending) {r.pending=s;return response({ok:true});}
    if (p==='/api/signal' && s?.type==='signal' && body.kind===s.signal.kind && body.day===s.signal.day && !r.pending) {r.pending=s;return response({ok:true});}
    if (p==='/api/plan' && s?.type==='save' && body.decision==='save') {
      r.state=s.after;r.index++;setTimeout(()=>window.replayUpdate?.(),0);return response(s.response);
    }
    return reject();
  }
  if(p==='/api/next-signal') return s?.type==='signal'?response(s.next_signal):reject();
  if(p==='/api/monitor-series') return response(RECORDING.base.series[u.searchParams.get('kind')]);
  if(p==='/api/inspect' && u.searchParams.get('start')==='2020-02-03' && u.searchParams.get('end')===RECORDING.day && !u.searchParams.get('compare_start')) return response(RECORDING.base.inspect[u.searchParams.get('domain')]);
  if(p==='/api/inspect') return response({error:'自由日期查询未录制；请查看聊天中的原始数据表或 Readiness snapshot。'});
  if(p in r.state) return response(r.state[p]);
  if(p in RECORDING.base) return response(RECORDING.base[p]);
  return reject();
};
window.EventSource=class {
  constructor() {
    const s=REPLAY.pending;
    if(!s) {setTimeout(()=>this.onerror?.({}),0);return;}
    REPLAY.playing=true;
    let i=0;
    const tick=()=>{
      if(this.closed)return;
      const event=s.events[i++];
      if(event.kind==='done') {
        REPLAY.state=s.after;REPLAY.index++;REPLAY.pending=null;REPLAY.playing=false;
      }
      this.onmessage?.({data:JSON.stringify(event)});
      window.replayUpdate?.();
      if(i<s.events.length&&!this.closed)this.timer=setTimeout(tick,180);
    };
    this.timer=setTimeout(tick,100);
  }
  close(){this.closed=true;clearTimeout(this.timer);}
};
