const {readFileSync}=require('node:fs');
const vm=require('node:vm'),assert=require('node:assert/strict');
const recording=JSON.parse(readFileSync('evidence/local-demo-2026-10-22/ui-recording.json','utf8'));
const ctx={RECORDING:recording,URL,Response,JSON,setTimeout:(f)=>setTimeout(f,0),clearTimeout};ctx.window=ctx;
vm.createContext(ctx);vm.runInContext(readFileSync('tools/replay-adapter.js','utf8'),ctx);
(async()=>{
  assert.equal((await ctx.fetch('https://example.com/unrecorded')).status,409);
  assert.equal((await ctx.fetch('/api/turn',{method:'POST',body:JSON.stringify({message:'unrecorded question'})})).status,409);
  for(const step of recording.steps){
    if(step.type==='save'){
      assert.equal((await ctx.fetch('/api/plan',{method:'POST',body:'{"decision":"discard"}'})).status,409);
      const response=await ctx.fetch('/api/plan',{method:'POST',body:'{"decision":"save"}'});
      assert.equal((await response.json()).version.version,1);
    }else{
      const body=step.type==='turn'?{message:step.message}:step.signal;
      if(step.type==='signal')assert.deepEqual(await(await ctx.fetch('/api/next-signal')).json(),step.next_signal);
      assert.equal((await ctx.fetch(step.type==='turn'?'/api/turn':'/api/signal',{method:'POST',body:JSON.stringify(body)})).status,200);
      const events=[];
      await new Promise((resolve,reject)=>{
        const es=new ctx.EventSource('/api/stream');es.onerror=reject;
        es.onmessage=({data})=>{const event=JSON.parse(data);events.push(event);if(event.kind==='done'){es.close();resolve();}};
      });
      assert.deepEqual(events,step.events);
    }
    assert.deepEqual(await(await ctx.fetch('/api/plan')).json(),step.after['/api/plan']);
  }
  assert.equal(ctx.REPLAY.index,9);
  assert.equal((await ctx.fetch('/api/next-signal')).status,409);
  console.log('PASS: complete recorded HTTP/SSE chain, exact events, saved state, unsupported request rejection.');
})().catch(e=>{console.error(e);process.exitCode=1});
