function renderResearchWelcome() {
  const target=document.getElementById('chat-welcome');
  if(!target)return;
  const labels=['核查已有实现','收窄创新假设','追查相邻方向'];
  target.innerHTML=`<div class="welcome-eyebrow">论文研究方向解析 Agent</div>
    <h1 class="welcome-title">这个研究方向，<span>还有哪些空白？</span></h1>
    <p class="welcome-subtitle">描述你想采用的实现手段，让 Agent 查已有工作、核对反证，判断哪些想法值得继续验证。<br>论文、证据与历次判断保存在工作区，补充资料或新点子后可以接着研究。</p>
    <div class="welcome-actions"><button class="welcome-primary" onclick="exitReportChatMode(true);document.getElementById('user-input').focus()">提出研究方向 ${readerIcon('arrow')}</button><button class="welcome-secondary" onclick="uploadPaper()">${readerIcon('upload')} 补充已有论文</button></div>
    <div class="welcome-setup" id="welcome-setup" hidden><span>${readerIcon('settings')} 首次使用，先连接你自己的模型 API。</span><button onclick="showSettings()">配置模型 ${readerIcon('arrow')}</button></div>
    <p class="availability-note">查已有实现 → 核对覆盖与反证 → 收窄候选空白 → 提出验证步骤。没有可靠候选时会说明证据缺口；未搜到不等于无人研究。</p>
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
function formatConversationTime(value) {
  if(!value)return '';
  // Legacy timestamps are shown as recorded, without guessing their timezone.
  return String(value).replace('T',' ').slice(0,16);
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
  if(hint)hint.textContent=state.chatMode==='report_chat'?'讨论已有结果；补检索或核查新想法请切换「研究 Agent」':'研究 Agent：查已有实现、核查候选空白；补全文后可在原会话继续';
  const input=document.getElementById('user-input');
  if(input)input.placeholder=state.chatMode==='report_chat'?'继续追问已有报告，或选择论文作为上下文…':'我想把什么方法用在哪个模块？需要核查哪些已有实现和空白…';
  updateJumpToLatest();
}
function updateReaderSetup(config) {
  state.fulltextLimit=config.search?.deep_parse_top_k??50;
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
