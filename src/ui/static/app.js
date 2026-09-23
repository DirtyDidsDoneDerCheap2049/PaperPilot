const EXAMPLES = [
  "在科研文献检索中用证据一致性约束候选排序，已有工作实现到了哪一步？还有哪些差异值得验证？",
  "把不确定性估计用于资源受限的迭代推理，哪些方法已经验证过？还缺哪些反例和实验？",
  "将图结构建模迁移到时间序列异常检测，请查来源方向与目标任务的已有工作，核查具体创新空间。",
];

const state = {
  activeView: "chat",
  workspace: null,
  sessions: [],
  currentSessionId: null,
  messages: [],
  files: [],
  papers: [],
  selectedPaperIds: new Set(),
  selectedPapersById: new Map(),
  agentRuns: {},
  evidence: [],
  missingPapers: [],
  currentReport: null,
  currentDocument: null,
  chatMode: "analysis",
  reportChatPath: null,
  inspectorTab: "evidence",
  inspectorVisible: false,
  sidebarVisible: true,
  sidebarAutoHidden: false,
  running: false,
  runningCardEl: null,
  innovationsExtracted: 0,
  pipelineStage: 0,
  pipelineTotal: 0,
  streaming: false,
  streamingEl: null,
  streamingContent: "",
  reportMatrix: null,
  matrixCells: [],
};

let ws = null;
let pointerDown = null;

function rememberSession(sid) {
  try {
    if (sid) localStorage.setItem("aiReaderLastSession", sid);
    else localStorage.removeItem("aiReaderLastSession");
  } catch (_) {}
}
function restoreLastSession() {
  let sid = null;
  try { sid = localStorage.getItem("aiReaderLastSession"); } catch (_) {}
  if (!sid) return;
  const known = (state.sessions || []).some(s => s.id === sid);
  if (!known) { rememberSession(null); return; }
  loadSession(sid);
}
async function init() {
  setupResponsiveLayout();
  connectWS();
  loadWorkspace();
  await loadSessions();
  loadMissingPapers();
  // Reopen the last session so a page refresh mid-run keeps receiving progress.
  restoreLastSession();
}
document.addEventListener("DOMContentLoaded", init);

/* ====== Views ====== */
function switchView(view) {
  state.activeView = view;
  document.querySelectorAll(".view-panel").forEach(p => p.style.display = "none");
  const el = document.getElementById(`view-${view}`);
  if (el) el.style.display = "flex";
  document.querySelectorAll(".rail-btn").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".ws-nav-item[data-v]").forEach(n => n.classList.toggle("active", n.dataset.v === view));
  document.getElementById("main-title").textContent =
    view === "chat" ? "对话" : view === "library" ? "论文库" : view === "reports" ? "报告" : view === "document" ? "资料阅读" : "缺全文";
  if (view === "reports") refreshReportList();
  if (view === "missing") loadMissingPapers();
  if (view === "library") loadPapers();
}
function toggleInspector() {
  const inspector = document.getElementById("inspector");
  state.inspectorVisible = !state.inspectorVisible;
  if (inspector) {
    inspector.dataset.userToggled = "1";
    delete inspector.dataset.autoHidden;
    inspector.classList.toggle("hidden", !state.inspectorVisible);
  }
}
function setupResponsiveLayout() {
  try {
    state.sidebarVisible = localStorage.getItem("aiReaderSidebarCollapsed") !== "1";
  } catch (_) {}
  const apply = () => {
    const compact = window.innerWidth < 1180;
    const narrow = window.innerWidth < 980;
    document.body.classList.toggle("layout-compact", compact);
    if (narrow && state.sidebarVisible && !state.sidebarUserToggled) {
      state.sidebarVisible = false;
      state.sidebarAutoHidden = true;
    } else if (!narrow && state.sidebarAutoHidden) {
      state.sidebarVisible = true;
      state.sidebarAutoHidden = false;
    }
    applySidebarVisibility();
    const inspector = document.getElementById("inspector");
    if (!inspector) return;
    inspector.classList.toggle('hidden', !state.inspectorVisible);
    if (compact && state.inspectorVisible && inspector.dataset.userToggled !== "1") {
      state.inspectorVisible = false;
      inspector.dataset.autoHidden = "1";
      inspector.classList.add("hidden");
    } else if (!compact && inspector.dataset.autoHidden === "1") {
      state.inspectorVisible = true;
      delete inspector.dataset.autoHidden;
      inspector.classList.remove("hidden");
    }
  };
  window.addEventListener("resize", apply);
  apply();
}
function applySidebarVisibility() {
  const narrow = window.innerWidth < 980;
  document.body.classList.toggle("sidebar-collapsed", !state.sidebarVisible && !narrow);
  document.body.classList.toggle("sidebar-open", state.sidebarVisible && narrow);
  const btn = document.getElementById("sidebar-toggle");
  if (btn) {
    btn.classList.toggle("active", state.sidebarVisible);
    btn.title = state.sidebarVisible ? "收起左侧栏" : "展开左侧栏";
  }
}
function toggleSidebar() {
  state.sidebarVisible = !state.sidebarVisible;
  state.sidebarUserToggled = true;
  state.sidebarAutoHidden = false;
  try { localStorage.setItem("aiReaderSidebarCollapsed", state.sidebarVisible ? "0" : "1"); } catch (_) {}
  applySidebarVisibility();
}
function switchInspectorTab(tab) {
  if (!["evidence", "missing", "file"].includes(tab)) tab = "evidence";
  state.inspectorTab = tab;
  document.querySelectorAll(".inspector-tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  document.querySelectorAll(".ins-panel").forEach(p => p.classList.toggle("active", p.id === `panel-${tab}`));
  const inspector = document.getElementById("inspector");
  if (inspector && !state.inspectorVisible) {
    state.inspectorVisible = true;
    inspector.classList.remove("hidden");
    delete inspector.dataset.autoHidden;
  }
  if (tab === "missing") renderMissingList();
  if (tab === "file") renderInspectorFile();
}
function renderInspectorFile() {
  const el = document.getElementById("ins-file");
  if (!el) return;
  const doc = state.currentDocument;
  if (!doc) {
    el.innerHTML = '<div class="ins-empty">还没有打开资料。点击左侧「文件」分组里的条目即可在这里查看。</div>';
    return;
  }
  el.innerHTML = `<div class="ins-file-card">
    <div class="ins-file-title">${esc(doc.title || doc.path || "当前资料")}</div>
    ${doc.path ? `<div class="ins-file-path selectable-text">${esc(doc.path)}</div>` : ""}
    <div class="ins-file-actions">
      <button class="rc-btn" onclick="switchView('document')">回到阅读</button>
      <button class="rc-btn" onclick="copyOpenDocumentPath()">复制路径</button>
    </div>
  </div>`;
}

function shouldIgnoreOpenClick(e) {
  if (e && e.target && e.target.closest("button,a,input,textarea,select,label")) return true;
  if (e && pointerDown && typeof e.clientX === "number" && typeof e.clientY === "number") {
    const dx = Math.abs(e.clientX - pointerDown.x);
    const dy = Math.abs(e.clientY - pointerDown.y);
    if (dx > 4 || dy > 4) return true;
  }
  const selection = window.getSelection ? window.getSelection().toString().trim() : "";
  return selection.length > 0;
}

/* ====== WebSocket ====== */
// The socket is always a global listener (empty session_id): the server then
// broadcasts every event to it, so switching or creating sessions never drops
// early progress events. Session scoping is done client-side in handleWSEvent.
function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const socket = new WebSocket(`ws://${location.host}/ws?session_id=`);
  ws = socket;
  socket.onopen = () => updateConnection(true);
  socket.onmessage = (e) => { try { handleWSEvent(JSON.parse(e.data)); } catch (_) {} };
  socket.onclose = () => { if (ws === socket) ws = null; updateConnection(false); setTimeout(connectWS, 3000); };
  socket.onerror = () => socket.close();
}
// Kept for call sites that used to re-scope the socket; the global listener
// already receives everything, so this only ensures a live connection.
function reconnectWS() {
  connectWS();
}
function updateConnection(ok) {
  const dot = document.getElementById("connection-dot"); if (dot) dot.className = "status-dot" + (ok ? " connected" : "");
  const lbl = document.getElementById("status-label"); if (lbl) lbl.textContent = ok ? "已连接" : "重连中";
}

function setSessionTitle(title) {
  const el = document.getElementById("titlebar-session");
  if (el) el.textContent = title || "新的研究会话";
}

function getSessionTitle() {
  const el = document.getElementById("titlebar-session");
  return el ? el.textContent : "";
}

function genSessionId() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

function handleWSEvent(ev) {
  // Ignore events belonging to another session than the one being viewed.
  if (ev && ev.session_id && ev.session_id !== state.currentSessionId) return;
  handleWSEventAsync(ev);
}
function stopRunningAgents(reason) {
  Object.values(state.agentRuns).forEach(a => {
    if (a.status === "running") {
      a.status = "cancelled";
      a.finishedAt = Date.now();
      a.events = a.events || [];
      a.events.push({ stage: "aborted", message: reason || "流程已中止" });
    }
  });
}
async function handleWSEventAsync(ev) {
  switch (ev.type) {
    case "agent_started": {
      state.agentRuns[ev.agent] = {
        name: ev.agent,
        status: "running",
        startedAt: Date.now(),
        events: [{ msg: ev.message || "开始..." }],
      };
      if (ev.total_stages) state.pipelineTotal = ev.total_stages;
      if (ev.stage_index) state.pipelineStage = ev.stage_index;
      state.running = true;
      updateSendBtn();
      updateRunningCard(
        (state.pipelineStage && state.pipelineTotal) ? `当前阶段 ${state.pipelineStage}/${state.pipelineTotal}` : null,
        ev.message || "准备中..."
      );
      break;
    }
    case "agent_progress": {
      const ag = ev.agent || "";
      if (!state.agentRuns[ag]) {
        state.agentRuns[ag] = { name: ag, status: "running", startedAt: Date.now(), events: [] };
      }
      state.agentRuns[ag].events.push({
        stage: ev.stage, message: ev.message,
        detail: ev.detail, elapsed_ms: ev.elapsed_ms,
      });
      updateRunningCard(null, `${ev.agent || ""}: ${ev.message || ""}`);
      break;
    }
    case "agent_completed": {
      if (state.agentRuns[ev.agent]) {
        state.agentRuns[ev.agent].status = "completed";
        state.agentRuns[ev.agent].finishedAt = Date.now();
        state.agentRuns[ev.agent].summary = ev.message;
        if (ev.data) state.agentRuns[ev.agent].data = ev.data;
      }
      if (ev.agent === "MonitorAgent" && ev.data && Array.isArray(ev.data.papers)) {
        state.missingPapers = ev.data.papers;
        await loadMissingPapers();
      }
      break;
    }
    case "agent_failed": {
      const reason = `${ev.agent || "Agent"} 失败: ${ev.error || "未知错误"}`;
      if (state.agentRuns[ev.agent]) {
        state.agentRuns[ev.agent].status = "failed";
        state.agentRuns[ev.agent].finishedAt = Date.now();
        state.agentRuns[ev.agent].error = ev.error;
        state.agentRuns[ev.agent].events.push({ msg: `失败: ${ev.error || ""}`, stage: "failed" });
      }
      if (ev.agent === "Orchestrator") stopRunningAgents(reason);
      const card = document.getElementById("running-card");
      if (card) {
        card.innerHTML = `<div class="rc-status error">${esc(ev.agent || "")} 失败: ${esc(ev.error || "")} <button onclick="copyError('${esc(ev.error||'')}')">复制错误</button> <button onclick="retryLast()">重试</button></div>`;
      }
      state.running = false;
      updateSendBtn();
      break;
    }
    case "agent_cancelled": {
      state.running = false;
      updateSendBtn();
      removeRunningCard();
      stopRunningAgents(ev.message || "任务已取消");
      const ag = ev.agent || "Orchestrator";
      state.agentRuns[ag] = state.agentRuns[ag] || { name: ag, startedAt: Date.now(), events: [] };
      state.agentRuns[ag].status = "cancelled";
      state.agentRuns[ag].finishedAt = Date.now();
      state.agentRuns[ag].events.push({ msg: ev.message || "任务已取消" });
      toast(ev.message || "任务已取消", "warning");
      break;
    }
    case "chat_delta": {
      appendStreamingDelta(ev.content || "");
      break;
    }
    case "chat_completed": {
      state.running = false;
      updateSendBtn();
      removeRunningCard();
      endStreaming({ id: ev.assistant_message_id, content: ev.content || "" });
      break;
    }
    case "chat_failed": {
      state.running = false;
      updateSendBtn();
      removeRunningCard();
      cancelStreaming();
      toast("回答失败: " + (ev.error || "未知错误"), "error");
      break;
    }
    case "report_ready": {
      state.running = false;
      updateSendBtn();
      removeRunningCard();
      state.missingPapers = Array.isArray(ev.missing) ? ev.missing : state.missingPapers;
      state.evidence = Array.isArray(ev.evidence) ? ev.evidence : state.evidence;
      state.innovationsExtracted = typeof ev.innovations_extracted === "number" ? ev.innovations_extracted : 0;
      await loadMissingPapers();
      renderEvidenceList();
      const reportPath = ev.report_path || ev.path || "";
      state.currentReport = { path: reportPath, content: ev.content || "" };
      state.messages.push({
        id: ev.assistant_message_id,
        role: "assistant",
        content: ev.content || "",
        kind: "report",
        report: {
          path: reportPath,
          papers_found: ev.papers_found,
          innovations_extracted: ev.innovations_extracted,
          missing_count: ev.missing_count,
          gaps_count: ev.gaps_count,
          provisional_gaps_count: ev.provisional_gaps_count,
          summary: ev.report_summary,
          unprocessed_count: ev.unprocessed_count,
        },
      });
      renderMessages();
      refreshReportList();
      loadWorkspaceTree();
      break;
    }
    case "ask_permission": {
      const allowed = await askPermission(ev.action || "未知操作", ev.message || "");
      // Send response back via fetch or WS
      if (ev.session_id) {
        await fetch(`/api/permissions/respond`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: ev.session_id, action: ev.action, allowed }),
        });
      }
      break;
    }
    case "warning": {
      toast(ev.message || "警告", "warning");
      break;
    }
  }
}

function copyError(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text || "").then(() => toast("已复制错误信息", ""));
  }
}

function removeRunningCard() {
  if (state.runningCardEl) { state.runningCardEl.remove(); state.runningCardEl = null; }
}

/* ====== Chat ====== */
function addMessage(role, content, extra) {
  const msg = { role, content, ...(extra || {}) };
  state.messages.push(msg);
  renderMessages();
  return msg;
}

function isReportContent(content, metadata = {}) {
  const c = String(content || "");
  return Boolean(metadata?.report_path || metadata?.report_path_rel) || /证据审计与候选空白报告/.test(c) || /空白创新点分析报告/.test(c) || (/^# .*(?:空白分析报告|研究报告|研究空白核查)/m.test(c) && /AI Reader 自动生成/.test(c));
}

function htmlToEl(html) {
  const t = document.createElement("div");
  t.innerHTML = html;
  return t.firstElementChild;
}

function reportCardHTML(meta) {
  const m = meta || {};
  const path = m.path || "";
  const noInnov = typeof m.innovations_extracted === "number" && m.innovations_extracted === 0;
  const gapsText = noInnov ? "证据不足" : (typeof m.gaps_count === "number" ? m.gaps_count : "?");
  const num = (v) => (typeof v === "number" ? v : "?");
  const openBtn = path ? `<button class="rc-btn" onclick="openReport('${esc(path)}')">查看完整报告</button>` : "";
  const chatBtn = path ? `<button class="rc-btn" onclick="startReportChatForReport('${esc(path)}')">继续追问，不生成报告</button>` : "";
  const warnHtml = noInnov ? `<div class="rc-warn">没有取得可用于分析的论文证据，本轮无法判断研究空白。</div>` : "";
  return `<div class="report-card">
    <h3>本轮研究结果</h3>
    ${m.summary?`<div class="rc-conclusion">${safeMarkdown(m.summary)}</div>`:''}
    <div class="rc-grid">
      <div>纳入资料: <strong>${num(m.papers_found)}</strong></div>
      <div>已提取证据: <strong>${num(m.innovations_extracted)}</strong></div>
      <div>研究候选: <strong>${gapsText}</strong></div>
      <div>待核实假设: <strong>${num(m.provisional_gaps_count)}</strong></div>
      <div>缺全文: <strong>${num(m.missing_count)}</strong></div>
      ${typeof m.unprocessed_count==='number'?`<div>尚未完成分析: <strong>${m.unprocessed_count}</strong></div>`:''}
    </div>
    <div class="rc-actions">${openBtn}${chatBtn}<button class="rc-btn" onclick="exportGptHandoff('${esc(path)}')">导出研究资料包</button><button class="rc-btn" onclick="switchInspectorTab('evidence')">证据</button><button class="rc-btn" onclick="switchView('missing')">缺全文论文</button></div>
    ${warnHtml}
  </div>`;
}

function legacyReportCardHTML(content, index) {
  return `<div class="report-card legacy">
    <h3>${esc((String(content).match(/^# (.+)$/m)||[])[1]||'历史研究报告')}</h3>
    <div class="rc-status">历史结果 · 本次未重新验证结论。阅读的是这条消息保存的原文。</div>
    <div class="rc-actions">
      <button class="rc-btn" onclick="readHistoricalReport(${index})">阅读这份报告</button>
      <button class="rc-btn" onclick="toggleLegacyReport(this)" aria-expanded="false">在对话中展开</button>
    </div>
    <div class="legacy-report-body" style="display:none">${safeMarkdown(content)}</div>
  </div>`;
}
async function openLatestSessionReport() {
  if (!state.currentSessionId) return toast("当前没有会话", "warning");
  try {
    const r = await fetch(`/api/sessions/${encodeURIComponent(state.currentSessionId)}/latest-report`);
    if (!r.ok) return toast("这个会话没有可打开的报告文件", "warning");
    const d = await r.json();
    if (d.path) await openReport(d.path);
  } catch (e) {
    toast(String(e), "error");
  }
}

function toggleLegacyReport(btn) {
  const card = btn.closest(".report-card");
  const body = card ? card.querySelector(".legacy-report-body") : null;
  if (body) {const open=body.style.display==='none';body.style.display=open?'block':'none';btn.textContent=open?'收起报告':'在对话中展开';btn.setAttribute('aria-expanded',String(open));}
}

function historicalPromptHTML(message) {
  let metadata = {};
  try { metadata = typeof message.metadata_json === 'string' ? JSON.parse(message.metadata_json) : (message.metadata_json || {}); } catch (_) {}
  const archived = metadata?.legacy_expanded_input;
  if (message.role !== 'user' || typeof archived !== 'string' || !archived || archived === message.content) return '';
  return `<details class="historical-prompt"><summary>查看旧版扩展内容</summary><p>这是旧记录中的扩展提示，保留用于核对。当前问题以外面的正文为准。</p><pre>${esc(archived)}</pre></details>`;
}

function renderMessages(forceBottom = false) {
  const el = document.getElementById("chat-content");
  const oldTop = el.scrollTop;
  const follow = forceBottom || el.scrollHeight - el.clientHeight - oldTop < 90;
  const welcome = document.getElementById("chat-welcome");
  el.querySelectorAll(".msg, .report-card").forEach(n => n.remove());
  if (!state.messages.length && !state.running && !state.loadingSession) {
    if (welcome) welcome.style.display = "block";
    return;
  }
  if (welcome) welcome.style.display = "none";
  state.messages.forEach((m, index) => {
    let storedMetadata = {};
    try { storedMetadata = typeof m.metadata_json === 'string' ? JSON.parse(m.metadata_json) : (m.metadata_json || {}); } catch (_) {}
    const isReport = m.kind === "report" || (m.role === "assistant" && isReportContent(m.content, storedMetadata));
    if (isReport) {
      const meta = m.report || {};
      el.appendChild(htmlToEl((meta && meta.path) ? reportCardHTML(meta) : legacyReportCardHTML(m.content, index)));
      return;
    }
    const div = document.createElement("div");
    div.className = `msg ${m.role}`;
    if (m.id) div.dataset.messageId = m.id;
    const actions = `<div class="msg-actions"><button class="msg-action-btn" data-action="copy" data-index="${index}">复制</button>${m.id ? `<details class="message-more"><summary>更多</summary>
      ${m.role === "user" ? `<button class="msg-action-btn" data-action="edit" data-message-id="${esc(m.id)}">修改</button>` : ""}
      <button class="msg-action-btn" data-action="retry" data-message-id="${esc(m.id)}">重试</button>
      <button class="msg-action-btn danger" data-action="delete" data-message-id="${esc(m.id)}">删除本轮</button>
    </details>` : ''}</div>`;
    const long = m.role === 'user' && String(m.content).length > 600;
    div.innerHTML = `<div class="msg-meta"><strong>${m.role === 'user' ? '你' : 'PaperPilot'}</strong><time>${esc(formatConversationTime(m.created_at))}</time></div><div class="msg-body${long ? ' is-collapsed' : ''}">${safeMarkdown(m.content)}</div>${long ? '<button class="message-expand" aria-expanded="false">展开完整问题</button>' : ''}${historicalPromptHTML(m)}${actions}`;
    div.querySelector('.message-expand')?.addEventListener('click', e => {
      const collapsed=div.querySelector('.msg-body').classList.toggle('is-collapsed');
      e.target.textContent=collapsed?'展开完整问题':'收起问题';e.target.setAttribute('aria-expanded',String(!collapsed));
    });
    el.appendChild(div);
  });
  el.querySelectorAll(".msg-action-btn").forEach(btn => btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const id = btn.dataset.messageId;
    if (btn.dataset.action === "copy") copyText(state.messages[Number(btn.dataset.index)].content);
    if (btn.dataset.action === "edit") editMessage(id);
    if (btn.dataset.action === "delete") deleteMessageTurn(id);
    if (btn.dataset.action === "retry") retryMessageTurn(id);
  }));
  el.scrollTop = follow ? el.scrollHeight : oldTop;
  if(typeof renderResearchChat==='function')renderResearchChat();
  updateConversationChrome();
}

function safeMarkdown(text) {
  if (!text) return "";
  const stash = [];
  const pushStash = (html) => { stash.push(html); return `\u0000${stash.length - 1}\u0000`; };

  let src = String(text);

  // Fenced code blocks (stashed before any other processing).
  src = src.replace(/```(\w*)\n?([\s\S]*?)(```|$)/g, (m, lang, code) => pushStash(`<pre><code>${esc(code)}</code></pre>`));

  // Markdown links [text](url) — stashed so the URL is not double-escaped.
  src = src.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+|[#\/][^\s)]*)\)/g,
    (m, label, url) => pushStash(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`));

  // Bare http(s) URLs — autolink.
  src = src.replace(/(https?:\/\/[^\s<"'()[\]{},;]+)/g,
    (m) => pushStash(`<a href="${esc(m)}" target="_blank" rel="noopener noreferrer">${esc(m)}</a>`));

  let out = esc(src);
  const tables = [];
  out = renderMarkdownTables(out, tables);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/\*(.+?)\*/g, "<em>$1</em>");
  out = out.replace(/^# (.+)$/gm, (m, t) => { const slug = t.replace(/\s+/g,'-').replace(/[^\w\u4e00-\u9fff-]/g,'').toLowerCase(); return `<h1 id="${esc(slug)}">${t}</h1>`; });
  out = out.replace(/^### (.+)$/gm, (m, t) => { const slug = t.replace(/\s+/g,'-').replace(/[^\w\u4e00-\u9fff-]/g,'').toLowerCase(); return `<h3 id="${esc(slug)}">${t}</h3>`; });
  out = out.replace(/^## (.+)$/gm, (m, t) => { const slug = t.replace(/\s+/g,'-').replace(/[^\w\u4e00-\u9fff-]/g,'').toLowerCase(); return `<h2 id="${esc(slug)}">${t}</h2>`; });

  // Blockquote (">" is escaped to "&gt;" by esc).
  out = out.replace(/^&gt; ?(.+)$/gm, "<blockquote>$1</blockquote>");

  // Ordered lists (1. / 2) ...) and unordered lists (- / *).
  out = out.replace(/^\d+[.)] (.+)$/gm, '<li class="ordered">$1</li>');
  out = out.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
  out = out.replace(/((?:<li class="ordered">.*?<\/li>\n?)+)/g, (m) => `<ol>${m.replace(/\n/g, "")}</ol>`);
  out = out.replace(/((?:<li>(?! class="ordered">).*?<\/li>\n?)+)/g, (m) => `<ul>${m.replace(/\n/g, "")}</ul>`);

  out = out.replace(/\n\n/g, "</p><p>");
  out = out.replace(/\n/g, "<br>");
  out = out.replace(/(<\/(?:blockquote|h[1-6]|ul|ol|pre)>)(?:<br>|<\/p><p>)+(?=<(?:blockquote|h[1-6]|ul|ol|pre))/g, '$1');
  if (!out.startsWith("<")) out = "<p>" + out + "</p>";
  tables.forEach((html, i) => { out = out.replace(`@@TABLE_${i}@@`, html); });
  stash.forEach((html, i) => { out = out.replace(`\u0000${i}\u0000`, html); });
  return out;
}
function renderMarkdownTables(text, tables) {
  const lines = text.split("\n");
  const result = [];
  const isSep = line => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const isRow = line => line.includes("|") && !/^\s*```/.test(line);
  const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
  for (let i = 0; i < lines.length; i++) {
    if (isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const header = cells(lines[i]);
      i += 2;
      const rows = [];
      while (i < lines.length && isRow(lines[i]) && !isSep(lines[i])) {
        rows.push(cells(lines[i]));
        i++;
      }
      i--;
      const html = `<div class="md-table-wrap"><table class="md-table"><thead><tr>${header.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${header.map((_,idx)=>`<td>${r[idx]||""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      const key = tables.length;
      tables.push(html);
      result.push(`@@TABLE_${key}@@`);
    } else {
      result.push(lines[i]);
    }
  }
  return result.join("\n");
}
function esc(text) {
  const map = { "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;" };
  return String(text).replace(/[&<>"']/g, c => map[c]);
}
function useExample(idx) {
  const inp = document.getElementById("user-input");
  if (inp) { inp.value = EXAMPLES[idx] || ""; inp.focus(); updateTokenCount(); saveConversationDraft(); resizeComposer(); updateSendBtn(); }
}

/* ====== Send ====== */
async function sendMessage() {
  const input = document.getElementById("user-input");
  const msg = input.value.trim();
  if (!msg || state.running || state.sending || state.loadingSession) return;
  if(state.chatMode==='report_chat' && state.selectedPaperIds.size>8){
    toast('讨论结果最多附带 8 篇片段；核查研究方向或补检索请切换「研究 Agent」。','warning');return;
  }
  state.sending = true; updateSendBtn();
  try {
    const cfg = await (await fetch('/api/settings')).json();
    if (!cfg.llm.has_key) { showSettings(); toast('先连接你自己的模型 API，再开始分析。', ''); return; }
  } catch (_) { toast('本地服务不可用，请重新打开应用。','error'); return; }
  finally { state.sending = false; updateSendBtn(); }

  const userMsg = addMessage("user", msg);
  input.value = "";
  saveConversationDraft(); resizeComposer(); updateTokenCount(); renderMessages(true);
  const reportChat = state.chatMode === "report_chat";
  state.running = true;
  state.agentRuns = {};
  state.pipelineStage = 0;
  state.pipelineTotal = 0;
  if (!reportChat) {
    state.evidence = [];
    state.missingPapers = [];
    state.innovationsExtracted = 0;
  }
  // Assign the session id up front so the run can be cancelled immediately and
  // every progress event is already scoped to this session.
  let createdSession = false;
  if (!state.currentSessionId) {
    state.currentSessionId = genSessionId();
    createdSession = true;
    rememberSession(state.currentSessionId);
  }
  updateSendBtn();
  showRunningCard(reportChat ? "正在基于当前会话上下文回答" : msg, reportChat ? "只对话，不生成报告" : "正在分析你的研究方向");

  try {
    const selectedIds = [...state.selectedPaperIds];
    const body = reportChat
      ? { message: msg, mode: "report_chat", selected_paper_ids: selectedIds, session_id: state.currentSessionId }
      : { message: msg, selected_paper_ids: selectedIds, mode: "analysis", session_id: state.currentSessionId };
    if (reportChat && state.reportChatPath) body.report_path = state.reportChatPath;
    const requestKey = genSessionId();
    const resp = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json", "Idempotency-Key":requestKey}, body:JSON.stringify(body) });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "发送失败");
    }
    const data = await resp.json();
    if (data.message_id) userMsg.id = data.message_id;
    if (data.job_id && !reportChat && typeof attachResearchChat === 'function') {
      await attachResearchChat(state.currentSessionId, data.job_id);
    }
    if (data.mode === "report_chat") {
      // Streaming: the answer arrives via WS chat_delta / chat_completed.
      if (selectedIds.length) {
        state.selectedPaperIds.clear();
        state.selectedPapersById.clear();
        updateLibrarySelectionUI();
        renderLibraryTable();
        toast("本次论文/PDF上下文已发送，已自动清空选择篮子", "");
      }
      if (createdSession) {
        setSessionTitle("报告问答");
        await loadSessions();
      }
      return;
    }
    if (selectedIds.length) {
      state.selectedPaperIds.clear();
      state.selectedPapersById.clear();
      updateLibrarySelectionUI();
      renderLibraryTable();
      toast("本次论文上下文已发送，已自动清空选择篮子", "");
    }
    if (createdSession) {
      setSessionTitle(msg.slice(0, 40));
      await loadSessions();
    }
  } catch (e) {
    removeRunningCard();
    addMessage("assistant", `发送失败: ${e}`);
    state.running = false;
    if (createdSession) { state.currentSessionId = null; rememberSession(null); }
    updateSendBtn();
  }
}

function showRunningCard(msg, title) {
  removeRunningCard();
  if(state.chatMode!=='report_chat')return; // Research progress lives in the conversation timeline.
  const el = document.getElementById("chat-content");
  const card = document.createElement("div");
  card.id = "running-card";
  card.className = "report-card";
  card.style.borderColor = "var(--accent-blue)";
  card.innerHTML = `<h3>${esc(title || "正在分析你的研究方向")}</h3>
    <div class="rc-stage" id="running-stage">当前阶段：初始化</div>
    <div class="rc-status" id="running-status">${esc(msg || "准备中...")}</div>`;
  el.appendChild(card);
  state.runningCardEl = card;
}

function updateRunningCard(stageText, statusText) {
  const st = document.getElementById("running-stage");
  const su = document.getElementById("running-status");
  if (stageText != null && st) st.textContent = stageText;
  if (statusText != null && su) su.textContent = statusText;
}

function beginStreaming() {
  removeRunningCard();
  state.streaming = true;
  state.streamingContent = "";
  const el = document.getElementById("chat-content");
  const welcome = document.getElementById("chat-welcome");
  if (welcome) welcome.style.display = "none";
  const div = document.createElement("div");
  div.id = "streaming-msg";
  div.className = "msg assistant streaming";
  div.innerHTML = `<div class="msg-body"></div><span class="streaming-cursor">▍</span>`;
  el.appendChild(div);
  state.streamingEl = div;
}
function appendStreamingDelta(delta) {
  if (!state.streaming) beginStreaming();
  const el = document.getElementById("chat-content");
  const follow = el.scrollHeight - el.clientHeight - el.scrollTop < 90;
  state.streamingContent += (delta || "");
  const body = state.streamingEl ? state.streamingEl.querySelector(".msg-body") : null;
  if (body) body.innerHTML = safeMarkdown(state.streamingContent);
  if (follow) el.scrollTop = el.scrollHeight;
  updateJumpToLatest();
}
function endStreaming(meta) {
  state.streaming = false;
  state.streamingContent = "";
  if (state.streamingEl) { state.streamingEl.remove(); state.streamingEl = null; }
  state.messages.push({ id: meta.id, role: "assistant", content: meta.content });
  renderMessages();
}
function cancelStreaming() {
  state.streaming = false;
  state.streamingContent = "";
  if (state.streamingEl) { state.streamingEl.remove(); state.streamingEl = null; }
}

function updateSendBtn() {
  const btn = document.getElementById("send-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  if (!btn) return;
  btn.disabled = Boolean(state.sending || state.loadingSession || !document.getElementById('user-input').value.trim());
  btn.style.display = state.running ? "none" : "";
  if (cancelBtn) cancelBtn.style.display = state.running ? "" : "none";
  const mode=document.getElementById('chat-mode-btn');if(mode)mode.disabled=Boolean(state.running||state.sending||state.loadingSession);
  updateConversationChrome();
}
async function cancelTask() {
  if (!state.currentSessionId) return;
  try {
    const resp = await fetch(`/api/tasks/${state.currentSessionId}/cancel`, { method: "POST" });
    const data = await resp.json();
    toast(data.status === "cancel_requested" ? "已请求取消任务" : "当前没有运行中的任务", "warning");
  } catch (e) {
    toast("取消失败: " + e, "error");
  }
}

// Permission modal
let _permissionResolve = null;
function askPermission(action, message) {
  return new Promise((resolve) => {
    _permissionResolve = resolve;
    document.getElementById("perm-title").textContent = `权限确认: ${action}`;
    document.getElementById("perm-message").textContent = message || `系统需要执行 "${action}" 操作，是否允许？`;
    document.getElementById("permission-modal").style.display = "flex";
  });
}
function respondPermission(allowed) {
  document.getElementById("permission-modal").style.display = "none";
  if (_permissionResolve) {
    _permissionResolve(allowed);
    _permissionResolve = null;
  }
}

function retryLast() {
  const lastMsg = [...state.messages].reverse().find(m => m.role === "user");
  if (lastMsg) {
    document.getElementById("user-input").value = lastMsg.content;
    sendMessage();
  }
}

function messageIndexById(id) {
  return state.messages.findIndex(m => m.id === id);
}
function userMessageForTurn(id) {
  const idx = messageIndexById(id);
  if (idx < 0) return null;
  if (state.messages[idx].role === "user") return state.messages[idx];
  for (let i = idx; i >= 0; i--) {
    if (state.messages[i].role === "user") return state.messages[i];
  }
  return null;
}
async function editMessage(id) {
  if (!id || state.running) return;
  const idx = messageIndexById(id);
  if (idx < 0) return toast("消息不存在", "warning");
  const next = prompt("修改这条消息：", state.messages[idx].content || "");
  if (next === null) return;
  const content = next.trim();
  if (!content) return toast("消息不能为空", "warning");
  try {
    const resp = await fetch(`/api/messages/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "修改失败");
    }
    const data = await resp.json();
    const edited = data.message?.content || content;
    state.messages[idx].content = edited;
    const isUserTurn = state.messages[idx].role === "user";
    renderMessages();
    if (isUserTurn) {
      if (confirm("内容已保存。是否删掉这一轮的旧回答，并用新内容重新运行？")) {
        await rerunEditedTurn(id, edited);
        return;
      }
      toast("消息已修改（未重新运行）", "");
      return;
    }
    toast("消息已修改", "");
  } catch (e) {
    toast(String(e), "error");
  }
}
async function rerunEditedTurn(userMessageId, newText) {
  const ok = await deleteMessageTurn(userMessageId, true);
  if (!ok) return;
  const input = document.getElementById("user-input");
  if (!input) return;
  input.value = newText;
  updateTokenCount();
  await sendMessage();
}
async function deleteMessageTurn(id, silent) {
  if (!id || state.running) return false;
  if (!silent && !confirm("删除这一轮对话？报告文件和论文库不会被删除。")) return false;
  try {
    const resp = await fetch(`/api/messages/${encodeURIComponent(id)}/turn`, { method: "DELETE" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "删除失败");
    }
    const data = await resp.json();
    const deleted = new Set(data.deleted_message_ids || []);
    state.messages = state.messages.filter(m => !deleted.has(m.id));
    renderMessages();
    if (!silent) toast("这一轮对话已删除", "warning");
    return true;
  } catch (e) {
    toast(String(e), "error");
    return false;
  }
}
async function retryMessageTurn(id) {
  if (!id || state.running) return;
  const userMsg = userMessageForTurn(id);
  if (!userMsg) return toast("找不到这一轮的用户问题", "warning");
  if (!confirm("删除这一轮并重新发送同一个问题？")) return;
  const text = userMsg.content || "";
  const ok = await deleteMessageTurn(id, true);
  if (!ok) return;
  const input = document.getElementById("user-input");
  if (input) {
    input.value = text;
    updateTokenCount();
    await sendMessage();
  }
}

/* ====== Sessions ====== */
async function loadSessions() {
  try { const r = await fetch("/api/sessions"); const d = await r.json(); state.sessions = d.sessions || []; renderSessions(); } catch (e) {}
}
function renderSessions() {
  const el = document.getElementById("session-list");
  if (!el) return;
  if (!state.sessions.length) { el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:4px 10px;">暂无会话</div>'; return; }
  const query=(document.getElementById('session-search')?.value||'').trim().toLowerCase();
  const sessions=state.sessions.filter(s=>(s.title||'').toLowerCase().includes(query));
  if (!sessions.length) { el.innerHTML='<p class="session-empty">没有匹配的会话</p>'; return; }
  el.innerHTML = sessions.map(s => {
    const active = s.id === state.currentSessionId ? " active" : "";
    const isChat = (s.title || "").startsWith("只对话");
    const badge = isChat ? '<span class="session-badge">问答</span>' : "";
    const displayTitle = (s.title || s.id).replace(/^[>\s]+/, '').replace(/^(只对话|研究问题)[:：]?\s*/, '');
    return `<div class="session-item${active}">
      <button class="session-open" onclick="loadSession('${esc(s.id)}')" title="${esc(s.title||'')}" ${active ? 'aria-current="true"' : ''}><span class="session-title">${badge}${esc(displayTitle || '未命名对话')}</span><span class="session-date">${esc(formatConversationTime(s.created_at))}</span></button>
      <button class="session-del-btn" onclick="deleteSession('${esc(s.id)}')" aria-label="删除会话" title="删除会话">&times;</button>
    </div>`;
  }).join("");
}
function clearChatDynamicContent() {
  const el = document.getElementById("chat-content");
  if (!el) return;
  el.querySelectorAll(".msg, .report-card").forEach(n => n.remove());
  const welcome = document.getElementById("chat-welcome");
  if (welcome) welcome.style.display = "block";
}
async function loadSession(sid) {
  if (state.sending || state.running) {
    toast("当前任务仍在运行，请先取消或等待完成", "warning");
    return;
  }
  saveConversationDraft();
  const loadId = state.sessionLoadId = (state.sessionLoadId || 0) + 1;
  state.loadingSession = true;
  state.currentSessionId = sid;
  restoreConversationDraft();
  rememberSession(sid);
  state.messages = []; state.agentRuns = {}; state.evidence = [];
  state.running = false;
  exitReportChatMode(true);
  state.chatMode = 'analysis'; updateLibrarySelectionUI();
  removeRunningCard();
  cancelStreaming();
  clearChatDynamicContent();
  const session = state.sessions.find(s => s.id === sid);
  setSessionTitle(session ? (session.title || sid).slice(0, 40) : sid.slice(0, 40));
  renderMessages(); renderSessions(); updateSendBtn(); switchView("chat");
  updateConversationChrome();
  try {
    const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}/messages`);
    if (!r.ok) throw new Error("会话消息加载失败");
    const d = await r.json();
    if (loadId !== state.sessionLoadId) return;
    state.messages = (d.messages || []).map(m => {
      let kind, report;
      try {
        const meta = m.metadata_json ? JSON.parse(m.metadata_json) : null;
        if (meta && meta.kind === "report") {
          kind = "report";
          report = {
            path: meta.report_path || "",
            papers_found: meta.papers_found,
            innovations_extracted: meta.innovations_extracted,
            missing_count: meta.missing_count,
            gaps_count: meta.gaps_count,
            provisional_gaps_count: meta.provisional_gaps_count,
            summary: meta.report_summary,
            unprocessed_count: meta.unprocessed_count,
          };
        }
      } catch (_) {}
      return {
        id: m.id,
        role: m.role,
        content: m.content,
        metadata_json: m.metadata_json,
        created_at: m.created_at,
        kind,
        report,
      };
    });
    const latestAssistant = [...state.messages].reverse().find(m => m.role === 'assistant');
    let latestMode = 'analysis';
    try { if (JSON.parse(latestAssistant?.metadata_json || '{}').mode === 'report_chat') latestMode = 'report_chat'; } catch (_) {}
    state.chatMode = latestMode;
    updateLibrarySelectionUI();
    state.loadingSession = false;
    renderMessages(true);
    reconnectWS();
    if(typeof attachResearchChat==='function')await attachResearchChat(sid);
  } catch (e) {
    if (loadId === state.sessionLoadId) toast(String(e), "error");
  } finally {
    if (loadId === state.sessionLoadId) { state.loadingSession = false; updateSendBtn(); updateConversationChrome(); }
  }
}
async function deleteSession(sid) {
  if (!confirm("确定删除这个会话吗？论文库和报告文件不会被删除。")) return;
  try {
    const r = await fetch(`/api/sessions/${sid}`, { method: "DELETE" });
    if (!r.ok) { const err = await r.json(); toast(err.detail || "删除失败", "error"); return; }
    if (sid === state.currentSessionId) { newResearch(); }
    await loadSessions();
    toast("会话已删除", "");
  } catch (e) { toast("删除失败: " + e, "error"); }
}

/* ====== Sidebar / Tree / API ====== */
async function loadWorkspace() { try { const r = await fetch("/api/workspace"); state.workspace = await r.json(); loadWorkspaceTree(); } catch (e) {} }
async function loadWorkspaceTree() {
  try { const r = await fetch("/api/workspace/tree"); const d = await r.json(); state.files = d.items || []; renderFileTree();
    const reps = state.files.filter(f => f.path.startsWith("reports/") && f.type === "file");
    document.getElementById("reports-count").textContent = reps.length;
    loadPapers();
  } catch (e) {}
}
function renderFileTree() {
  const el = document.getElementById("file-tree"); if (!el) return;
  const items = (state.files || []).filter(shouldShowWorkspaceFile);
  if (!items.length) { el.innerHTML = '<div class="file-empty">暂无可读文件。点击“生成说明”创建工作区导览。</div>'; return; }
  const groups = workspaceFileGroups();
  const byKey = Object.fromEntries(groups.map(g => [g.key, []]));
  items.forEach(item => {
    const key = workspaceSectionForPath(item.path);
    (byKey[key] || byKey.other).push(item);
  });
  el.innerHTML = groups.map(group => {
    const files = (byKey[group.key] || []).sort((a,b) => a.path.localeCompare(b.path));
    if (!files.length && group.key !== "guide" && group.key !== "exports") return "";
    const fileHtml = files.length ? files.map(item => `<div class="tree-node file human-file" data-path="${esc(item.path)}"><span>${workspaceFileIcon(item.path)}</span><span class="human-file-label">${esc(workspaceFileLabel(item.path))}</span></div>`).join("") : `<div class="file-empty small">${esc(group.empty || "暂无")}</div>`;
    return `<div class="file-group"><div class="file-group-title">${group.title}<span>${files.length}</span></div><div class="file-group-desc">${group.desc}</div>${fileHtml}</div>`;
  }).join("") || '<div class="file-empty">暂无可读文件。</div>';
  el.querySelectorAll(".tree-node.file[data-path]").forEach(n => n.addEventListener("click", () => openWorkspaceFile(n.dataset.path)));
}
function workspaceFileGroups() {
  return [
    { key:"guide", title:"工作区说明", desc:"给人看的目录地图和使用建议。", empty:"点击上方“生成说明”。" },
    { key:"reports", title:"研究报告", desc:"最终 Markdown 报告，适合阅读和重命名。" },
    { key:"exports", title:"研究资料包", desc:"用于继续讨论或交给其他模型复核。", empty:"点击“导出研究资料包”。" },
    { key:"manual", title:"本地PDF", desc:"你手动上传或绑定的 PDF。" },
    { key:"parsed", title:"解析全文", desc:"只显示 full.md / PDF；隐藏图片和 JSON 中间产物。" },
    { key:"notes", title:"领域先验笔记", desc:"历史或自行导入的背景笔记，不作为论文原文证据。" },
    { key:"config", title:"配置", desc:"工作区配置和轻量索引。" },
    { key:"other", title:"其他", desc:"未归类但可能有用的文件。" },
  ];
}
function shouldShowWorkspaceFile(item) {
  if (!item || item.type !== "file" || !item.path) return false;
  const p = item.path.replace(/\\/g, "/");
  if (p.startsWith("db/") || p.startsWith(".agent_history/") || p.startsWith(".")) return false;
  if (p === ".paper_tracker.json") return false;
  if (p.startsWith("papers/parsed/")) {
    if (/\/images\//.test(p)) return false;
    if (/\.(jpg|jpeg|png|json)$/i.test(p) && !p.endsWith("import_summary.json")) return false;
    return p.endsWith("/full.md") || /\.pdf$/i.test(p) || /\.md$/i.test(p);
  }
  return true;
}
function workspaceSectionForPath(path) {
  const p = (path || "").replace(/\\/g, "/");
  if (p === "00_WORKSPACE_GUIDE.md") return "guide";
  if (p.startsWith("reports/")) return "reports";
  if (p.startsWith("exports/")) return "exports";
  if (p.startsWith("papers/manual/") || (p.startsWith("papers/") && /\.pdf$/i.test(p) && !p.startsWith("papers/parsed/"))) return "manual";
  if (p.startsWith("papers/parsed/")) return "parsed";
  if (p.startsWith("notes/")) return "notes";
  if (p === "config.yaml" || p.endsWith("import_summary.json")) return "config";
  return "other";
}
function workspaceFileIcon(path) {
  const key = workspaceSectionForPath(path);
  if (key === "reports") return "R";
  if (key === "exports") return "G";
  if (key === "manual") return "PDF";
  if (key === "parsed") return "MD";
  if (key === "notes") return "N";
  if (key === "config") return "CFG";
  if (key === "guide") return "?";
  return "F";
}
function workspaceFileLabel(path) {
  const p = (path || "").replace(/\\/g, "/");
  if (p === "00_WORKSPACE_GUIDE.md") return "工作区说明 / 00_WORKSPACE_GUIDE.md";
  if (p.startsWith("papers/parsed/") && p.endsWith("/full.md")) {
    const parts = p.split("/");
    return `${parts[2]} / full.md`;
  }
  if (p.startsWith("reports/") || p.startsWith("exports/") || p.startsWith("papers/manual/")) return p.split("/").slice(-1)[0];
  return p;
}
async function organizeWorkspace() {
  try {
    const resp = await fetch("/api/workspace/organize", { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "整理失败");
    }
    const data = await resp.json();
    await loadWorkspaceTree();
    await openWorkspaceFile(data.path);
    toast("已生成工作区说明", "");
  } catch (e) {
    toast(String(e), "error");
  }
}
function renderLibraryTable() {
  const tb = document.getElementById("paper-table-body"); if (!tb) return;
  const papers = state.papers || [];
  papers.forEach(p => { if (state.selectedPaperIds.has(p.id)) state.selectedPapersById.set(p.id, p); });
  const paperCountEl = document.getElementById("folder-papers-count");
  if (paperCountEl) paperCountEl.textContent = papers.length;
  tb.innerHTML = papers.length ? papers.map(p=>paperRowHTML(p)).join("") : `<tr><td colspan="8" class="lib-empty"><div class="library-empty-state">${readerIcon('book')}<h2>${document.getElementById('library-search').value.trim()?'没有匹配的论文':'把第一篇论文放进来'}</h2><p>导入手头的 PDF，或从研究问题开始检索。<br>本地解析适用于可选中文字的 PDF；扫描件可能需要额外解析服务。</p><div class="welcome-actions"><button class="welcome-primary" onclick="uploadPaper()">导入 PDF</button><button class="welcome-secondary" onclick="switchView('chat');document.getElementById('user-input').focus()">从问题开始</button></div></div></td></tr>`;
  tb.querySelectorAll(".paper-row[data-paper-id]").forEach(row => row.addEventListener("click", (e) => {
    if (shouldIgnoreOpenClick(e)) return;
    openPaperDetail(row.dataset.paperId);
  }));
  tb.querySelectorAll(".paper-open-btn[data-paper-id]").forEach(btn => btn.addEventListener("click", (e) => { e.stopPropagation(); openPaperDetail(btn.dataset.paperId); }));
  tb.querySelectorAll(".paper-select[data-paper-id]").forEach(cb => cb.addEventListener("click", e => e.stopPropagation()));
  tb.querySelectorAll(".paper-select[data-paper-id]").forEach(cb => cb.addEventListener("change", () => togglePaperSelection(cb.dataset.paperId, cb.checked)));
  updateLibrarySelectionUI();
}
function paperStatus(p) {
  const s = p.retrieval_status || "metadata_only";
  if (p.kind === "prior_knowledge") return { cls: "parsed", text: "先验" };
  if (s === "parsed") return { cls: "parsed", text: "已解析" };
  if (s === "downloaded") return { cls: "parsed", text: "已下载" };
  if (s === "missing_fulltext") return { cls: "missing", text: "缺全文" };
  if (s === "off_topic") return { cls: "failed", text: "跑偏" };
  if (s === "failed") return { cls: "failed", text: "失败" };
  return { cls: "metadata", text: "仅元数据" };
}
function paperRowHTML(p) {
  const st = paperStatus(p);
  const authors = (p.authors || []).slice(0,3).join(", ") || "-";
  const venue = [p.venue, p.year].filter(Boolean).join(" / ") || "-";
  const added = (p.updated_at || p.created_at || "").slice(0,10) || "-";
  const title = p.title || p.id || "Untitled";
  const checked = state.selectedPaperIds.has(p.id) ? " checked" : "";
  const selected = state.selectedPaperIds.has(p.id) ? " selected" : "";
  return `<tr class="paper-row${selected}" data-paper-id="${esc(p.id)}"><td><input class="paper-select" type=checkbox data-paper-id="${esc(p.id)}"${checked}></td><td><div class="thumb-cell ${p.kind === 'prior_knowledge' ? 'prior' : ''}"></div></td><td><div class="paper-title">${esc(title)}</div><div class="paper-subtitle">${esc(p.id || '')}</div></td><td>${esc(authors)}</td><td>${esc(venue)}</td><td><span class="status-badge ${st.cls}">${st.text}</span></td><td>${esc(added)}</td><td class="lib-row-actions"><button class="paper-open-btn" data-paper-id="${esc(p.id)}">查看</button></td></tr>`;
}

function selectedPapers() {
  return [...state.selectedPaperIds].map(id => state.selectedPapersById.get(id)).filter(Boolean);
}
function updateLibrarySelectionUI() {
  const selected = selectedPapers();
  const bar = document.getElementById("library-selection-bar");
  const count = document.getElementById("library-selection-count");
  const all = document.getElementById("paper-select-all");
  if (bar) bar.hidden = selected.length === 0;
  if (count) count.textContent = `已选择 ${selected.length} 篇`;
  const badge = document.getElementById("selected-context-badge");
  if (badge) {
    if (state.chatMode === "report_chat") {
      badge.hidden = selected.length === 0;
      badge.textContent = `已选 ${selected.length} 篇`;
      badge.title = '查看本次对话选择的论文';
      badge.onclick = () => switchView('library');
      badge.classList.add("report-chat-badge");
    } else {
      badge.hidden = selected.length === 0;
      badge.textContent = selected.length ? `上下文 ${selected.length} 篇` : "";
      badge.title = "发送问题时，选中的论文会作为结构化上下文传给后端；不会改写你的问题。";
      badge.onclick = null;
      badge.classList.remove("report-chat-badge");
    }
  }
  const input = document.getElementById("user-input");
  if (input) {
    input.placeholder = state.chatMode === "report_chat"
      ? selected.length
        ? `只对话模式：将发送 ${selected.length} 篇论文/PDF作为上下文，不生成新报告...`
        : "只对话模式：基于当前会话历史和最近报告回答，不生成新报告..."
      : selected.length
      ? `已选 ${selected.length} 篇论文作为上下文。直接输入你的原问题...`
      : "在这里输入你的研究问题...";
  }
  updateChatModeButton();
  if (all) {
    const visible = state.papers || [];
    const visibleSelected = visible.filter(p => state.selectedPaperIds.has(p.id)).length;
    all.checked = visible.length > 0 && visibleSelected === visible.length;
    all.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
  }
}
function togglePaperSelection(id, checked) {
  if (!id) return;
  const paper = (state.papers || []).find(p => p.id === id);
  if (checked) {
    state.selectedPaperIds.add(id);
    if (paper) state.selectedPapersById.set(id, paper);
  } else {
    state.selectedPaperIds.delete(id);
    state.selectedPapersById.delete(id);
  }
  const row = document.querySelector(`.paper-row[data-paper-id="${CSS.escape(id)}"]`);
  if (row) row.classList.toggle("selected", checked);
  updateLibrarySelectionUI();
}
function toggleSelectAllPapers(checked) {
  (state.papers || []).forEach(p => {
    if (checked) {
      state.selectedPaperIds.add(p.id);
      state.selectedPapersById.set(p.id, p);
    } else {
      state.selectedPaperIds.delete(p.id);
      state.selectedPapersById.delete(p.id);
    }
  });
  renderLibraryTable();
}
function clearPaperSelection() {
  state.selectedPaperIds.clear();
  state.selectedPapersById.clear();
  renderLibraryTable();
}
function formatPaperCitation(p, i) {
  const authors = (p.authors || []).join(", ") || "Unknown authors";
  const venue = [p.venue, p.year].filter(Boolean).join(" / ") || "Unknown venue";
  const link = p.open_access_pdf_url || p.url || "";
  return `${i + 1}. ${p.title || p.id}\n   Authors: ${authors}\n   Venue/Year: ${venue}\n   ID: ${p.id}${link ? `\n   Link: ${link}` : ""}`;
}
function selectedBibliographyText() {
  return selectedPapers().map(formatPaperCitation).join("\n\n");
}
async function copySelectedBibliography() {
  const text = selectedBibliographyText();
  if (!text) return toast("请先选择论文", "warning");
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制选中论文题录", "");
  } catch (e) {
    toast("复制失败", "error");
  }
}
function useSelectedPapersInChat() {
  const selected = selectedPapers();
  if (!selected.length) return toast("请先选择论文", "warning");
  const input = document.getElementById("user-input");
  switchView("chat");
  if (input) input.focus();
  toast(`已将 ${selected.length} 篇论文设为下一问上下文`, "");
}
async function bulkUpdateSelectedPapers(status, missingReason) {
  const ids = selectedPapers().map(p => p.id);
  if (!ids.length) return toast("请先选择论文", "warning");
  const resp = await fetch("/api/papers/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_ids: ids, status, missing_reason: missingReason || null }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || "批量更新失败");
  }
  return resp.json();
}
async function markSelectedOffTopic() {
  if (!selectedPapers().length) return toast("请先选择论文", "warning");
  if (!confirm("把选中的论文标记为跑偏并从默认论文库隐藏？")) return;
  try {
    const result = await bulkUpdateSelectedPapers("off_topic", "user_marked_off_topic");
    state.selectedPaperIds.clear();
    state.selectedPapersById.clear();
    await loadPapers();
    toast(`已隐藏 ${result.updated || 0} 篇论文`, "warning");
  } catch (e) {
    toast(String(e), "error");
  }
}
async function loadPapers() {
  try {
    const input = document.getElementById("library-search");
    const q = input ? input.value.trim() : "";
    const r = await fetch(`/api/papers?limit=500&q=${encodeURIComponent(q)}`);
    const d = await r.json();
    state.papers = d.papers || [];
    renderLibraryTable();
  } catch (e) { toast("论文库加载失败", "error"); }
}
async function openPaperDetail(id) {
  try {
    const r = await fetch(`/api/papers/detail?paper_id=${encodeURIComponent(id)}`);
    if (!r.ok) throw new Error("论文详情无法打开");
    const d = await r.json();
    const p = d.paper || {};
    const st = paperStatus(p);
    const authors = (p.authors || []).join(", ") || "-";
    const links = [p.url ? `<a href="${esc(p.url)}" target="_blank">论文页</a>` : "", p.open_access_pdf_url ? `<a href="${esc(p.open_access_pdf_url)}" target="_blank">PDF</a>` : ""].filter(Boolean).join(" · ");
    const fulltext = p.fulltext_path ? `<div class="paper-meta-line">本地全文：<code>${esc(p.fulltext_path)}</code></div>` : "";
    const missingActions = (p.retrieval_status === "missing_fulltext" || p.retrieval_status === "metadata_only") ? `<div class="paper-detail-actions"><button class="lib-btn" onclick="uploadMissingFulltext('${esc(p.id)}')">上传 PDF</button><button class="lib-btn" onclick="markMissingPaperNotFound('${esc(p.id)}')">我也没找到</button></div>` : "";
    const profile = d.profile && Object.keys(d.profile).length ? `<h3>创新画像</h3><p>${esc(d.profile.innovation_detail || d.profile.method_subcategory || "已提取，但没有摘要字段。")}</p>` : "";
    const preview = d.preview_markdown ? `<h3>全文预览</h3><div class="paper-md-preview">${safeMarkdown(d.preview_markdown.slice(0,12000))}</div>` : "";
    openDocumentReader({
      title: p.title || p.id || "论文详情",
      path: p.id || id,
      kind: "paper",
      sourceView: "library",
      html: `<div class="paper-detail"><h2>${esc(p.title || p.id || "Untitled")}</h2><div class="paper-meta-line">${esc(authors)}</div><div class="paper-meta-line">${esc([p.venue,p.year].filter(Boolean).join(" / ") || "-")} · <span class="status-badge ${st.cls}">${st.text}</span></div>${fulltext}${links?`<div class="paper-links">${links}</div>`:""}${missingActions}<h3>摘要</h3><p>${esc(p.abstract || "暂无摘要。")}</p>${profile}${preview}</div>`,
    });
  } catch (e) { toast(String(e), "error"); }
}
async function openWorkspaceFile(path) {
  if (!path) return;
  const normalized = path.replace(/\\/g, "/");
  if (normalized.startsWith("reports/") && normalized.endsWith(".md")) {
    await openReport(normalized);
    return;
  }
  try {
    const r = await fetch(`/api/workspace/file?path=${encodeURIComponent(normalized)}`);
    if (!r.ok) throw new Error("文件无法打开");
    const d = await r.json();
    const content = d.content || "";
    const binary = content === "[binary file]";
    const displayContent = binary
      ? `# ${workspaceDocumentTitle(normalized)}\n\n这个文件是二进制文件，不能在内置 Markdown 阅读器中直接预览。\n\n文件路径：\`${normalized}\``
      : content;
    openDocumentReader({
      title: workspaceDocumentTitle(normalized),
      path: normalized,
      kind: workspaceSectionForPath(normalized),
      sourceView: state.activeView === "document" ? (state.currentDocument?.sourceView || "chat") : state.activeView,
      content: displayContent,
    });
  } catch (e) {
    toast(String(e), "error");
  }
}
function workspaceDocumentTitle(path) {
  const p = (path || "").replace(/\\/g, "/");
  const name = p.split("/").pop() || p || "资料";
  const key = workspaceSectionForPath(p);
  if (key === "exports") return `研究资料包 / ${name}`;
  if (key === "guide") return "工作区说明";
  if (key === "parsed") return `解析全文 / ${workspaceFileLabel(p)}`;
  if (key === "manual") return `本地PDF / ${name}`;
  if (key === "notes") return `领域先验 / ${name}`;
  if (key === "config") return `配置 / ${name}`;
  return name;
}
function openDocumentReader(options) {
  const opts = options || {};
  const title = opts.title || opts.path || "资料阅读";
  const sourceView = opts.sourceView || (state.activeView === "document" ? "chat" : state.activeView);
  const rendered = opts.html || (opts.kind === "report" && typeof reportReadingHTML === "function" ? reportReadingHTML(opts.content || "") : safeMarkdown(opts.content || ""));
  const kind = String(opts.kind || "file").replace(/[^a-zA-Z0-9_-]/g, "") || "file";
  state.currentDocument = { title, path: opts.path || "", content: opts.content || "", sourceView };
  switchView("document");
  const titleEl = document.getElementById("doc-title");
  const bodyEl = document.getElementById("doc-body");
  if (titleEl) titleEl.textContent = title;
  if (bodyEl) {
    const meta = opts.path ? `<div class="document-meta">${esc(opts.path)}</div>` : "";
    bodyEl.innerHTML = `<article class="document-page document-kind-${kind}">${meta}${rendered}</article>`;
    bodyEl.scrollTop = 0;
  }
  buildRenderedToc("doc-toc", "doc-body");
  renderInspectorFile();
}
function closeDocumentReader() {
  const target = state.currentDocument?.sourceView || "chat";
  state.currentDocument = null;
  switchView(target === "document" ? "chat" : target);
  renderInspectorFile();
}
function copyOpenDocumentPath() {
  const path = state.currentDocument?.path || "";
  if (!path) return toast("当前资料没有可复制路径", "warning");
  copyText(path, "已复制资料路径");
}


/* ====== Reports ====== */
function parseReportFileName(name) {
  const m = String(name || "").match(/^gap_analysis_(\d{8})_(\d{6})(?:_(.+))?\.md$/);
  if (!m) return { title: String(name || "").replace(/\.md$/, ""), date: "" };
  const date = `${m[1].slice(0,4)}-${m[1].slice(4,6)}-${m[1].slice(6,8)} ${m[2].slice(0,2)}:${m[2].slice(2,4)}`;
  const slug = m[3] ? m[3].replace(/_+/g, " ").trim() : "";
  const title = slug ? slug.replace(/\b\w/g, (c) => c.toUpperCase()) : "空白分析报告";
  return { title, date };
}

async function refreshReportList() {
  const el = document.getElementById("report-list");
  if (!el) return;
  try {
    const r = await fetch("/api/workspace/tree");
    const d = await r.json();
    const reps = (d.items || []).filter(i => i.path.startsWith("reports/") && i.type === "file" && i.path.endsWith(".md"));
    document.getElementById("reports-count").textContent = reps.length;
    el.innerHTML = reps.map(rp => {
      const parsed = parseReportFileName(rp.path.replace("reports/", ""));
      return `<div class="report-item" onclick="if(!shouldIgnoreOpenClick(event)) openReport('${esc(rp.path)}')">
        <div class="ri-left">
          <span class="ri-name" title="${esc(rp.path)}">${esc(parsed.title)}</span>
          <span class="ri-date">${esc(parsed.date)}</span>
        </div>
        <span class="report-actions" onclick="event.stopPropagation()">
          <button class="report-action-btn" onclick="renameReport('${esc(rp.path)}')">重命名</button>
          <button class="report-action-btn danger" onclick="deleteReport('${esc(rp.path)}')">删除</button>
        </span>
      </div>`;
    }).join("") || '<div class="lib-empty">暂无报告。</div>';
  } catch(e) {
    toast("报告列表加载失败", "error");
  }
}
async function openReport(path) {
  try{const r=await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);if(!r.ok){toast("报告未找到","error");return}
    const d=await r.json();switchView("reports");
    state.currentReport = { path, content: d.content || "" };
    document.getElementById("report-list").style.display="none";
    document.getElementById("report-reader").style.display="flex";
    document.getElementById("rr-title").textContent="研究报告";
    const bodyEl = document.getElementById("rr-body");
    bodyEl.innerHTML = typeof reportReadingHTML === "function" ? reportReadingHTML(d.content||"") : safeMarkdown(d.content||"");
    bodyEl.scrollTop = 0;
    // Evidence tables are available inside the reading edition appendices.
    buildToC(d.content||"");
  }catch(e){toast("打开失败","error");}
}

/* ====== Interactive innovation matrix ====== */
function extractReportMatrix(content) {
  const m = String(content || "").match(/##\s*附录：原始创新矩阵\s*```json\s*([\s\S]*?)```/);
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (_) { return null; }
}
function paperTitleById(paperId) {
  const p = (state.papers || []).find(x => x.id === paperId);
  return p ? (p.title || paperId) : "";
}
function paperById(paperId) {
  return (state.papers || []).find(x => x.id === paperId) || null;
}
function paperStatusLabel(paperId) {
  const p = paperById(paperId);
  if (!p) return "";
  const st = paperStatus(p);
  return st ? st.text : "";
}
function matrixCellLabel(cell) {
  const co = (cell && cell.coordinates) || {};
  const parts = Object.entries(co).map(([k, v]) => `${k}：${v}`);
  return parts.length ? parts.join(" · ") : "未标注组合";
}
function showMatrixCell(idx) {
  const cell = (state.matrixCells || [])[idx];
  const el = document.getElementById("matrix-detail");
  if (!cell || !el) return;
  const ids = cell.paper_ids || [];
  const papersHtml = ids.length
    ? `<div class="mx-papers">${ids.map(pid => {
        const st = paperStatusLabel(pid);
        return `<button class="mx-paper" onclick="openPaperDetail('${esc(pid)}')">${esc(paperTitleById(pid) || pid)}${st ? ` <span class="mx-status">${esc(st)}</span>` : ""}</button>`;
      }).join("")}</div>`
    : '<div class="matrix-hint">该组合暂无论文记录。</div>';
  const weakCount = ids.filter(pid => {
    const p = paperById(pid);
    return p && (p.retrieval_status === "missing_fulltext" || p.retrieval_status === "metadata_only");
  }).length;
  const weakNote = weakCount
    ? `<div class="mx-warn">其中 ${weakCount} 篇只有摘要或缺失全文，这一格的覆盖判定置信度有限，建议补全文后复核。</div>`
    : "";
  el.innerHTML = `<div class="mx-detail-head">${esc(matrixCellLabel(cell))}</div>
    <div class="mx-detail-meta">覆盖级别：<strong>${esc(cell.coverage || "unknown")}</strong> · 涉及论文 ${ids.length} 篇</div>
    ${papersHtml}
    ${weakNote}`;
}
function renderReportMatrix(content, container) {
  const matrix = extractReportMatrix(content);
  state.reportMatrix = matrix;
  state.matrixCells = matrix && Array.isArray(matrix.cells) ? matrix.cells : [];
  if (!matrix || !state.matrixCells.length || !container) return;
  const axes = Array.isArray(matrix.axes) ? matrix.axes : [];
  const cells = state.matrixCells;
  let gridHtml = "";
  if (axes.length >= 2) {
    const rowAxis = axes[0], colAxis = axes[1];
    const rows = [], cols = [];
    const byKey = new Map();
    cells.forEach((cell, idx) => {
      const co = cell.coordinates || {};
      const r = co[rowAxis] == null ? "未标注" : String(co[rowAxis]);
      const c = co[colAxis] == null ? "未标注" : String(co[colAxis]);
      if (!rows.includes(r)) rows.push(r);
      if (!cols.includes(c)) cols.push(c);
      byKey.set(r + "\u0000" + c, { cell, idx });
    });
    gridHtml = `<div class="matrix-grid-wrap"><table class="matrix-grid">
      <thead><tr><th class="mx-corner">${esc(rowAxis)} \\ ${esc(colAxis)}</th>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr><th>${esc(r)}</th>${cols.map(c => {
        const hit = byKey.get(r + "\u0000" + c);
        if (!hit) return '<td class="mx-cell mx-empty" title="该组合暂无证据记录">—</td>';
        const cov = String(hit.cell.coverage || "unknown");
        const cls = cov.replace(/[^a-zA-Z0-9_-]/g, "") || "unknown";
        return `<td class="mx-cell cov-${esc(cls)}" data-cell-idx="${hit.idx}"><span class="mx-cov">${esc(cov)}</span><span class="mx-count">${(hit.cell.paper_ids || []).length} 篇</span></td>`;
      }).join("")}</tr>`).join("")}</tbody></table></div>`;
  } else {
    gridHtml = `<div class="matrix-list">${cells.map((cell, idx) => {
      const cov = String(cell.coverage || "unknown");
      const cls = cov.replace(/[^a-zA-Z0-9_-]/g, "") || "unknown";
      return `<button class="mx-row cov-${esc(cls)}" data-cell-idx="${idx}">
        <span class="mx-cov">${esc(matrixCellLabel(cell))}</span>
        <span class="mx-count">${esc(cov)} · ${(cell.paper_ids || []).length} 篇</span></button>`;
    }).join("")}</div>`;
  }
  const card = document.createElement("div");
  card.className = "matrix-card";
  card.innerHTML = `<div class="matrix-head">
      <h3>创新覆盖矩阵（交互式）</h3>
      <span class="matrix-meta">${cells.length} 条证据记录 · 点击查看涉及论文</span>
    </div>
    ${gridHtml}
    <div class="matrix-detail" id="matrix-detail"><div class="matrix-hint">点击上方任意格子，查看该组合覆盖的论文与覆盖级别。</div></div>`;
  container.insertBefore(card, container.firstChild);
  card.querySelectorAll("[data-cell-idx]").forEach(node => {
    node.addEventListener("click", () => showMatrixCell(Number(node.dataset.cellIdx)));
  });
}
function startReportChat() {
  if (!state.currentReport || !state.currentReport.path) return toast("请先打开一份报告", "warning");
  startReportChatForReport(state.currentReport.path);
}
function startReportChatForReport(path) {
  if (!path) return toast("当前没有可追问的报告", "warning");
  state.chatMode = "report_chat";
  state.reportChatPath = path;
  state.currentReport = state.currentReport || { path, content: "" };
  state.currentReport.path = path;
  switchView("chat");
  const input = document.getElementById("user-input");
  if (input) input.focus();
  updateLibrarySelectionUI();
  toast("已进入报告对话模式，会带上当前会话历史且不生成新报告", "");
}
function toggleContextChatMode() {
  if (state.chatMode === "report_chat") {
    exitReportChatMode(false);
  } else {
    state.chatMode = "report_chat";
    state.reportChatPath = null;
    updateLibrarySelectionUI();
    toast("已切换为只对话模式：保留当前上下文，不生成新报告", "");
  }
}
function exitReportChat() {
  exitReportChatMode(false);
}
function exitReportChatMode(silent) {
  state.chatMode = "analysis";
  state.reportChatPath = null;
  updateLibrarySelectionUI();
  if (!silent) toast("已退出只对话模式", "");
}
function updateChatModeButton() {
  const btn = document.getElementById("chat-mode-btn");
  if (!btn) return;
  const contextOnly = state.chatMode === "report_chat";
  btn.textContent = contextOnly ? "讨论结果" : "研究 Agent";
  btn.classList.toggle("active", contextOnly);
  btn.title = contextOnly
    ? "讨论已有结果，不补检索或重新分析论文。点击切回研究 Agent。"
    : "检索已有实现，核查覆盖与反证，审查候选研究空白。点击切到讨论结果。";
  updateConversationChrome();
}

async function exportGptHandoff(reportPath) {
  try {
    const body = {
      session_id: state.currentSessionId || null,
      report_path: reportPath || [...state.messages].reverse().find(m=>m.kind==='report')?.report?.path || state.reportChatPath || null,
      selected_paper_ids: [...state.selectedPaperIds],
    };
    const resp = await fetch("/api/export/gpt-handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "导出失败");
    }
    const data = await resp.json();
    await loadWorkspaceTree();
    await openWorkspaceFile(data.path);
    const modal=desktopModal('研究复核资料包已生成',`<p>已导出 ${Number(data.paper_count)} 篇关联资料${data.omitted_count?'；另有 '+Number(data.omitted_count)+' 篇超过本包上限，见清单':''}。ZIP 包含可读 Markdown、完整结构化 JSON 和文件校验清单，不自动包含 PDF。</p><p>可上传给 GPT 或其他支持文件阅读的模型复核。资料含你的研究内容；分享前请检查预览。</p><p class="selectable-text">${esc(data.zip_path||data.path)}</p><div class="desktop-action-row"><a class="rc-btn" href="/api/export/download?path=${encodeURIComponent(data.zip_path||data.path)}" download>下载 ZIP 资料包</a><button class="rc-btn" id="export-preview">查看 Markdown</button></div>`);
    modal.querySelector('#export-preview').onclick=()=>{modal.remove();openWorkspaceFile(data.path);};
    toast('研究复核资料包已生成', "");
  } catch (e) {
    toast(String(e), "error");
  }
}

function buildToC(content) {
  buildRenderedToc('rr-toc', 'rr-body');
}
function buildRenderedToc(tocId, bodyId) {
  const toc = document.getElementById(tocId);
  const body = document.getElementById(bodyId);
  if (!toc || !body) return;
  const headings = Array.from(body.querySelectorAll("h1,h2,h3")).filter(h => !h.closest?.('.report-appendix'));
  toc.replaceChildren();
  headings.forEach((h, idx) => {
    // A report may also exist in a hidden chat card or another reader.
    // Bind to this heading directly; never look up a global Markdown slug.
    const id = `${bodyId}-section-${idx}`;
    h.id = id;
    const level = h.tagName === "H3" ? 3 : h.tagName === "H2" ? 2 : 1;
    const link = document.createElement('a');
    link.href = `#${id}`;
    link.textContent = h.textContent || '未命名章节';
    link.style.paddingLeft = `${(level - 1) * 12}px`;
    link.addEventListener('click', event => {
      event.preventDefault();
      let folded = h.closest?.('details');
      while (folded) { folded.open = true; folded = folded.parentElement?.closest('details'); }
      const top = h.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 16;
      const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      body.scrollTo({top: Math.max(0, top), behavior: reduced ? 'auto' : 'smooth'});
      toc.querySelectorAll('a').forEach(item => {
        item.classList.toggle('active', item === link);
        if (item === link) item.setAttribute('aria-current', 'location');
        else item.removeAttribute('aria-current');
      });
      h.tabIndex = -1;
      h.focus({preventScroll: true});
    });
    toc.appendChild(link);
  });
  if (!headings.length) toc.textContent = '无章节';
}
function closeReport() { document.getElementById("report-list").style.display="block";document.getElementById("report-reader").style.display="none";refreshReportList();switchView("reports"); }
function reportBaseName(path) { return (path || "").replace(/^reports\//, ""); }
async function renameOpenReport() {
  if (!state.currentReport || !state.currentReport.path) return toast("当前没有打开报告", "warning");
  await renameReport(state.currentReport.path);
}
async function deleteOpenReport() {
  if (!state.currentReport || !state.currentReport.path) return toast("当前没有打开报告", "warning");
  await deleteReport(state.currentReport.path);
}
async function renameReport(path) {
  const current = reportBaseName(path);
  const next = prompt("新的报告文件名:", current);
  if (!next || next.trim() === current) return;
  try {
    const resp = await fetch("/api/reports/rename", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, new_name: next.trim() }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "重命名失败");
    }
    const data = await resp.json();
    if (state.currentReport && state.currentReport.path === path) {
      state.currentReport.path = data.path;
      const title = document.getElementById("rr-title");
      if (title) title.textContent = reportBaseName(data.path);
    }
    if (state.reportChatPath === path) state.reportChatPath = data.path;
    await refreshReportList();
    loadWorkspaceTree();
    toast("报告已重命名", "");
  } catch (e) {
    toast(String(e), "error");
  }
}
async function deleteReport(path) {
  if (!confirm(`确定删除报告 ${reportBaseName(path)} 吗？`)) return;
  try {
    const resp = await fetch(`/api/reports?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "删除失败");
    }
    if (state.currentReport && state.currentReport.path === path) {
      state.currentReport = null;
      document.getElementById("report-reader").style.display = "none";
      document.getElementById("report-list").style.display = "block";
    }
    if (state.reportChatPath === path) exitReportChatMode(true);
    await refreshReportList();
    loadWorkspaceTree();
    toast("报告已删除", "warning");
  } catch (e) {
    toast(String(e), "error");
  }
}
function selectedReportText() {
  const sel = window.getSelection ? window.getSelection() : null;
  if (!sel || !sel.toString().trim()) return "";
  const body = document.getElementById("rr-body");
  if (!body) return sel.toString().trim();
  const node = sel.anchorNode;
  if (node && body.contains(node.nodeType === Node.TEXT_NODE ? node.parentElement : node)) {
    return sel.toString().trim();
  }
  return "";
}
function buildIdeaExpansionPrompt(idea) {
  const reportName = state.currentReport?.path ? reportBaseName(state.currentReport.path) : "当前报告";
  return `我从报告《${reportName}》中想到一个可能的创新点：

${idea}

请你不要只停留在当前报告已有论文里，帮我跨相关方向继续找资料，把这个创新点具体化落地。请重点完成：
1. 相关但不同方向的检索关键词，包括来源领域、目标任务、实现机制、蒸馏/不确定性/轻量化/自监督等可能相关方向；
2. 找出可以借鉴的论文、方法模块、损失函数、训练策略或实验设定；
3. 判断这些资料能怎样转化成一个针对当前研究任务、可用实验验证的改进方案；
4. 给出具体模型改法、输入输出、训练数据、损失设计、消融实验和风险点；
5. 标出哪些结论需要全文支撑，哪些只是摘要/元数据层面的低置信推断。`;
}
function expandReportIdea() {
  let idea = selectedReportText();
  if (!idea) {
    idea = prompt("输入你想从报告中继续拓展的点子:", "");
  }
  idea = (idea || "").trim();
  if (!idea) return toast("请先选中报告中的点子，或手动输入一个点子", "warning");
  const input = document.getElementById("user-input");
  if (!input) return;
  input.value = buildIdeaExpansionPrompt(idea.slice(0, 2400));
  switchView("chat");
  input.focus();
  updateTokenCount();
  toast("已生成拓展资料问题，确认后发送即可开始搜索", "");
}

/* ====== Missing ====== */
function missingPaperId(m) { return m.id || m.paper_id || ""; }
function missingReasonText(reason) {
  if (reason === "user_not_found") return "已记录：你也没找到公开全文";
  if (reason === "no_open_fulltext") return "未发现可公开下载全文";
  if (/^[A-Za-z0-9_-]+$/.test(reason||'')) return '尚无可用全文；历史分类备注需重新核查';
  return reason || "需要手动补全文";
}
function missingActionsHTML(m) {
  const id = missingPaperId(m);
  if (!id) return `<span class="missing-reason">缺少 paper_id</span>`;
  const reason = m.missing_reason || "";
  return `<div class="missing-actions"><button class="lib-btn" data-copy="${esc(m.title || '')}" onclick="copyText(this.dataset.copy, '已复制论文标题')">复制标题</button><button class="lib-btn" onclick="uploadMissingFulltext('${esc(id)}')">上传 PDF</button><button class="lib-btn" onclick="markMissingPaperNotFound('${esc(id)}')" ${reason === "user_not_found" ? "disabled" : ""}>我也没找到</button></div>`;
}
async function loadMissingPapers() {
  state.missingLoadError='';
  const loadId=state.missingLoadId=(state.missingLoadId||0)+1;
  const scope=document.getElementById('missing-scope')?.value;
  const session=scope==='session'?state.currentSessionId:null;
  try {
    const r = await fetch('/api/missing-papers'+(session?'?session_id='+encodeURIComponent(session):''));
    if (!r.ok) throw new Error("缺全文列表加载失败");
    const d = await r.json();
    if(loadId!==state.missingLoadId)return;
    state.missingPapers = d.papers || [];
    updateMissingBadges();
    renderMissingView();
    renderMissingList();
  } catch (e) {
    if(loadId!==state.missingLoadId)return;
    state.missingLoadError='缺全文列表加载失败，请刷新重试。';
    updateMissingBadges();
    renderMissingView();
    renderMissingList();
  }
}
function renderMissingView() {
  const tb = document.getElementById("missing-table-body");
  if (!tb) return;
  if(state.missingLoadError){tb.innerHTML='<tr><td colspan="4">'+esc(state.missingLoadError)+' <button class="rc-btn" onclick="loadMissingPapers()">重新加载</button></td></tr>';return;}
  tb.innerHTML = state.missingPapers.length ? state.missingPapers.map(m => `<tr><td><div class="paper-title selectable-text">${esc(m.title||'')}</div><div class="paper-subtitle selectable-text">${esc(missingPaperId(m))}</div></td><td class="selectable-text">${esc([m.venue,m.year].filter(Boolean).join(' / ') || '-')}</td><td><span class="missing-reason selectable-text">${esc(missingReasonText(m.missing_reason))}</span></td><td>${missingActionsHTML(m)}</td></tr>`).join("") : '<tr><td colspan="4" class="lib-empty">暂无缺全文论文。</td></tr>';
}
function renderMissingList() {
  const el = document.getElementById("ins-missing-list");
  if (!el) return;
  el.innerHTML = state.missingPapers.length ? state.missingPapers.map(m => `<div class="missing-item"><div class="selectable-text"><strong>${esc(m.title||'')}</strong></div><div class="selectable-text" style="color:var(--text-muted);">${esc([m.venue,m.year].filter(Boolean).join(' / ') || '-')}</div><div class="mi-action selectable-text">${esc(missingReasonText(m.missing_reason))}</div>${missingActionsHTML(m)}</div>`).join("") : '<div class="ins-empty">暂无缺全文论文。</div>';
}
async function uploadMissingFulltext(paperId) {
  if (!paperId) return;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".pdf,application/pdf";
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const resp = await fetch(`/api/papers/${encodeURIComponent(paperId)}/fulltext`, { method: "POST", body: form });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "上传失败");
      }
      const uploadedPaper = (await resp.json()).paper;
      if (uploadedPaper && uploadedPaper.id) {
        state.selectedPaperIds.add(uploadedPaper.id);
        state.selectedPapersById.set(uploadedPaper.id, uploadedPaper);
        updateLibrarySelectionUI();
      }
      state.missingPapers = state.missingPapers.filter(p => missingPaperId(p) !== paperId);
      updateMissingBadges();
      renderMissingView();
      await loadPapers();
      await loadMissingPapers();
      toast("PDF 已绑定，并已加入下一问上下文", "");
    } catch (err) {
      toast(String(err), "error");
    }
  };
  input.click();
}
async function markMissingPaperNotFound(paperId) {
  if (!paperId) return;
  try {
    const resp = await fetch("/api/papers/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_ids: [paperId], status: "missing_fulltext", missing_reason: "user_not_found" }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "反馈失败");
    }
    await loadMissingPapers();
    await loadPapers();
    toast("已记录：你也没找到公开全文", "warning");
  } catch (e) {
    toast(String(e), "error");
  }
}
function evidencePaperTitle(paperId) {
  const p = (state.papers || []).find(x => x.id === paperId);
  return p ? (p.title || paperId) : paperId;
}
function renderEvidenceList() {
  const el = document.getElementById("evidence-list");
  if (!el) return;
  if (!state.evidence.length) { el.innerHTML = '<div class="ins-empty">分析完成后将显示证据。</div>'; return; }
  el.innerHTML = state.evidence.map(ev => {
    const claim = ev.supports || ev.claim || ev.difference || "";
    const loc = [ev.section, ev.page ? `p.${ev.page}` : ""].filter(Boolean).join(" · ");
    const level = ev.evidence_level || ev.source_type || (ev.confidence != null ? `置信度 ${ev.confidence}` : "");
    return `<div class="evidence-item">
      <div class="ev-head"><strong>${esc(evidencePaperTitle(ev.paper_id))}</strong>${claim ? ` <span class="ev-claim">${esc(claim)}</span>` : ""}</div>
      ${loc ? `<div class="ev-meta">${esc(loc)}</div>` : ""}
      <div class="ev-quote">${esc(ev.quote || "")}</div>
      ${level ? `<div class="ev-level">${esc(level)}</div>` : ""}
    </div>`;
  }).join("");
}
function updateMissingBadges() { const count=state.missingPapers.length; const badge=document.getElementById("missing-badge"); const ws=document.getElementById("ws-missing-count"); if(badge){badge.textContent=count; badge.style.display=count?"flex":"none";} if(ws) ws.textContent=count; }

async function copyText(text, message) {
  if (!text) return toast("没有可复制内容", "warning");
  try {
    await navigator.clipboard.writeText(text);
    toast(message || "已复制", "");
  } catch (e) {
    toast("复制失败", "error");
  }
}

/* ====== New Research ====== */
function newResearch() {
  if (state.running || state.sending) { toast('请先等待当前任务完成或取消任务', 'warning'); return false; }
  saveConversationDraft();
  state.sessionLoadId = (state.sessionLoadId || 0) + 1;
  state.loadingSession = false;
  state.currentSessionId = null;
  restoreConversationDraft();
  rememberSession(null);
  state.messages = [];
  state.running = false;
  state.agentRuns = {};
  state.evidence = [];
  state.missingPapers = [];
  exitReportChatMode(true);
  removeRunningCard();
  cancelStreaming();
  const el = document.getElementById("chat-content");
  el.innerHTML = `<div class="chat-welcome" id="chat-welcome"><h1 class="welcome-title">PaperPilot</h1><p class="welcome-subtitle">面向研究空白核查的论文分析 Agent</p><div class="welcome-workflow"><span>检索</span><span>&rarr;</span><span>解析</span><span>&rarr;</span><span>核证</span><span>&rarr;</span><span>分析</span><span>&rarr;</span><span>报告</span></div><div class="welcome-examples"><div class="ex-label">试试这些问题：</div>${[0,1,2].map(i=>`<div class="ex-item" onclick="useExample(${i})">${i+1}. ${EXAMPLES[i]}</div>`).join("")}</div></div>`;
  setSessionTitle("新的研究会话");
  switchView("chat"); updateSendBtn(); renderEvidenceList(); renderMissingList(); updateMissingBadges();
  reconnectWS();
  renderSessions(); updateConversationChrome();
}
async function uploadPaper() {
  const input=document.createElement('input');input.type='file';input.accept='.pdf';input.multiple=true;
  input.onchange=async(e)=>{
    const files=[...e.target.files];if(!files.length)return;
    const failed=[];let imported=0;
    for(const [index,file] of files.entries()){
      toast(`正在导入 ${index+1}/${files.length}：${file.name}`,'');
      try{
        const form=new FormData();form.append('file',file);
        const resp=await fetch('/api/files/import',{method:'POST',body:form});
        if(!resp.ok)throw Error('import failed');
        const data=await resp.json();
        if(data.paper?.id){state.selectedPaperIds.add(data.paper.id);state.selectedPapersById.set(data.paper.id,data.paper);}
        imported++;
      }catch(_){failed.push(file.name);}
    }
    updateLibrarySelectionUI();loadWorkspaceTree();
    toast(`已导入 ${imported}/${files.length} 篇，已加入研究材料；输入方向或追问后，用研究 Agent 核查空白。${failed.length?' 未成功：'+failed.join('、'):''}`,failed.length?'warning':'');
  };
  input.click();
}
function toggleTheme() { document.body.classList.toggle("dark"); }

function updateTokenCount() {
  const inp = document.getElementById("user-input");
  const cnt = document.getElementById("token-count");
  if (inp && cnt) cnt.textContent = Math.ceil(inp.value.length / 2);
}

async function editSessionTitle() {
  if (!state.currentSessionId) return toast("请先开始一个会话", "");
  const title = prompt("新标题:", getSessionTitle() || "");
  if (!title) return;
  try {
    const response = await fetch(`/api/sessions/${state.currentSessionId}`, {
      method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title}),
    });
    if(!response.ok)throw new Error('保存标题失败');
    setSessionTitle(title.slice(0, 40));
    await loadSessions();updateConversationChrome();
    toast("标题已更新", "");
  } catch(e) { toast("更新失败", "error"); }
}

async function showSettings() {
  try {
    const [sr,st] = await Promise.all([fetch("/api/settings"),fetch("/api/settings/status")]);
    const cfg = await sr.json();
    const stat = await st.json();
    const statusRow = (label, key) => `<div class="settings-row"><span class="s-label">${esc(label)}</span><span class="s-value">${esc(String(stat[key] ?? "unknown"))}</span></div>`;
    const secretHint = (has) => has ? "已配置，留空则不修改" : "未配置";
    const ov = document.createElement("div"); ov.className="settings-modal";
    ov.innerHTML = `<div class="settings-box">
      <h3>设置</h3>
      <div class="settings-scroll">
        <div class="settings-group-title">连接状态</div>
        ${statusRow("DeepSeek", "deepseek")}
        ${statusRow("MinerU", "mineru")}
        ${statusRow("Semantic Scholar", "semantic_scholar")}
        ${statusRow("ChromaDB", "chromadb")}
        ${statusRow("SQLite", "sqlite")}
        <div class="settings-group-title">模型</div>
        <label class="settings-field"><span>DeepSeek API Key</span><input id="set-deepseek-key" type="password" autocomplete="off" placeholder="${esc(secretHint(cfg.llm?.has_key))}"></label>
        <label class="settings-field"><span>快速模型</span><input id="set-fast-model" type="text" value="${esc(cfg.llm?.fast_model || "")}"></label>
        <label class="settings-field"><span>推理模型</span><input id="set-reasoning-model" type="text" value="${esc(cfg.llm?.reasoning_model || "")}"></label>
        <div class="settings-group-title">MinerU 解析</div>
        <label class="settings-field"><span>MinerU Token</span><input id="set-mineru-token" type="password" autocomplete="off" placeholder="${esc(secretHint(cfg.mineru?.has_token))}"></label>
        <label class="settings-field"><span>启用 MinerU</span><input id="set-mineru-enabled" type="checkbox" ${cfg.mineru?.enabled ? "checked" : ""}></label>
        <label class="settings-field"><span>解析模型版本</span><input id="set-mineru-model" type="text" value="${esc(cfg.mineru?.model_version || "")}"></label>
        <div class="settings-group-title">检索</div>
        <label class="settings-field"><span>搜索轮数</span><input id="set-search-rounds" type="number" min="1" value="${esc(String(cfg.search?.max_rounds ?? 3))}"></label>
        <label class="settings-field"><span>每轮 Top K</span><input id="set-search-topk" type="number" min="1" value="${esc(String(cfg.search?.top_k_per_round ?? 20))}"></label>
        <label class="settings-field"><span>深度解析篇数</span><input id="set-search-deepparse" type="number" min="0" value="${esc(String(cfg.search?.deep_parse_top_k ?? 10))}"></label>
        <div class="settings-note">Embedding：${esc(cfg.embedding?.provider || "")} / ${esc(cfg.embedding?.model || "")}（改动后需重启生效）</div>
        <div class="settings-note">API Key 会写入 .env，其余选项写入 config.yaml（首次保存会留一份 .bak 备份）。</div>
      </div>
      <div class="settings-actions">
        <button class="settings-close" onclick="this.closest('.settings-modal').remove()">关闭</button>
        <button class="settings-save" onclick="saveSettings(this)">保存</button>
      </div>
    </div>`;
    document.body.appendChild(ov);
  } catch(e) { toast("无法加载设置", "error"); }
}

async function saveSettings(btn) {
  const box = btn.closest(".settings-box");
  if (!box) return;
  const val = (id) => { const el = box.querySelector("#" + id); return el ? el.value.trim() : ""; };
  const num = (id) => { const v = val(id); return v === "" ? null : Number(v); };
  const body = {
    deepseek_api_key: val("set-deepseek-key") || null,
    mineru_api_token: val("set-mineru-token") || null,
    fast_model: val("set-fast-model") || null,
    reasoning_model: val("set-reasoning-model") || null,
    mineru_model_version: val("set-mineru-model") || null,
    search_max_rounds: num("set-search-rounds"),
    search_top_k_per_round: num("set-search-topk"),
    search_deep_parse_top_k: num("set-search-deepparse"),
  };
  const enabledEl = box.querySelector("#set-mineru-enabled");
  if (enabledEl) body.mineru_enabled = enabledEl.checked;
  btn.disabled = true;
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "保存失败");
    }
    const applied = Object.keys((await resp.json()).applied || {});
    toast(applied.length ? `已保存：${applied.join("、")}` : "没有需要保存的改动", "");
    const modal = box.closest(".settings-modal");
    if (modal) modal.remove();
  } catch (e) {
    toast(String(e), "error");
  } finally {
    btn.disabled = false;
  }
}

function toast(msg, type) { let c=document.querySelector(".toast-container");if(!c){c=document.createElement("div");c.className="toast-container";document.body.appendChild(c);} const t=document.createElement("div");t.className=`toast ${type||""}`;t.textContent=msg;c.appendChild(t);setTimeout(()=>t.remove(),4000); }
document.addEventListener("pointerdown", e => { pointerDown = { x: e.clientX, y: e.clientY }; });
document.addEventListener("keydown", e => { if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229&&document.activeElement===document.getElementById("user-input")){e.preventDefault();sendMessage();}});
