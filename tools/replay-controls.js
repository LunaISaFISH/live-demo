// Replay-only restrictions and navigation. The live UI source above is unchanged.
const liveContextControls=contextControls;
contextControls=function(busy){liveContextControls(busy);replayUpdate();};
window.replayUpdate=function(){
  const s=RECORDING.steps[REPLAY.index], locked=BUSY||CONTEXT_BUSY||REPLAY.playing;
  for(const id of ['pid','clock','clock-back','clock-next','clock-latest','reset','new-chat']) $(id).disabled=true;
  $('msg').readOnly=true;
  if(!locked)$('msg').value=s?.type==='turn'?s.message:'';
  $('send').disabled=locked||s?.type!=='turn';
  $('next-signal').disabled=locked||s?.type!=='signal';
  $('starters').style.display='none';
  $('replay-step').disabled=locked;
  $('replay-restart').disabled=locked;
  $('replay-step').value=String(REPLAY.index);
  $('replay-hint').textContent=locked?'正在播放真实记录…':!s?'回放完成；点击 Evidence 查看审计、记忆与来源。':s.type==='save'?'本步：点击计划卡上的 Save as my plan。':s.type==='signal'?'本步：点击 Advance to next review。':'本步：点击 Ask coach 发送已录制的输入。';
  document.querySelectorAll('[data-plan]').forEach(b=>b.disabled=locked||s?.type!=='save'||b.dataset.plan!=='save');
  document.querySelectorAll('.review').forEach(b=>b.disabled=true);
  $('run-tests').textContent='Show recorded test results';
  document.querySelectorAll('#inspect-form input, #inspect-form select, #inspect-form button').forEach(e=>e.disabled=true);
};
$('replay-step').innerHTML=RECORDING.steps.map((s,i)=>`<option value="${i}">${i+1}. ${s.title}</option>`).join('')+'<option value="9">10. Evidence / 完整结果</option>';
async function replayJump(index){
  if(BUSY||CONTEXT_BUSY||REPLAY.playing)return;
  REPLAY.index=index;REPLAY.state=index?RECORDING.steps[index-1].after:RECORDING.initial;
  MONITOR_RUN=null;
  await select(RECORDING.pid);
  setMode(RECORDING.steps[index]?.mode||'checkin');
  replayUpdate();
}
$('replay-step').onchange=e=>replayJump(Number(e.target.value));
$('replay-restart').onclick=()=>replayJump(0);
// The standalone document has no second page to navigate to.
document.querySelectorAll('.views a').forEach(a=>a.remove());
// Refresh inserts plan buttons asynchronously; keep replay restrictions current.
const replayObserver=new MutationObserver(()=>replayUpdate());
replayObserver.observe($('plan-panel'),{childList:true});

const liveRefresh=refresh;
refresh=async function(){const result=await liveRefresh();replayUpdate();return result;};
