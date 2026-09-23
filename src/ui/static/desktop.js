/* Desktop integration: local request guard, provider setup, durable job history. */
const originalFetch = window.fetch.bind(window);
window.fetch = (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  if (url.origin === location.origin) {
    const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
    headers.set('X-Reader-Client', 'desktop');
    options = { ...options, headers };
  }
  return originalFetch(input, options);
};

const FULLTEXT_NOTE = 'PaperPilot 会尝试获取论文全文。部分论文可能因访问权限或来源限制无法下载，届时可补充 PDF，或基于现有资料继续分析。摘要分析不能替代全文核查。';
let desktopJobs = [];
let pollingJobs = false;

function desktopModal(title, content) {
  const modal = document.createElement('div');
  modal.className = 'settings-modal desktop-modal';
  modal.innerHTML = `<section class="settings-box" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="desktop-modal-heading"><h2>${esc(title)}</h2><button class="rc-btn" data-close aria-label="关闭">关闭</button></div><div class="settings-scroll">${content}</div></section>`;
  const previousFocus=document.activeElement;
  const close=()=>{modal.remove();previousFocus?.focus();};
  modal.querySelector('[data-close]').onclick = close;
  modal.addEventListener('keydown', e => {
    if(e.key==='Escape')close();
    if(e.key==='Tab'){
      const controls=[...modal.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary')].filter(el=>el.getClientRects().length>0);
      const first=controls[0],last=controls[controls.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
  });
  document.body.append(modal);
  modal.querySelector('button').focus();
  return modal;
}

function showGettingStarted() {
  const modal = desktopModal('开始使用 PaperPilot', `<p class="desktop-lead">从一个具体创新方向出发，查清哪些实现已有论文覆盖，哪些差异值得进一步核查和实验。</p>
    <ol class="setup-steps"><li><strong>连接你自己的模型</strong><p>填写服务地址、模型名称和 API Key。调用费用由你的服务账户承担。</p></li><li><strong>描述实现想法</strong><p>说明想把什么方法用在哪个任务、要解决什么问题，以及希望核查的差异。无需事先备齐论文；已有材料可以选中或上传，不会替换你的问题。可以从首页示例入手，把任务与方法换成你关注的方向。</p></li><li><strong>查看依据与待验证项</strong><p>报告区分已有覆盖、候选差异和证据不足，保留最近工作与验证步骤。没有合格候选也是有效结果。</p></li><li><strong>在原会话继续研究</strong><p>补全文或提出新点子后，使用「研究 Agent」更新分析；「讨论结果」只讨论已有资料。${FULLTEXT_NOTE}</p></li></ol>
    <div class="desktop-notice">论文文本会发送给你配置的模型服务。开启外部解析或向量检索时，也会发送给相应服务；请只处理有权使用的资料。文件与任务记录保存在本机。</div>
    <div class="desktop-action-row"><button class="settings-save" id="start-configure">配置模型</button><button class="rc-btn" id="start-explore">先看看界面</button></div>`);
  modal.querySelector('#start-configure').onclick = () => { localStorage.setItem('readerIntroSeen','1'); modal.remove(); showSettings(); };
  modal.querySelector('#start-explore').onclick = () => { localStorage.setItem('readerIntroSeen','1'); modal.remove(); };
}

async function showSettings() {
  try {
    const response = await fetch('/api/settings');
    if (!response.ok) throw new Error('无法读取设置');
    const cfg = await response.json();
    const input = (id,label,value='',type='text',placeholder='') => `<label class="settings-field"><span>${label}</span><input id="${id}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`;
    const hint = value => value ? '已保存；留空保留原密钥' : '粘贴你的 API Key';
    const modal = desktopModal('连接与设置', `<p class="desktop-lead">使用你自己的 API。先配置语言模型，其余服务按需开启。</p>
      <div class="settings-group-title">语言模型 · OpenAI Chat Completions 兼容接口</div>
      ${input('cfg-base','服务地址',cfg.llm.base_url,'url','https://你的服务/v1')}
      ${input('cfg-key','API Key','','password',hint(cfg.llm.has_key))}
      ${input('cfg-fast','常规模型',cfg.llm.fast_model)}${input('cfg-deep','分析模型',cfg.llm.reasoning_model)}
      <p class="settings-note">支持不同厂商的 OpenAI Chat Completions 兼容接口。填写厂商提供的基础地址与准确模型 ID，通常地址以 /v1 结尾，不要填完整的 /chat/completions 路径。API Key 属于当前服务。</p>
      <label class="settings-field"><span>接口参数方案</span><select id="cfg-capability"><option value="auto">自动识别（官方 DeepSeek 启用专用参数）</option><option value="compatible">通用 OpenAI 兼容（不附加厂商参数）</option><option value="deepseek">DeepSeek 参数（含兼容网关）</option></select></label>
      <details class="optional-settings"><summary>模型调用参数 · 思考与输出预算</summary>
      ${input('cfg-output','每次请求输出预算（token，含模型思考）',String(cfg.llm.max_output_tokens),'number')}
      ${input('cfg-timeout','单次请求超时（秒）',String(cfg.llm.timeout_seconds),'number')}
      <label class="settings-field"><span>输出预算参数名</span><select id="cfg-token-param"><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option><option value="omit">不发送，由服务决定</option></select></label>
      <label class="settings-field"><span>JSON 输出约束</span><select id="cfg-json-output"><option value="auto">自动（通用服务只使用提示词）</option><option value="json_object">发送 response_format: json_object</option><option value="prompt">仅提示词约束</option></select></label>
      <label class="settings-field"><span>常规任务思考强度</span><select id="cfg-regular-effort"><option value="none">关闭思考</option><option value="low">低</option><option value="high">高</option><option value="max">最高</option></select></label>
      <label class="settings-field"><span>证据审查与空白分析思考强度</span><select id="cfg-analysis-effort"><option value="none">关闭思考</option><option value="low">低</option><option value="high">高</option><option value="max">最高</option></select></label>
      <p class="settings-note" id="cfg-effort-hint">两个思考档位用于 DeepSeek 参数方案。通用方案不发送这些档位；可按厂商文档在下面填写支持的参数。DeepSeek 的 V4.1 Flash 调用名为 deepseek-flash。</p>
      <label class="settings-field"><span>厂商附加参数（可选 JSON）</span><textarea id="cfg-extra-body" rows="4" spellcheck="false">${esc(JSON.stringify(cfg.llm.extra_body_params||{},null,2))}</textarea></label>
      <p class="settings-note">只支持 temperature、top_p、reasoning_effort、thinking、enable_thinking 和 chat_template_kwargs。勿填写密钥。参数必须符合厂商文档；不支持的参数会导致请求失败。这里填写的参数优先于自动参数。</p></details>
      <div class="settings-group-title">研究任务 · 检索与处理预算</div>
      ${input('cfg-parse','本轮尝试处理的全文篇数（0–100）',String(cfg.search.deep_parse_top_k??50),'number')}
      ${input('cfg-calls','每任务模型请求上限（含重试，1–1000）',String(cfg.research.max_model_calls),'number')}
      ${input('cfg-minutes','任务时限（分钟，1–480）',String(cfg.research.timeout_minutes),'number')}
      <p class="settings-note">新的工作区默认尝试处理 50 篇全文。已保存的范围会保留；超过上限或失败的论文会列在报告中，不算作已读。补全文后切换「研究 Agent」重新分析；「讨论结果」不会重新检索。</p>
      <details class="optional-settings"><summary>可选服务与分析范围</summary><div class="settings-group-title">论文解析</div>
      <label class="settings-field"><span>使用 MinerU 外部解析</span><input id="cfg-mineru" type="checkbox" ${cfg.mineru.enabled?'checked':''}></label>
      ${input('cfg-mineru-key','MinerU Token','','password',hint(cfg.mineru.has_token))}
      <p class="settings-note">默认可用本地 PDF 文本提取。复杂公式、扫描 PDF 可能需要外部解析，开启后论文会发送给 MinerU。</p>
      <div class="settings-group-title">资料检索</div>
      <label class="settings-field"><span>检索方式</span><select id="cfg-vector"><option value="local">本地文本检索（无需 API）</option><option value="openai">外部向量服务（需要可选依赖）</option></select></label>
      ${input('cfg-vector-base','向量服务地址',cfg.embedding.base_url,'url')}${input('cfg-vector-model','向量模型',cfg.embedding.model)}${input('cfg-vector-key','向量 API Key','','password',hint(cfg.embedding.has_key))}
      ${input('cfg-rounds','检索查询数',String(cfg.search.max_rounds||3),'number')}${input('cfg-topk','每次检索篇数',String(cfg.search.top_k_per_round||20),'number')}
      <p class="settings-note">${FULLTEXT_NOTE}</p><p class="settings-note">密钥保存方式：${esc(cfg.secret_storage)}。切换向量服务后需重启，旧索引需要重新生成。</p>
      </details><details class="optional-settings" id="advanced-storage"><summary>高级选项 · 工作区与数据库</summary>
      <div class="storage-summary"><strong>${cfg.storage?.backend==='mysql'?'MySQL 工作区':'本地保存 · SQLite'}</strong><p>工作区位置：<span class="workspace-path">${esc(cfg.storage?.workspace||'')}</span></p><p>${cfg.storage?.backend==='mysql'?'记录存入你配置的 MySQL，PDF 和报告保留在本地。连接失败不会自动切回本地库。':'论文记录、会话与任务保存在本机，无需安装数据库或注册 PaperPilot 账号。'}</p></div>
      <p class="settings-note">日常使用无需修改数据库。备份前请关闭应用，复制完整工作区；MySQL 工作区还需单独备份数据库。</p>
      <details class="optional-settings"><summary>使用可选 MySQL 模式</summary><p class="settings-note">先准备专用数据库和账号，再关闭应用，从 PowerShell 启动一个独立工作区：</p><pre><code>./AIReader.exe --storage mysql --workspace D:/ReaderData/mysql-research</code></pre><p class="settings-note">首次启动会打开连接向导。此操作不会搬迁当前记录，也不提供云同步；迁移步骤见发布包 docs/MYSQL.md。已有 MySQL 工作区仍按原配置打开。</p></details></details>
      <p id="settings-result" role="status"></p><div class="desktop-action-row"><button class="settings-save" id="settings-save-new">保存设置</button><button class="rc-btn" id="settings-test-new">测试已保存连接</button><button class="rc-btn" id="settings-clear-key">清除模型密钥</button></div>`);
    const settingsBox=modal.querySelector('.settings-box');
    modal.querySelector('#cfg-regular-effort').value=cfg.llm.regular_effort||'max';
    modal.querySelector('#cfg-analysis-effort').value=cfg.llm.analysis_effort||'max';
    modal.querySelector('#cfg-capability').value=cfg.llm.capability_profile||'auto';
    modal.querySelector('#cfg-token-param').value=cfg.llm.token_parameter||'max_tokens';
    modal.querySelector('#cfg-json-output').value=cfg.llm.json_output||'auto';
    const updateCapabilities=()=>{
      const mode=modal.querySelector('#cfg-capability').value;
      let host='';try{host=new URL(modal.querySelector('#cfg-base').value).hostname;}catch(_){}
      const enabled=mode==='deepseek'||(mode==='auto'&&host==='api.deepseek.com');
      for(const id of ['cfg-regular-effort','cfg-analysis-effort'])modal.querySelector('#'+id).disabled=!enabled;
    };
    modal.querySelector('#cfg-capability').onchange=updateCapabilities;
    modal.querySelector('#cfg-base').addEventListener('input',updateCapabilities);updateCapabilities();
    settingsBox.append(modal.querySelector('#settings-result'),modal.querySelector('.desktop-action-row'));
    modal.querySelector('#cfg-vector').value = cfg.embedding.provider === 'local' ? 'local' : 'openai';
    const value = id => modal.querySelector('#'+id).value.trim();
    const status = modal.querySelector('#settings-result');
    modal.querySelector('#settings-save-new').onclick = async function () {
      this.disabled=true;
      try {
        const payload={base_url:value('cfg-base'),fast_model:value('cfg-fast'),reasoning_model:value('cfg-deep'),mineru_enabled:modal.querySelector('#cfg-mineru').checked,embedding_provider:value('cfg-vector'),embedding_base_url:value('cfg-vector-base'),embedding_model:value('cfg-vector-model'),search_max_rounds:Number(value('cfg-rounds')),search_top_k_per_round:Number(value('cfg-topk')),search_deep_parse_top_k:Number(value('cfg-parse'))};
        payload.research_max_model_calls=Number(value('cfg-calls'));
        payload.research_timeout_minutes=Number(value('cfg-minutes'));
        Object.assign(payload,{max_output_tokens:Number(value('cfg-output')),timeout_seconds:Number(value('cfg-timeout')),regular_effort:value('cfg-regular-effort'),analysis_effort:value('cfg-analysis-effort')});
        Object.assign(payload,{capability_profile:value('cfg-capability'),token_parameter:value('cfg-token-param'),json_output:value('cfg-json-output'),extra_body_params:JSON.parse(value('cfg-extra-body')||'{}')});
        for(const [id,key] of [['cfg-key','deepseek_api_key'],['cfg-mineru-key','mineru_api_token'],['cfg-vector-key','siliconflow_api_key']]) if(value(id)) payload[key]=value(id);
        const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await r.json(); if(!r.ok) throw new Error(typeof d.detail==='string'?d.detail:'请检查服务地址、模型与数字范围');
        status.textContent=d.message; status.className='setup-success';
        updateReaderSetup(await (await fetch('/api/settings')).json());
        for(const id of ['cfg-key','cfg-mineru-key','cfg-vector-key']) { modal.querySelector('#'+id).value=''; modal.querySelector('#'+id).placeholder='已保存；留空保留原密钥'; }
      } catch(e) { status.textContent=e.message; status.className='setup-error'; } finally { this.disabled=false; }
    };
    modal.querySelector('#settings-test-new').onclick=async function(){
      this.disabled=true; status.textContent='正在检查已保存服务的模型列表接口…';
      try { const r=await fetch('/api/settings/test',{method:'POST'}); const d=await r.json(); status.textContent=d.message||d.detail; } catch(e){status.textContent='连接失败，请检查网络。';} finally{this.disabled=false;}
    };
    modal.querySelector('#settings-clear-key').onclick=async()=>{
      if(!confirm('清除后，新分析需要重新配置模型密钥。继续吗？')) return;
      const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deepseek_api_key:''})});
      status.textContent=r.ok?'已清除模型密钥':'清除失败，请先结束任务';
      if(r.ok)updateReaderSetup(await (await fetch('/api/settings')).json());
    };
  } catch(e){toast(e.message,'error');}
}

const jobLabels={queued:'排队中',running:'正在运行',cancel_requested:'正在取消',cancelled:'已取消',interrupted:'执行中断',failed:'执行失败',succeeded:'已完成'};
async function refreshDesktopJobs(){
  if(pollingJobs) return;
  pollingJobs=true;
  try{
    const r=await fetch('/api/jobs'); if(!r.ok) return;
    const jobs=(await r.json()).jobs||[];
    const active=jobs.find(j=>j.session_id===state.currentSessionId&&['queued','running','cancel_requested'].includes(j.status));
    const previous=desktopJobs.find(j=>j.session_id===state.currentSessionId&&['queued','running','cancel_requested'].includes(j.status));
    desktopJobs=jobs;
    const count=jobs.filter(j=>['queued','running','cancel_requested'].includes(j.status)).length;
    const button=document.getElementById('desktop-jobs'); if(button) button.textContent=count?`任务记录 · ${count} 进行中`:'任务记录';
    if(active){ state.running=true; updateSendBtn(); if(!document.getElementById('running-card')) showRunningCard('可在任务记录查看状态；关闭应用会中断执行。',jobLabels[active.status]); }
    else if(previous){const completedSession=state.currentSessionId;state.running=false;updateSendBtn();removeRunningCard();await loadSessions();if(state.currentSessionId===completedSession&&!state.loadingSession&&!state.running&&state.activeView==='chat')await loadSession(completedSession);}
  }catch(e){/* Connection indicator handles unavailable local API. */}finally{pollingJobs=false;}
}

async function showDesktopJobs(){
  await refreshDesktopJobs();
  const modal=desktopModal('任务记录',`<p class="settings-note">任务记录保存在本机。关闭应用会停止执行；重新打开后可查看中断任务。重试可能再次调用外部 API 并产生费用。</p><div class="job-list"></div>`);
  const list=modal.querySelector('.job-list');
  if(!desktopJobs.length){list.innerHTML='<p class="desktop-empty">还没有任务。配置模型后，输入一个研究问题开始。</p>';return;}
  for(const job of desktopJobs){
    const row=document.createElement('article');row.className='desktop-job';
    row.innerHTML=`<div><strong>${esc(jobLabels[job.status]||job.status)}</strong><span class="job-time">${esc(job.created_at)}</span></div><code>${esc(job.id)}</code>${job.error?`<p>${esc(job.error)}</p>`:''}<div class="desktop-action-row"></div>`;
    const scope=document.createElement('p');
    scope.textContent=(job.mode==='report_chat'?'讨论结果':'研究 Agent')+(job.research_limits?` · 请求上限 ${job.research_limits.max_model_calls} 次 · 时限 ${job.research_limits.timeout_minutes} 分钟`:' · 旧任务未记录预算快照');
    row.insertBefore(scope,row.querySelector('.desktop-action-row'));
    const actions=row.querySelector('.desktop-action-row');
    const details=document.createElement('button');details.className='rc-btn';details.textContent='查看研究过程';details.onclick=()=>{modal.remove();openResearchConversation(job.session_id,job.id);};actions.append(details);
    const open=document.createElement('button');open.className='rc-btn';open.textContent='打开会话';open.onclick=()=>{modal.remove();loadSession(job.session_id);};actions.append(open);
    if(['interrupted','failed','cancelled'].includes(job.status)){
      const retry=document.createElement('button');retry.className='rc-btn';retry.textContent='重试任务';retry.onclick=async()=>{if(!confirm('重试会重新执行分析，已验证的论文画像可能复用，外部 API 仍可能再次计费。继续吗？'))return;const r=await fetch(`/api/jobs/${encodeURIComponent(job.id)}/retry`,{method:'POST'});if(!r.ok){toast((await r.json()).detail||'重试失败','error');return;}modal.remove();await refreshDesktopJobs();showDesktopJobs();};actions.append(retry);
    }
    if(['queued','running'].includes(job.status)){
      const cancel=document.createElement('button');cancel.className='rc-btn';cancel.textContent='取消';cancel.onclick=async()=>{await fetch(`/api/jobs/${encodeURIComponent(job.id)}/cancel`,{method:'POST'});modal.remove();await refreshDesktopJobs();showDesktopJobs();};actions.append(cancel);
    }
    list.append(row);
  }
}

document.addEventListener('DOMContentLoaded',async()=>{
  const bar=document.createElement('div');bar.className='desktop-toolbar';
  bar.innerHTML='<span>本地桌面工作区</span><button id="desktop-jobs" class="rc-btn">任务记录</button><button id="desktop-guide" class="rc-btn">使用引导</button><button id="desktop-settings" class="rc-btn">连接与设置</button>';
  document.querySelector('.main-content')?.prepend(bar);
  if(!bar.isConnected) document.querySelector('main').prepend(bar);
  bar.querySelector('#desktop-jobs').onclick=showDesktopJobs;bar.querySelector('#desktop-guide').onclick=showGettingStarted;bar.querySelector('#desktop-settings').onclick=showSettings;
  const note=document.createElement('p');note.className='desktop-fulltext-note';note.textContent=FULLTEXT_NOTE;
  document.querySelector('#chat-welcome')?.append(note);
  const input=document.querySelector('#user-input'); if(input) input.setAttribute('placeholder','描述研究问题；也可以先选择或上传论文…');
  try {const config=await (await fetch('/api/settings')).json();updateReaderSetup(config);}catch(e){}
  await refreshDesktopJobs();setInterval(refreshDesktopJobs,2000);
});
