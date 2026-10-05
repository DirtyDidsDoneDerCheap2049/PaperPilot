/* In-conversation research stream. Transport events are reduced once, not re-parsed per frame. */
const researchChat={session:null,epoch:0,jobs:new Map(),source:null,timer:null};
const researchNames={DirectionParser:'理解问题',SearchAgent:'检索论文',ParseAgent:'获取全文',InnovationExtractor:'阅读论文',AnalyzeAgent:'比较与分析',ResearchFollowup:'补查与核验',CriticAgent:'复核结论',MonitorAgent:'检查资料',ReportGenerator:'撰写报告',Orchestrator:'研究'};
function researchEvent(row){try{return typeof row.body==='string'?JSON.parse(row.body):row.body||{};}catch(_){return {};}}
function reduceResearch(run,row){
  if(row.seq<=run.after)return false;run.after=row.seq;
  const e=researchEvent(row);
  if(e.attempt_token!=null&&e.attempt_token!==run.job.token){
    if(Number(e.attempt_token)>Number(run.job.token)){
      run.job.token=e.attempt_token;run.stages=[];run.calls.clear();run.current=null;
    }else return false;
  }
  if(!['agent_started','agent_progress','agent_completed','agent_failed','model_activity','model_delta'].includes(e.type))return false;
  const name=e.agent||'Orchestrator';
  if(e.type==='agent_started'){
    const stage={id:e.run_id||String(row.seq),name,label:e.label||researchNames[name]||'研究',message:e.message||'',status:'running',calls:[],revision:0};
    run.stages.push(stage);run.current=stage;
  }
  let stage=[...run.stages].reverse().find(s=>s.name===name)||run.current;
  if(!stage)return false;
  let changed=e.type==='agent_started';
  const oldMessage=stage.message,oldStatus=stage.status;
  if(e.type==='agent_progress')stage.message=e.message||stage.message;
  if(e.type==='agent_completed'||e.type==='agent_failed')stage.status=e.type==='agent_completed'?'completed':'failed';
  changed ||= stage.message!==oldMessage||stage.status!==oldStatus;
  if(!['model_activity','model_delta'].includes(e.type)){if(changed)stage.revision++;return changed;}
  const id=String(e.call||0);let call=run.calls.get(id);
  if(!call){call={id,text:'',preview:'',phase:'waiting',label:e.item_label||stage.label,kind:e.presentation||'research',revision:0,textVersion:0};run.calls.set(id,call);stage.calls.push(call);changed=true;}
  const oldPhase=call.phase,oldPreview=call.preview,oldKind=call.kind;
  if(e.type==='model_delta'&&e.content){call.text+=e.content;call.textVersion++;changed=true;}
  if(e.presentation)call.kind=e.presentation;
  if(e.phase&&(e.phase!=='processing'||call.phase==='waiting'))call.phase=e.phase;
  if(e.preview)call.preview=e.preview;
  changed ||= oldPhase!==call.phase||oldPreview!==call.preview||oldKind!==call.kind;
  if(changed){stage.revision++;call.revision++;}
  return changed;
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
  if(call.proseSource===call.text&&call.proseKind===call.kind)return call.prose;
  call.proseSource=call.text;call.proseKind=call.kind;
  call.prose=extractResearchProse(call);return call.prose;
}
function extractResearchProse(call){
  if(call.kind==='report_draft')return call.text;
  // Scan appended characters once. Only root-level, explicitly public prose is shown.
  if(!call.proseScan||call.textVersion==null||call.text.length<call.proseScan.offset){
    call.proseScan={offset:0,depth:0,inString:false,escape:false,unicode:null,role:null,
      key:'',expect:'key',valueKey:null,fields:Object.create(null)};
  }
  const scan=call.proseScan,allowed=new Set(['progress_summary','method_summary','innovation_detail','research_question']);
  const append=c=>{if(scan.role==='key'){if(scan.key.length<256)scan.key+=c;}
    else if(scan.role==='value')scan.fields[scan.valueKey]+=c;};
  for(;scan.offset<call.text.length;scan.offset++){
    const c=call.text[scan.offset];
    if(scan.inString){
      if(scan.unicode!==null){
        if(!/[\da-f]/i.test(c)){scan.unicode=null;scan.role=null;continue;}
        scan.unicode+=c;if(scan.unicode.length===4){append(String.fromCharCode(parseInt(scan.unicode,16)));scan.unicode=null;}
      }else if(scan.escape){
        scan.escape=false;if(c==='u')scan.unicode='';
        else append(({n:'\n',r:'\r',t:'\t',b:'\b',f:'\f','"':'"','\\':'\\','/':'/'})[c]||'');
      }else if(c==='\\')scan.escape=true;
      else if(c==='"'){
        scan.inString=false;
        if(scan.role==='key'){scan.valueKey=scan.key;scan.expect='colon';}
        else if(scan.depth===1)scan.expect='after';
        scan.role=null;
      }else append(c);
      continue;
    }
    if(c==='"'){
      scan.inString=true;scan.role=scan.depth===1&&scan.expect==='key'?'key':
        scan.depth===1&&scan.expect==='value'&&allowed.has(scan.valueKey)?'value':null;
      if(scan.role==='key')scan.key='';
      if(scan.role==='value')scan.fields[scan.valueKey]='';
    }else if(c==='{'||c==='['){scan.depth++;}
    else if(c==='}'||c===']'){scan.depth--;if(scan.depth===1)scan.expect='after';}
    else if(scan.depth===1&&c===':')scan.expect='value';
    else if(scan.depth===1&&c===','){scan.expect='key';scan.valueKey=null;}
  }
  return scan.fields.progress_summary||['method_summary','innovation_detail','research_question']
    .map(k=>scan.fields[k]||'').filter(Boolean).join('\n\n');
}
function closeResearchStream(){researchChat.source?.close();researchChat.source=null;}
function researchErrorMessage(error){
  const text=String(error||'');
  return /402|Insufficient Balance/i.test(text)?'模型服务余额不足，本轮研究未完成。已取得的资料与分析已保留。':text;
}
function scheduleResearch(){
  if(researchChat.timer!=null)return;
  const nextFrame=typeof requestAnimationFrame==='function'?requestAnimationFrame:fn=>setTimeout(fn,16);
  researchChat.timer=nextFrame(()=>{researchChat.timer=null;renderResearchChat();});
}
function researchHead(run){
  const active=['queued','running','cancel_requested'].includes(run.job.status);
  const heading=active?'PaperPilot 正在研究':run.job.status==='succeeded'?'研究过程':'研究已停止';
  return `<strong>${heading}</strong>${active?'<button class="rc-btn" data-research-cancel="'+esc(run.job.id)+'">停止</button>':''}`;
}
function researchStage(run,stage){
      const active=['queued','running','cancel_requested'].includes(run.job.status);
      const cacheKey=[stage.revision||0,run.job.status,stage===run.current].join(':');
      if(stage.renderKey===cacheKey)return stage.html;
      const running=active&&stage===run.current&&stage.status==='running';
      const stateText=stage.status==='failed'?'未完成':running?'进行中':stage.status==='completed'?'已完成':active?'处理中':'已停止';
      stage.renderKey=cacheKey;
      stage.html=`<summary><span class="research-step-dot ${running?'is-active':''}"></span><strong>${esc(stage.label)}</strong><span>${stateText}</span></summary><div class="research-step-body">`+
        (stage.message?`<p class="research-tool-line">${esc(stage.message)}</p>`:'')+
        stage.calls.map(call=>{
          const thinking=active&&call.phase==='thinking';const prose=researchProse(call);
          const draftLabel=active?'报告草稿 · 正在核对':run.job.status==='succeeded'?'生成过程中的草稿 · 最终版本请看报告':'草稿未通过交付检查 · 不能作为最终结论';
          if(call.markdownSource!==prose){call.markdownSource=prose;call.markdown=prose?safeMarkdown(prose):'';}
          return `<div class="research-call"><div class="research-think ${thinking?'is-active':''}" title="思考仅作过程提示，不作为研究结论"><span>${thinking?'思考中':call.phase==='failed'?'请求失败':call.phase==='waiting'?'等待模型':'思考'}</span><span>${esc(call.preview||call.label)}</span></div>${prose?`<div class="research-prose">${call.kind==='report_draft'?'<small>'+draftLabel+'</small>':''}${call.markdown}</div>`:''}</div>`;
        }).join('')+'</div>';
      return stage.html;
}
function researchBlock(run){
  return `<div class="research-turn-head">${researchHead(run)}</div>`+run.stages.filter(s=>s.name!=='Orchestrator').map(stage=>
    `<details class="research-step" data-step="${esc(run.job.id+'-'+stage.id)}">${researchStage(run,stage)}</details>`).join('');
}
function researchStageState(run,stage){
  const active=['queued','running','cancel_requested'].includes(run.job.status);
  const running=active&&stage===run.current&&stage.status==='running';
  return {active,running,text:stage.status==='failed'?'未完成':running?'进行中':stage.status==='completed'?'已完成':active?'处理中':'已停止'};
}
function appendMarkdownLines(stream,delta){
  const completed=[];
  for(const c of delta){
    if(c!=='\n'){stream.line+=c;continue;}
    const line=stream.line;stream.line='';stream.block.push(line+'\n');
    const fence=line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if(fence){
      if(!stream.fence)stream.fence={char:fence[1][0],length:fence[1].length};
      else if(fence[1][0]===stream.fence.char&&fence[1].length>=stream.fence.length&&!fence[2].trim())stream.fence=null;
    }
    if(!line.trim()&&!stream.fence){
      const text=stream.block.join('');stream.block=[];if(text.trim())completed.push(text);
    }
  }
  return completed;
}
function patchResearchProse(node,prose,streaming){
  if(node._source===prose&&node._streaming===streaming)return;
  node.hidden=!prose;
  if(!streaming){
    node.innerHTML=prose?safeMarkdown(prose):'';node._stream=null;
  }else{
    if(!node._stream||!prose.startsWith(node._source||'')){
      node.replaceChildren();node._stream={offset:0,line:'',block:[],fence:null,tail:null};
    }
    const s=node._stream;
    if(!s.tail){s.tail=document.createElement('div');s.tail.className='research-prose-tail';node.append(s.tail);}
    for(const text of appendMarkdownLines(s,prose.slice(s.offset))){
      s.tail.innerHTML=safeMarkdown(text);s.tail.className='research-prose-block';
      s.tail=document.createElement('div');s.tail.className='research-prose-tail';node.append(s.tail);
    }
    const tail=s.block.join('')+s.line;
    if(s.tail._text!==tail){s.tail.innerHTML=tail?safeMarkdown(tail):'';s.tail._text=tail;}
    s.offset=prose.length;
  }
  node._source=prose;node._streaming=streaming;
}
function patchResearchCall(parent,run,call){
  let node=call.node;
  if(!node||node.parentElement!==parent){
    node=document.createElement('div');node.className='research-call';node.dataset.call=call.id;
    node.innerHTML='<div class="research-think"><span class="research-call-phase"></span><span class="research-call-label"></span><span class="research-call-preview"></span></div><small class="research-draft-label"></small><div class="research-prose"></div>';
    call.node=node;parent.append(node);
  }
  const key=[call.revision||0,run.job.status].join(':');if(node._key===key)return;node._key=key;
  const active=['queued','running','cancel_requested'].includes(run.job.status),thinking=active&&call.phase==='thinking';
  const phase=call.phase==='failed'?'请求失败':call.phase==='received'?'已完成':
    thinking?'思考中':call.phase==='waiting'||call.phase==='processing'?'等待模型':active?'正在输出':'已结束';
  node.querySelector('.research-think').classList.toggle('is-active',thinking);
  node.querySelector('.research-call-phase').textContent=phase;
  node.querySelector('.research-call-label').textContent=call.label;
  const preview=node.querySelector('.research-call-preview');preview.textContent=thinking?call.preview:'';preview.hidden=!thinking||!call.preview;
  const draft=node.querySelector('.research-draft-label');draft.hidden=call.kind!=='report_draft';
  draft.textContent=active?'报告草稿 · 正在核对':run.job.status==='succeeded'?'生成过程中的草稿 · 最终版本请看报告':'草稿未通过交付检查 · 不能作为最终结论';
  patchResearchProse(node.querySelector('.research-prose'),researchProse(call),active&&!['received','failed'].includes(call.phase));
}
function renderResearchChat(){
  const container=document.getElementById('chat-content');if(!container)return;
  if(researchChat.session!==state.currentSessionId){container.querySelectorAll('.research-turn').forEach(n=>n.remove());return;}
  const follow=container.scrollHeight-container.clientHeight-container.scrollTop<100,oldTop=container.scrollTop;
  for(const run of researchChat.jobs.values()){
    let block=container.querySelector(`[data-research-job="${CSS.escape(run.job.id)}"]`);
    if(!block){block=document.createElement('section');block.className='research-turn';block.dataset.researchJob=run.job.id;block.innerHTML='<div class="research-turn-head"></div><div class="research-steps"></div><div class="research-turn-footer"></div>';container.append(block);}
    run.manualOpen=run.manualOpen||new Map();
    const head=block.querySelector('.research-turn-head'),headHTML=researchHead(run);
    if(head._html!==headHTML){head.innerHTML=headHTML;head._html=headHTML;
      head.querySelector('[data-research-cancel]')?.addEventListener('click',async()=>{
        const r=await fetch(`/api/jobs/${encodeURIComponent(run.job.id)}/cancel`,{method:'POST'});
        if(!r.ok){toast('停止请求失败，请重试','error');return;}run.job=await r.json();scheduleResearch();
      });
    }
    const steps=block.querySelector('.research-steps'),stages=run.stages.filter(s=>s.name!=='Orchestrator');
    const keys=new Set(stages.map(s=>run.job.id+'-'+s.id));
    steps.querySelectorAll('details').forEach(d=>{if(!keys.has(d.dataset.step))d.remove();});
    steps.querySelector('.research-pending')?.remove();
    for(const stage of stages){
      const key=run.job.id+'-'+stage.id;
      let d=steps.querySelector(`[data-step="${CSS.escape(key)}"]`);
      if(!d){
        d=document.createElement('details');d.className='research-step';d.dataset.step=key;
        d.innerHTML='<summary><span class="research-step-dot"></span><strong></strong><span class="research-stage-status"></span></summary><div class="research-step-body"><p class="research-tool-line"></p><div class="research-calls"></div></div>';
        d.querySelector('summary').addEventListener('click',()=>run.manualOpen.set(key,!d.open));
        d.addEventListener('toggle',scheduleResearch);steps.append(d);
      }
      const stageState=researchStageState(run,stage);
      const stateKey=[stage.label,stageState.text,stageState.running].join(':');
      if(d._stateKey!==stateKey){
        d.querySelector('summary strong').textContent=stage.label;
        d.querySelector('.research-stage-status').textContent=stageState.text;
        d.querySelector('.research-step-dot').classList.toggle('is-active',stageState.running);d._stateKey=stateKey;
      }
      const open=run.manualOpen.has(key)?run.manualOpen.get(key):stageState.running;
      if(d.open!==open)d.open=open;
      // Closed historical bodies are materialized only when opened.
      if(d.open){
        const line=d.querySelector('.research-tool-line');
        if(line._message!==stage.message){line.textContent=stage.message;line.hidden=!stage.message;line._message=stage.message;}
        const calls=d.querySelector('.research-calls');for(const call of stage.calls)patchResearchCall(calls,run,call);
      }
    }
    if(!stages.length)steps.innerHTML='<p class="research-tool-line research-pending">任务已提交，正在准备研究资料。</p>';
    const footer=block.querySelector('.research-turn-footer'),error=run.job.error||'';
    if(footer._error!==error){footer.innerHTML=error?`<p class="research-error">${esc(researchErrorMessage(error))}</p>`:'';footer._error=error;}
    // Reinsert after the matching user turn whenever chat messages are re-rendered.
    const users=[...container.querySelectorAll('.msg.user')];
    const anchor=users.find(n=>n.dataset.messageId===run.job.message_id)||users.at(-1);
    if(anchor&&block.previousElementSibling!==anchor)anchor.after(block);
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
    renderResearchChat();
    // One ordered replay/live channel; paint each arriving page instead of awaiting all history.
    const source=new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?after=${run.after}&batch=64`);researchChat.source=source;
    source.onmessage=message=>{
      if(epoch!==researchChat.epoch)return;
      const data=JSON.parse(message.data);let changed=false;
      const rows=data.kind==='events'?data.rows:data.kind==='event'?[data.row]:[];
      for(const row of rows)changed=reduceResearch(run,row)||changed;
      if(data.kind==='status'){
        changed=run.job.status!==data.job.status||run.job.error!==data.job.error||run.job.token!==data.job.token||changed;
        run.job=data.job;if(!['queued','running','cancel_requested'].includes(run.job.status))closeResearchStream();
      }
      if(changed)scheduleResearch();
    };
    source.onerror=()=>{if(epoch===researchChat.epoch)scheduleResearch();};
  }catch(error){if(epoch===researchChat.epoch)toast(error.message,'error');}
}
async function openResearchConversation(session=state.currentSessionId,jobId=null){
  if(session!==state.currentSessionId)await loadSession(session);else switchView('chat');
  await attachResearchChat(session,jobId);
}
