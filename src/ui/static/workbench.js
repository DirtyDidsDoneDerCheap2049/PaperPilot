function renderResearchWelcome() {
  const target=document.getElementById('chat-welcome');
  if(!target)return;
  const labels=['核查已有实现','收窄创新假设','追查相邻方向'];
  target.innerHTML=`<div class="welcome-eyebrow">论文研究方向解析 Agent</div>
    <h1 class="welcome-title">从研究问题开始，<span>让论文给出依据。</span></h1>
    <p class="welcome-subtitle">描述你的问题，让 Agent 检索论文、读取证据，比较方法与指标，或梳理研究空白。<br>它会根据已找到的资料决定是否补查，论文与历次结论保存在工作区，方便继续研究。</p>
    <div class="welcome-actions"><button class="welcome-primary" onclick="exitReportChatMode(true);document.getElementById('user-input').focus()">提出研究问题 ${readerIcon('arrow')}</button><button class="welcome-secondary" onclick="uploadPaper()">${readerIcon('upload')} 补充已有论文</button></div>
    <div class="welcome-setup" id="welcome-setup" hidden><span>${readerIcon('settings')} 首次使用，先连接你自己的模型 API。</span><button onclick="showSettings()">配置模型 ${readerIcon('arrow')}</button></div>
    <p class="availability-note">按问题选择检索、阅读、比较与核验。资料不足时明确说明已确认部分和缺失，未搜到不等于无人研究。</p>
    <div class="welcome-examples"><div class="ex-label">从这些问题试起 <span>替换为你的研究方向、方法和待验证问题</span></div>${EXAMPLES.map((text,i)=>`<button class="ex-item" data-example="${i}"><span class="example-number">0${i+1}</span><span class="example-title">${labels[i]} ${readerIcon('arrow')}</span><span>${esc(text)}</span></button>`).join('')}</div>
    <details class="fulltext-disclosure"><summary>${readerIcon('info')} 全文获取与结果可靠性</summary><p>${FULLTEXT_NOTE}</p></details>`;
  target.querySelectorAll('[data-example]').forEach(button=>button.onclick=()=>useExample(Number(button.dataset.example)));
  if(window.readerConfigured===false)target.querySelector('#welcome-setup').hidden=false;
}

const createOriginalResearch=newResearch;
window.newResearch=function(){
  if(createOriginalResearch()===false)return;
  updateTokenCount();
  renderResearchWelcome();
};

const conversationDrafts = new Map();
function readHistoricalReport(index) {
  const message=state.messages[index];if(!message)return;
  openDocumentReader({title:'历史报告 · '+formatConversationTime(message.created_at), content:message.content, sourceView:'chat', kind:'report'});
}
function saveConversationDraft() {
  const input=document.getElementById('user-input');
  if(input)conversationDrafts.set(state.currentSessionId||'new', input.value);
}
function restoreConversationDraft() {
  const input=document.getElementById('user-input');
  if(input)input.value=conversationDrafts.get(state.currentSessionId||'new')||'';
  resizeComposer();updateTokenCount();
}
function resizeComposer() {
  const input=document.getElementById('user-input');if(!input)return;
  input.style.height='auto';input.style.height=Math.min(180,Math.max(52,input.scrollHeight))+'px';
}
function formatConversationTime(value, timeZone) {
  if(!value)return '';
  const raw=String(value).trim();
  // SQLite datetime('now') and MySQL DATETIME values are UTC in our stores.
  // Unzoned ISO strings belong to older local logs; leave those as recorded.
  const databaseUTC=/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$/.test(raw);
  const zoned=/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const recorded=raw.replace('T',' ').slice(0,16);
  if(!databaseUTC&&!zoned)return recorded;
  const instant=new Date(raw.replace(' ','T')+(databaseUTC?'Z':''));
  if(!Number.isFinite(instant.getTime()))return recorded;
  const options={year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'};
  if(timeZone)options.timeZone=timeZone;
  const parts=new Intl.DateTimeFormat('zh-CN',options).formatToParts(instant);
  const field=name=>parts.find(part=>part.type===name)?.value||'';
  return `${field('year')}-${field('month')}-${field('day')} ${field('hour')}:${field('minute')}`;
}
function jobUsageHTML(usage) {
  if(!usage||typeof usage!=='object')return '';
  const number=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString('zh-CN'):'未返回';
  const rate=usage.input_cache_hit_rate;
  const hit=typeof rate==='number'&&Number.isFinite(rate)&&rate>=0&&rate<=1?(rate*100).toFixed(1)+'%':'未返回';
  const count=usage.model_requests, measured=usage.requests_with_usage, cacheMeasured=usage.requests_with_cache_usage;
  const tokens=`输入 ${number(usage.prompt_tokens)} token · 输入缓存命中 ${hit} · 输出 ${number(usage.completion_tokens)} token`;
  const coverage=`用量返回 ${number(measured)}/${number(count)} 次；缓存统计返回 ${number(cacheMeasured)}/${number(count)} 次`;
  return `<p class="settings-note job-usage">${esc(tokens)}<br>${esc('本地证据复用 '+number(usage.local_evidence_cache_hits)+' 篇次 · '+coverage)}<br>缓存比例只按返回缓存统计的输入计算；输出含服务商计入的思考 token，缺失统计不代表零消耗。</p>`;
}
function updateJumpToLatest() {
  const el=document.getElementById('chat-content'), button=document.getElementById('jump-latest');
  if(el&&button)button.hidden=el.scrollHeight-el.clientHeight-el.scrollTop<100;
}
function jumpToLatest() {
  const el=document.getElementById('chat-content');el.scrollTop=el.scrollHeight;updateJumpToLatest();
}
function updateConversationChrome() {
  const heading=document.getElementById('conversation-heading');if(!heading)return;
  heading.hidden=!state.currentSessionId;
  const session=state.sessions.find(s=>s.id===state.currentSessionId);
  document.getElementById('conversation-title').textContent=(session?.title||'新的研究会话').replace(/^[>\s]+/,'');
  document.getElementById('conversation-summary').textContent=state.loadingSession?'正在加载历史消息…':`${state.messages.length} 条消息${session?.created_at?' · '+formatConversationTime(session.created_at):''}${state.running?' · 任务进行中':''}`;
  const hint=document.getElementById('conversation-mode-hint');
  if(hint)hint.textContent=state.chatMode==='report_chat'?'讨论已有结果；补检索或核查新想法请切换「研究 Agent」':'研究 Agent：接续本会话和已有论文，按问题检索、比较与核验';
  const input=document.getElementById('user-input');
  if(input)input.placeholder=state.chatMode==='report_chat'?'继续追问已有报告，或选择论文作为上下文…':'描述你的研究问题、时间范围和需要核查的条件…';
  updateJumpToLatest();
}
function updateReaderSetup(config) {
  state.fulltextLimit=config.search?.deep_parse_top_k??60;
  updateConversationChrome();
  window.readerConfigured=Boolean(config.llm.has_key);
  const setup=document.getElementById('welcome-setup');
  if(setup)setup.hidden=window.readerConfigured;
  const badge=document.getElementById('model-badge');
  badge.textContent=config.llm.has_key?config.llm.fast_model:'配置模型';
  badge.setAttribute('role','button');badge.tabIndex=0;
  badge.onclick=showSettings;
  badge.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showSettings();}};
  const storage=document.getElementById('storage-label');
  storage.innerHTML=readerIcon('folder')+(config.storage?.backend==='mysql'?'MySQL 工作区':'本地工作区 · 无需数据库服务');
  storage.title=config.storage?.workspace||'';
}
document.addEventListener('DOMContentLoaded',()=>{
  const bar=document.querySelector('.desktop-toolbar');
  const titlebar=document.querySelector('.main-titlebar');
  if(bar&&titlebar)titlebar.insertBefore(bar,titlebar.querySelector('.titlebar-actions'));
  hydrateReaderIcons();
  document.querySelectorAll('.rail-btn[title]').forEach(button=>button.setAttribute('aria-label',button.title));
  document.querySelectorAll('.ws-nav-item[data-v]').forEach(node=>{
    node.tabIndex=0;node.setAttribute('role','button');
    node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();node.click();}};
  });
  renderResearchWelcome();
  if(!state.currentSessionId&&state.inspectorVisible)toggleInspector();
  document.getElementById('chat-content').addEventListener('scroll',updateJumpToLatest,{passive:true});
  updateConversationChrome();updateSendBtn();
});
