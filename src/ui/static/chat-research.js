/* In-conversation research stream. Transport events are reduced once, not re-parsed per frame. */
const researchChat={session:null,epoch:0,jobs:new Map(),source:null,timer:null};
const researchNames={DirectionParser:'理解问题',SearchAgent:'检索论文',ParseAgent:'获取全文',InnovationExtractor:'阅读论文',AnalyzeAgent:'比较与分析',ResearchFollowup:'补查与核验',CriticAgent:'复核结论',MonitorAgent:'检查资料',ReportGenerator:'撰写报告',Orchestrator:'研究'};
function researchEvent(row){try{return typeof row.body==='string'?JSON.parse(row.body):row.body||{};}catch(_){return {};}}
function reduceResearch(run,row){
  if(row.seq<=run.after)return;run.after=row.seq;
  const e=researchEvent(row);
  if(e.attempt_token!=null&&e.attempt_token!==run.job.token){
    if(Number(e.attempt_token)>Number(run.job.token)){
      run.job.token=e.attempt_token;run.stages=[];run.calls.clear();run.current=null;
    }else return;
  }
  const name=e.agent||'Orchestrator';
  if(e.type==='agent_started'){
    const stage={id:e.run_id||String(row.seq),name,label:researchNames[name]||'研究',message:e.message||'',status:'running',calls:[]};
    run.stages.push(stage);run.current=stage;
  }
  let stage=[...run.stages].reverse().find(s=>s.name===name)||run.current;
  if(!stage)return;
  if(e.type==='agent_progress')stage.message=e.message||stage.message;
  if(e.type==='agent_completed'||e.type==='agent_failed')stage.status=e.type==='agent_completed'?'completed':'failed';
  if(!['model_activity','model_delta'].includes(e.type))return;
  const id=String(e.call||0);let call=run.calls.get(id);
  if(!call){call={id,text:'',preview:'',phase:'waiting',label:e.item_label||stage.label,kind:e.presentation||'research'};run.calls.set(id,call);stage.calls.push(call);}
  if(e.type==='model_delta')call.text+=e.content||'';
  if(e.presentation)call.kind=e.presentation;
  if(e.phase&&(e.phase!=='processing'||call.phase==='waiting'))call.phase=e.phase;
  if(e.preview)call.preview=e.preview;
}
function jsonStringField(text,key){
  const marker='"'+key+'"',pos=text.indexOf(marker);if(pos<0)return '';
  let i=pos+marker.length;while(/\s/.test(text[i]||'')&&i<text.length)i++;
  if(text[i++]!==':')return '';while(/\s/.test(text[i]||'')&&i<text.length)i++;
  if(text[i]!=='"')return '';const start=i++;let escaped=false;
  while(i<text.length){const c=text[i++];if(c==='"'&&!escaped){try{return JSON.parse(text.slice(start,i));}catch(_){return '';}}escaped=c==='\\'&&!escaped;}
  let partial=text.slice(start+1).replace(/\\u[\da-f]{0,3}$/i,'').replace(/\\$/,'');
  try{return JSON.parse('"'+partial+'"');}catch(_){return partial.replace(/\\n/g,'\n');}
}
function researchProse(call){
  if(call.kind==='report_draft')return call.text;
  // Deliberately do not enumerate arbitrary JSON keys or nested evidence/audit objects.
  const summary=jsonStringField(call.text,'progress_summary');
  if(summary)return summary;
  return ['method_summary','innovation_detail','research_question'].map(k=>jsonStringField(call.text,k)).filter(Boolean).join('\n\n');
}
function closeResearchStream(){researchChat.source?.close();researchChat.source=null;}
function researchErrorMessage(error){
  const text=String(error||'');
  return /402|Insufficient Balance/i.test(text)?'模型服务余额不足，本轮研究未完成。已取得的资料与分析已保留。':text;
}
function scheduleResearch(){if(!researchChat.timer)researchChat.timer=setTimeout(()=>{researchChat.timer=null;renderResearchChat();},160);}
function researchBlock(run){
  const active=['queued','running','cancel_requested'].includes(run.job.status);
  const heading=active?'PaperPilot 正在研究':run.job.status==='succeeded'?'研究过程':'研究已停止';
  return `<div class="research-turn-head"><strong>${heading}</strong>${active?'<button class="rc-btn" data-research-cancel="'+esc(run.job.id)+'">停止</button>':''}</div>`+
    (run.stages.length?run.stages.filter(s=>s.name!=='Orchestrator').map((stage,index)=>{
      const running=active&&stage===run.current;
      const stateText=stage.status==='failed'?'未完成':running?'进行中':stage.status==='completed'?'已完成':active?'处理中':'已停止';
      const key=esc(run.job.id+'-'+stage.id);
      return `<details class="research-step" data-step="${key}" ${running?'open':''}><summary><span class="research-step-dot ${running?'is-active':''}"></span><strong>${esc(stage.label)}</strong><span>${stateText}</span></summary><div class="research-step-body">`+
        (stage.message?`<p class="research-tool-line">${esc(stage.message)}</p>`:'')+
        stage.calls.map(call=>{
          const thinking=active&&call.phase==='thinking';const prose=researchProse(call);
          const draftLabel=active?'报告草稿 · 正在核对':run.job.status==='succeeded'?'生成过程中的草稿 · 最终版本请看报告':'草稿未通过交付检查 · 不能作为最终结论';
          return `<div class="research-call"><div class="research-think ${thinking?'is-active':''}" title="思考仅作过程提示，不作为研究结论"><span>${thinking?'思考中':call.phase==='failed'?'请求失败':call.phase==='waiting'?'等待模型':'思考'}</span><span>${esc(call.preview||call.label)}</span></div>${prose?`<div class="research-prose">${call.kind==='report_draft'?'<small>'+draftLabel+'</small>':''}${safeMarkdown(prose)}</div>`:''}</div>`;
        }).join('')+'</div></details>';
    }).join(''):'<p class="research-tool-line">任务已提交，正在准备研究资料。</p>')+
    (run.job.error?`<p class="research-error">${esc(researchErrorMessage(run.job.error))}</p>`:'');
}
function renderResearchChat(){
  const container=document.getElementById('chat-content');if(!container)return;
  if(researchChat.session!==state.currentSessionId){container.querySelectorAll('.research-turn').forEach(n=>n.remove());return;}
  const follow=container.scrollHeight-container.clientHeight-container.scrollTop<100,oldTop=container.scrollTop;
  for(const run of researchChat.jobs.values()){
    let block=container.querySelector(`[data-research-job="${CSS.escape(run.job.id)}"]`);
    if(!block){block=document.createElement('section');block.className='research-turn';block.dataset.researchJob=run.job.id;container.append(block);}
    run.manualOpen=run.manualOpen||new Map();
    block.innerHTML=researchBlock(run);
    block.querySelectorAll('details').forEach(d=>{
      if(run.manualOpen.has(d.dataset.step))d.open=run.manualOpen.get(d.dataset.step);
      d.querySelector('summary').addEventListener('click',()=>run.manualOpen.set(d.dataset.step,!d.open));
    });
    block.querySelector('[data-research-cancel]')?.addEventListener('click',async()=>{
      const r=await fetch(`/api/jobs/${encodeURIComponent(run.job.id)}/cancel`,{method:'POST'});
      if(!r.ok){toast('停止请求失败，请重试','error');return;}run.job=await r.json();scheduleResearch();
    });
    // Reinsert after the matching user turn whenever chat messages are re-rendered.
    const users=[...container.querySelectorAll('.msg.user')];
    const anchor=users.find(n=>n.dataset.messageId===run.job.message_id)||users.at(-1);
    if(anchor)anchor.after(block);
  }
  container.scrollTop=follow?container.scrollHeight:oldTop;
}
async function attachResearchChat(session=state.currentSessionId,jobId=null){
  closeResearchStream();const epoch=++researchChat.epoch;
  if(researchChat.session!==session)researchChat.jobs.clear();researchChat.session=session;
  renderResearchChat();if(!session)return;
  try{
    const response=await fetch('/api/jobs');if(!response.ok)return;
    const jobs=(await response.json()).jobs.filter(j=>j.session_id===session&&j.mode!=='report_chat');
    if(epoch!==researchChat.epoch)return;
    const job=jobs.find(j=>j.id===jobId)||jobs[0];if(!job)return;
    let run=researchChat.jobs.get(job.id);
    if(!run||run.job.token!==job.token){run={job,after:0,stages:[],calls:new Map(),current:null};researchChat.jobs.clear();researchChat.jobs.set(job.id,run);}else run.job=job;
    while(true){const response=await fetch(`/api/jobs/${encodeURIComponent(job.id)}/events?after=${run.after}`);if(!response.ok)throw Error('读取研究过程失败');const page=(await response.json()).events||[];if(epoch!==researchChat.epoch)return;page.forEach(row=>reduceResearch(run,row));if(page.length<200)break;}
    renderResearchChat();
    if(!['queued','running','cancel_requested'].includes(job.status))return;
    const source=new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?after=${run.after}`);researchChat.source=source;
    source.onmessage=message=>{if(epoch!==researchChat.epoch)return;const data=JSON.parse(message.data);if(data.kind==='event')reduceResearch(run,data.row);if(data.kind==='status'){run.job=data.job;if(!['queued','running','cancel_requested'].includes(run.job.status))closeResearchStream();}scheduleResearch();};
    source.onerror=()=>{if(epoch===researchChat.epoch)scheduleResearch();};
  }catch(error){if(epoch===researchChat.epoch)toast(error.message,'error');}
}
async function openResearchConversation(session=state.currentSessionId,jobId=null){
  if(session!==state.currentSessionId)await loadSession(session);else switchView('chat');
  await attachResearchChat(session,jobId);
}
