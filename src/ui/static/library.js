/* Model-assisted organization lives in the library, separate from research chat. */
const libraryOrganizer={info:null,job:null,source:null,after:0,progress:null,busy:false,submitting:false};

function paperOrganizationHTML(p){
  const tags=Array.isArray(p.tags)?p.tags:[];
  const facets=Object.values(p.facets||{}).flat();
  const labels=[...new Set([...facets,...tags.map(t=>t==='单目先验'?'单目深度先验':t)])];
  const source=p.canonical_id&&p.canonical_id!==p.id;
  return `<div class="paper-organization-tags"><span class="paper-category">${esc(p.category||'待分类')}</span>${labels.map(t=>`<span>${esc(t)}</span>`).join('')}${p.duplicate_count>0?`<span class="paper-merge-label">已合并 ${Number(p.duplicate_count)+1} 条记录</span>`:''}${source?'<span class="paper-merge-label">合并来源</span>':''}</div>${p.reading_summary?`<p class="paper-reading-summary">${esc(p.reading_summary)}</p>`:''}`;
}
function renderLibraryFacets(){
  const box=document.getElementById('library-facets');if(!box)return;
  const selected=state.libraryFacets||{};
  box.innerHTML=(libraryOrganizer.info?.facets||[]).map(f=>`<label>${esc(f.label)}<select data-facet="${esc(f.key)}"><option value="">全部</option>${f.values.map(v=>`<option value="${esc(v.name)}" ${selected[f.key]===v.name?'selected':''}>${esc(v.name)} · ${v.count}</option>`).join('')}</select></label>`).join('')+'<button class="rc-btn" data-clear-facets>清除筛选</button>';
  box.querySelectorAll('[data-facet]').forEach(select=>select.onchange=()=>{state.libraryFacets={...state.libraryFacets,[select.dataset.facet]:select.value};loadPapers();});
  box.querySelector('[data-clear-facets]').onclick=()=>{state.libraryFacets={};state.libraryCategory='';renderLibraryFacets();renderLibraryCategories();loadPapers();};
}
function libraryDetailHTML(d){
  const org=d.organization||{},sources=d.duplicate_records||[];
  return `<section class="paper-organization-detail">${paperOrganizationHTML({...org,reading_summary:org.reading_summary})}${sources.length?`<h3>合并来源 · ${sources.length} 条</h3><p>这些记录指向同一篇论文。原 PDF、各版本解析与历史引用均保留，可以逐条打开核对。</p><div class="library-source-list">${sources.map(p=>`<button class="rc-btn" data-library-source="${esc(p.id)}">${esc(p.title)} · ${esc(p.id)} · ${esc(paperStatus(p).text)}</button>`).join('')}</div>`:''}</section>`;
}
document.addEventListener('click',e=>{const button=e.target.closest?.('[data-library-source]');if(button)openPaperDetail(button.dataset.librarySource);});
function updateLibraryPagination(){
  const offset=state.libraryOffset||0,total=state.libraryTotal||0;
  document.getElementById('library-pagination').hidden=total<=100;
  document.getElementById('library-page-info').textContent=`${total?offset+1:0}–${Math.min(offset+100,total)} / ${total} 篇`;
  document.getElementById('library-previous').disabled=offset===0;
  document.getElementById('library-next').disabled=offset+100>=total;
}
function changeLibraryPage(direction){state.libraryOffset=Math.max(0,(state.libraryOffset||0)+direction*100);loadPapers(false);}
function selectLibraryCategory(name){state.libraryCategory=name;renderLibraryCategories();loadPapers();}
function renderLibraryCategories(){
  const info=libraryOrganizer.info;if(!info)return;
  const nav=document.getElementById('library-categories');
  const categories=[{name:'',label:'全部论文',count:info.library_items??info.papers},...info.categories.map(c=>({...c,label:c.name}))];
  nav.innerHTML=categories.map(c=>`<button class="library-category${(state.libraryCategory||'')===c.name?' active':''}" aria-pressed="${(state.libraryCategory||'')===c.name}" data-category="${esc(c.name)}">${esc(c.label)} <span>${c.count}</span></button>`).join('');
  nav.querySelectorAll('[data-category]').forEach(button=>button.onclick=()=>selectLibraryCategory(button.dataset.category));
}
async function refreshLibraryOrganization(){
  const r=await fetch('/api/library/organization');if(!r.ok)throw new Error('无法读取论文库整理记录');
  libraryOrganizer.info=await r.json();
  const info=libraryOrganizer.info;
  state.libraryItemCount=info.library_items??info.papers;
  const count=document.getElementById('folder-papers-count');if(count)count.textContent=state.libraryItemCount;
  if(state.libraryCategory&&!info.categories.some(c=>c.name===state.libraryCategory))state.libraryCategory='';
  document.getElementById('library-overview').textContent=`${info.papers} 篇论文${info.prior_count?` · ${info.prior_count} 条先验笔记`:''}${info.merged_records?` · 已折叠 ${info.merged_records} 条重复记录`:''} · 分类与摘要由模型整理，可打开原文核对`;
  renderLibraryCategories();renderLibraryFacets();renderLibraryOrganizerStatus();
}
async function showLibraryOrganizer(){
  try{
    await refreshLibraryOrganization();
    if(libraryOrganizer.busy){document.getElementById('library-organize-status').scrollIntoView({block:'nearest'});return;}
    const info=libraryOrganizer.info;
    const modal=desktopModal('自动整理论文库',`<p class="desktop-lead">复用 ${info.reused_papers||0} 篇已有分类，本次处理 ${info.pending_papers??info.papers} 篇新增或变化论文。</p><ul class="library-organize-explanation"><li>${info.estimated_calls?`向你配置的模型发送需更新论文的标题、摘要和解析片段；预计约 ${info.estimated_calls} 次请求，重试会增加费用。`:'资料和规则没有变化，本次核对无需调用模型。'}</li><li>沿用已有分类目录；资料或规则变化时重新判断。新加入的论文不会让未变的资料重复分析。</li><li>按 DOI、论文编号、相同 PDF 或标题与作者核对重复项。同名但身份不明的论文保留。</li><li>原 PDF、解析、会话和报告保留，完成后可撤销。</li></ul><p class="settings-note">核对整个论文库，不受当前筛选影响；不处理已隐藏记录和先验笔记，不重新下载或解析全文。</p><div id="library-start-error" role="alert"></div><div class="desktop-action-row"><button class="settings-save" id="library-start" ${!info.records||info.records>info.max_papers?'disabled':''}>${info.estimated_calls?'开始增量整理':'核对整理结果'}</button></div>`);
    modal.querySelector('#library-start').onclick=async function(){
      if(libraryOrganizer.submitting)return;
      libraryOrganizer.submitting=true;this.disabled=true;
      try{
        const key=genSessionId();
        const r=await fetch('/api/library/organize',{method:'POST',headers:{'Idempotency-Key':key}});
        const data=await r.json();if(!r.ok)throw new Error(data.detail||'整理未能启动');
        modal.remove();await attachLibraryOrganization(data.job_id);
      }catch(e){modal.querySelector('#library-start-error').textContent=e.message;this.disabled=false;}
      finally{libraryOrganizer.submitting=false;}
    };
  }catch(e){toast(e.message,'error');}
}
function renderLibraryOrganizerStatus(){
  const box=document.getElementById('library-organize-status');if(!box)return;
  const job=libraryOrganizer.job,info=libraryOrganizer.info,latest=info?.latest;
  const busy=job&&['queued','running','cancel_requested'].includes(job.status);libraryOrganizer.busy=Boolean(busy);
  const button=document.getElementById('library-organize');button.disabled=libraryOrganizer.submitting;
  button.innerHTML=readerIcon('folder')+(busy?'查看整理进度':'自动整理');
  box.hidden=!job&&!latest;
  if(busy){
    const progress=libraryOrganizer.progress||{},done=progress.completed||0,total=progress.total||0;
    const phase={waiting:'等待模型响应',thinking:'模型正在分类',processing:'模型正在分类',receiving:'正在接收分类结果',retrying:'模型请求正在重试'}[progress.phase]||'';
    box.innerHTML=`<div><strong>${esc(job.status==='queued'?'等待前面的任务完成':job.status==='cancel_requested'?'正在取消整理':progress.message||'准备整理论文库')}</strong><p>${total?`${done} / ${total} 篇 · `:''}${esc(phase)}${progress.model_calls?` · ${progress.model_calls} 次模型请求`:''}</p>${total?`<progress max="${total}" value="${done}" aria-label="分类进度"></progress>`:''}</div><button class="rc-btn" data-cancel ${job.status==='cancel_requested'?'disabled':''}>取消整理</button>`;
    box.querySelector('[data-cancel]').onclick=cancelLibraryOrganization;
  }else if(job&&['failed','cancelled','interrupted'].includes(job.status)){
    box.innerHTML=`<div><strong>${job.status==='failed'?'本次整理未完成':job.status==='cancelled'?'已取消整理':'整理已中断'}</strong><p>${esc(job.error||'已完成的分类断点保留。重试可继续处理未变的资料。')}</p></div><div class="library-status-actions"><button class="rc-btn" data-retry>继续整理</button>${latest?'<button class="rc-btn" data-last>上次整理结果</button>':''}</div>`;
    box.querySelector('[data-retry]').onclick=retryLibraryOrganization;
    if(latest)box.querySelector('[data-last]').onclick=showLibraryOrganizationResult;
  }else if(latest){
    box.innerHTML=`<div><strong>论文库已整理</strong><p>${latest.records} 条记录整理为 ${latest.papers} 篇论文 · ${latest.categories} 个分类 · 折叠 ${latest.merged_records} 条重复记录${latest.suspected_groups?.length?` · ${latest.suspected_groups.length} 组同名记录待核对`:''}</p></div><div class="library-status-actions"><button class="rc-btn" data-result>查看整理结果</button><button class="rc-btn" data-undo>撤销本次整理</button></div>`;
    box.querySelector('[data-result]').onclick=showLibraryOrganizationResult;box.querySelector('[data-undo]').onclick=undoLibraryOrganization;
  }else{box.hidden=true;}
}
async function attachLibraryOrganization(id){
  libraryOrganizer.source?.close();libraryOrganizer.after=0;libraryOrganizer.progress=null;
  const r=await fetch(`/api/jobs/${encodeURIComponent(id)}`);if(!r.ok)throw new Error('无法读取整理任务');
  libraryOrganizer.job=await r.json();renderLibraryOrganizerStatus();
  if(!['queued','running','cancel_requested'].includes(libraryOrganizer.job.status)){await refreshLibraryOrganization();return;}
  const source=new EventSource(`/api/jobs/${encodeURIComponent(id)}/stream`);libraryOrganizer.source=source;
  source.onmessage=async e=>{
    try{
      const frame=JSON.parse(e.data);
      if(frame.kind==='event'){
        if(libraryOrganizer.job?.id!==id)return;
        const row=frame.row;if(row.seq<=libraryOrganizer.after)return;libraryOrganizer.after=row.seq;
        const body=typeof row.body==='string'?JSON.parse(row.body):row.body;
        if(body.attempt_token&&body.attempt_token<(libraryOrganizer.job.token||0))return;
        if(body.attempt_token>(libraryOrganizer.job.token||0))libraryOrganizer.job.token=body.attempt_token;
        if(body.type==='library_progress')libraryOrganizer.progress={...body,phase:''};
        if(body.type==='library_model_activity')libraryOrganizer.progress={...libraryOrganizer.progress,...body};
      }else if(frame.kind==='status'){
        libraryOrganizer.job=frame.job;
        if(!['queued','running','cancel_requested'].includes(frame.job.status)){
          source.close();libraryOrganizer.source=null;
          await refreshLibraryOrganization();await loadPapers();
          if(frame.job.status==='succeeded')toast('论文库整理完成','');
        }
      }
      renderLibraryOrganizerStatus();
    }catch(e){console.warn('Library progress refresh failed',e);}
  };
}
async function cancelLibraryOrganization(){
  try{const r=await fetch(`/api/jobs/${encodeURIComponent(libraryOrganizer.job.id)}/cancel`,{method:'POST'});const data=await r.json();if(!r.ok)throw new Error(data.detail||'取消失败');libraryOrganizer.job=data;renderLibraryOrganizerStatus();}catch(e){toast(e.message,'error');}
}
async function retryLibraryOrganization(){
  try{const r=await fetch(`/api/jobs/${encodeURIComponent(libraryOrganizer.job.id)}/retry`,{method:'POST'});const data=await r.json();if(!r.ok)throw new Error(data.detail||'重试失败');await attachLibraryOrganization(data.id);}catch(e){toast(e.message,'error');}
}
function showLibraryOrganizationResult(){
  const result=libraryOrganizer.info?.latest;if(!result)return;
  const modal=desktopModal('论文库整理结果',`<p class="desktop-lead">${result.records} 条记录 → ${result.papers} 篇论文，折叠 ${result.merged_records} 条重复记录。</p><p>${result.reused_papers!=null?`复用 ${result.reused_papers} 篇，重新分类 ${result.classified_papers} 篇。`:''}分类和阅读摘要可以在论文库直接查看。模型调用 ${result.model_calls} 次；仅用于浏览整理，不替代论文证据。</p><h3>同名但未合并的记录</h3>${result.suspected_groups?.length?result.suspected_groups.map(g=>`<div class="library-suspect"><strong>${esc(g.title)}</strong><p>缺少一致身份或编号存在冲突，已保留。</p>${g.paper_ids.map(id=>`<button class="rc-btn" data-open-paper="${esc(id)}">查看 ${esc(id)}</button>`).join('')}</div>`).join(''):'<p>没有需要核对的同名记录。</p>'}`);
  modal.querySelectorAll('[data-open-paper]').forEach(button=>button.onclick=()=>{modal.remove();openPaperDetail(button.dataset.openPaper);});
}
async function undoLibraryOrganization(){
  const latest=libraryOrganizer.info?.latest;if(!latest)return;
  if(!confirm('撤销本次分类和重复折叠，恢复整理前的论文库展示。原 PDF、解析和历史报告不受影响。继续吗？'))return;
  try{const r=await fetch(`/api/library/organization/${encodeURIComponent(latest.run_id)}/undo`,{method:'POST'});const data=await r.json();if(!r.ok)throw new Error(data.detail||'撤销失败');libraryOrganizer.job=null;libraryOrganizer.progress=null;await refreshLibraryOrganization();await loadPapers();toast('已恢复整理前的论文库','');}catch(e){toast(e.message,'error');}
}
async function showFulltextIndex(){
  try{
    const response=await fetch('/api/retrieval/status');if(!response.ok)throw new Error('无法读取全文索引');
    const state=await response.json();
    const modal=desktopModal('论文全文检索',`<p class="desktop-lead">${esc(state.mode)} · 已索引 ${state.documents} 篇、${state.chunks} 个片段</p><p>在本机用中英文检索原文章节和表格。新增或改变的全文才会重新编码，无 API 费用；缺全文的论文不冒充已阅读。研究 Agent 会自动查询并补充已读论文的索引。</p><div class="desktop-action-row"><button class="settings-save" id="fulltext-build" ${state.model_ready?'':'disabled'}>增量更新全文索引</button></div><p id="fulltext-status" role="status"></p><label class="settings-field"><span>查找论文原文</span><input id="fulltext-query" placeholder="例如：跨领域知识蒸馏的实验和损失函数"></label><button class="rc-btn" id="fulltext-search">检索</button><div id="fulltext-results"></div>`);
    let stream=null;
    const close=modal.querySelector('[data-close]');if(close)close.addEventListener('click',()=>stream?.close());
    modal.addEventListener('keydown',e=>{if(e.key==='Escape')stream?.close();});
    modal.querySelector('#fulltext-search').onclick=async function(){
      const q=modal.querySelector('#fulltext-query').value.trim();if(!q)return;this.disabled=true;
      const results=modal.querySelector('#fulltext-results');results.textContent='检索原文中…';
      try{
        const r=await fetch('/api/retrieval/search?q='+encodeURIComponent(q));const data=await r.json();if(!r.ok)throw new Error(data.detail||'检索失败');
        results.innerHTML=data.hits.length?data.hits.map(h=>`<article class="library-suspect"><strong>${esc(h.title)}</strong><p>${esc(h.section||'原文章节')} · ${h.methods.map(m=>m==='vector'?'向量':'文本').join('＋')} · 原文 ${h.start}–${h.end}</p><div class="msg-body">${safeMarkdown(h.text)}</div><button class="rc-btn" data-paper-id="${esc(h.paper_id)}">打开论文</button></article>`).join(''):'没有命中。请先更新全文索引，或换用更具体的词。';
        results.querySelectorAll('[data-paper-id]').forEach(b=>b.onclick=()=>{stream?.close();modal.remove();openPaperDetail(b.dataset.paperId);});
      }catch(e){results.textContent=e.message;}finally{this.disabled=false;}
    };
    modal.querySelector('#fulltext-build').onclick=async function(){
      this.disabled=true;const status=modal.querySelector('#fulltext-status');status.textContent='提交本地索引任务…';
      try{
        const r=await fetch('/api/retrieval/index',{method:'POST',headers:{'Idempotency-Key':genSessionId()}});const data=await r.json();if(!r.ok)throw new Error(data.detail||'索引启动失败');
        stream?.close();stream=new EventSource(`/api/jobs/${encodeURIComponent(data.job_id)}/stream`);
        stream.onmessage=e=>{const frame=JSON.parse(e.data);if(!modal.isConnected){stream.close();return;}
          if(frame.kind==='event'){const body=typeof frame.row.body==='string'?JSON.parse(frame.row.body):frame.row.body;if(body.type==='index_progress')status.textContent=`${body.completed} / ${body.total} · ${body.message}`;}
          if(frame.kind==='status'){
            if(frame.job.status==='queued')status.textContent='已排队，等待当前任务完成';
            if(!['queued','running','cancel_requested'].includes(frame.job.status)){
              stream.close();this.disabled=false;const result=frame.job.index_result||{};
              if(result.index)modal.querySelector('.desktop-lead').textContent=`${result.index.mode} · 已索引 ${result.index.documents} 篇、${result.index.chunks} 个片段`;
              status.textContent=frame.job.status==='succeeded'?`索引已更新：新增或变化 ${result.indexed||0} 篇，复用 ${result.reused||0} 篇，缺全文 ${result.missing||0} 篇，失败 ${(result.failed||[]).length} 篇`:(frame.job.error||'索引已中断，可在任务记录中重试');
            }
          }
        };
      }catch(e){status.textContent=e.message;this.disabled=false;}
    };
  }catch(e){toast(e.message,'error');}
}

document.addEventListener('DOMContentLoaded',async()=>{
  try{await refreshLibraryOrganization();const r=await fetch('/api/jobs');if(!r.ok)return;const jobs=(await r.json()).jobs||[];const job=jobs.find(j=>j.mode==='library_organize');if(job)await attachLibraryOrganization(job.id);}catch(e){console.warn('Library organization unavailable',e);}
});
