// Run with: node --test tests/test_conversation_ui.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function page(){
  const listeners={};const input={value:'',style:{},scrollHeight:70};
  const elements={'user-input':input,'chat-content':{scrollTop:100,scrollHeight:900,clientHeight:300},'jump-latest':{hidden:true}};
  const context=vm.createContext({console,Map,Set,URL,Date,crypto:require('node:crypto').webcrypto,
    document:{addEventListener:(name,cb)=>{(listeners[name]??=[]).push(cb);},getElementById:id=>elements[id]||null},
    localStorage:{getItem:()=>null,setItem(){},removeItem(){}},window:{},fetch:()=>{throw Error('unexpected request');}});
  context.window=context;
  for(const name of ['app.js','workbench.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/ui/static',name),'utf8'),context);
  vm.runInContext('updateTokenCount=()=>{};updateConversationChrome=()=>{};',context);
  return {context,listeners,input,elements,run:code=>vm.runInContext(code,context)};
}

test('report revision card states no new reading and keeps research counts out',()=>{
 const p=page();
 p.context.meta={path:'reports/revision.md',revision_only:true,summary:'优先比较已有方法 [1]',papers_found:60};
 const html=p.run('reportCardHTML(meta)');
 assert.match(html,/报告已修改/);
 assert.match(html,/没有重新检索或阅读论文/);
 assert.match(html,/原报告保留/);
 assert.doesNotMatch(html,/待核实假设|纳入资料|缺全文:|<strong>\?<\/strong>/);
 assert.match(html,/继续讨论或修改/);
});
test('database UTC and explicit offsets render in the chosen display timezone',()=>{
 const p=page();
 for(const value of ['2026-10-04 10:27:29','2026-10-04T10:27:29Z','2026-10-04T18:27:29+08:00']){
   p.context.timestamp=value;
   assert.equal(p.run('formatConversationTime(timestamp,"Asia/Shanghai")'),'2026-10-04 18:27');
   assert.equal(p.run('formatConversationTime(timestamp,"UTC")'),'2026-10-04 10:27');
 }
 assert.equal(p.run('formatConversationTime("2026-10-04 16:00:00.123456","Asia/Shanghai")'),'2026-10-05 00:00');
 assert.equal(p.run('formatConversationTime("2026-07-04 10:27:29","America/New_York")'),'2026-07-04 06:27');
 assert.equal(p.run('formatConversationTime("2026-01-04 10:27:29","America/New_York")'),'2026-01-04 05:27');
});

test('unzoned legacy logs and publication-only dates are not shifted',()=>{
 const p=page();
 assert.equal(p.run('formatConversationTime("2026-10-04T18:27:29","Asia/Shanghai")'),'2026-10-04 18:27');
 assert.equal(p.run('formatConversationTime("2026-10-04","Asia/Shanghai")'),'2026-10-04');
 assert.equal(p.run('formatConversationTime(null,"Asia/Shanghai")'),'');
 assert.equal(p.run('formatConversationTime("not-a-timestamp","Asia/Shanghai")'),'not-a-timestamp');
});

test('job usage distinguishes provider cache, local reuse and unknown statistics',()=>{
 const p=page();
 p.context.usage={model_requests:5,requests_with_usage:4,requests_with_cache_usage:3,prompt_tokens:1234,
   completion_tokens:987,input_cache_hit_rate:.367,local_evidence_cache_hits:6};
 const html=p.run('jobUsageHTML(usage)');
 assert.match(html,/输入 1,234 token/);assert.match(html,/36.7%/);assert.match(html,/输出 987 token/);
 assert.match(html,/本地证据复用 6 篇次/);assert.match(html,/4\/5 次/);assert.match(html,/3\/5 次/);
 p.context.usage={model_requests:1,requests_with_usage:0,requests_with_cache_usage:0,prompt_tokens:null,
   completion_tokens:null,input_cache_hit_rate:null,local_evidence_cache_hits:0};
 const unknown=p.run('jobUsageHTML(usage)');
 assert.match(unknown,/输入 未返回 token/);assert.match(unknown,/输入缓存命中 未返回/);
 assert.doesNotMatch(unknown,/0\.0%|输入 0 token/);
 assert.equal(p.run('jobUsageHTML(null)'),'');
 p.context.usage={prompt_tokens:'<script>',input_cache_hit_rate:Infinity};
 assert.doesNotMatch(p.run('jobUsageHTML(usage)'),/<script>|Infinity/);
});

test('benchmark report card shows verified values without gap candidate counts',()=>{
 const p=page();p.context.result={task_type:'benchmark_comparison',benchmark_verified_count:2,innovations_extracted:3,gaps_count:0};
 const html=p.run('reportCardHTML(result)');
 assert.match(html,/已核实数值: <strong>2/);assert.doesNotMatch(html,/研究候选|待核实假设/);
});

test('paper source links reject unsafe schemes and escape external URLs',()=>{
  const p=page();
  p.context.link={label:'<script>publisher</script>',url:'javascript:alert(1)'};
  assert.equal(p.run('paperAccessLinkHTML(link)'),'');
  p.context.link={label:'Publisher',url:'https://user:secret@example.org/paper'};
  assert.equal(p.run('paperAccessLinkHTML(link)'),'');
  p.context.link={label:'<Publisher>',url:'https://doi.org/10.123/paper?q=a&x=2'};
  const html=p.run('paperAccessLinkHTML(link)');
  assert.match(html,/target="_blank" rel="noopener noreferrer"/);
  assert.match(html,/&lt;Publisher&gt;/);assert.match(html,/&amp;x=2/);
});

test('missing list uses explicit research scope and keeps total badge across pages',async()=>{
  const p=page();p.context.URLSearchParams=URLSearchParams;
  p.elements['missing-scope']={value:'research',querySelector:()=>({disabled:false})};
  p.elements['missing-badge']={style:{}};p.elements['ws-missing-count']={};
  p.run('renderMissingView=()=>{};renderMissingList=()=>{};');
  let requested;
  p.context.fetch=async url=>{requested=url;return {ok:true,json:async()=>({papers:[{id:'paper'}],summary:{research_total:140},total:140,scope:'research'})};};
  await p.run('loadMissingPapers()');
  assert.match(requested,/scope=research/);assert.match(requested,/offset=0/);
  assert.equal(p.elements['ws-missing-count'].textContent,140);
  await p.run('state.missingOffset=100;loadMissingPapers(false)');
  assert.match(requested,/offset=100/);assert.equal(p.elements['ws-missing-count'].textContent,140);
});
test('partial answers remain visibly partial instead of appearing fully answered',()=>{
 const p=page();p.context.result={task_type:'benchmark_comparison',answer_status:'partial',benchmark_verified_count:8,innovations_extracted:18};
 assert.match(p.run('reportCardHTML(result)'),/部分结果.*尚未核实/);
});
test('legacy expanded input is escaped and collapsed without filtering ordinary user text',()=>{
  const p=page();
  const message={role:'user',content:'My original question',metadata_json:JSON.stringify({legacy_expanded_input:'<script>bad()</script>\n# Context'})};
  p.context.fixture=message;
  const html=p.run('historicalPromptHTML(fixture)');
  assert.match(html,/<details class="historical-prompt">/);
  assert.doesNotMatch(html,/<details[^>]* open|<script>|<h1/);
  assert.match(html,/&lt;script&gt;/);
  p.context.fixture={role:'user',content:'研究问题：keep this\n训练背景：also keep this'};
  assert.equal(p.run('historicalPromptHTML(fixture)'),'');
  p.context.fixture={role:'user',content:'same',metadata_json:'invalid JSON'};
  assert.equal(p.run('historicalPromptHTML(fixture)'),'');
});

test('both readers navigate within their own body despite duplicate Markdown heading IDs',()=>{
  const p=page();
  for(const prefix of ['doc','rr']){
    let focused=false,scrolled;
    const headings=[{id:'duplicate',tagName:'H2',textContent:'Repeated section',
      getBoundingClientRect:()=>({top:320}),focus:options=>{focused=options.preventScroll;}}];
    const links=[];
    p.elements[prefix+'-toc']={replaceChildren(){links.length=0;},appendChild:link=>links.push(link),querySelectorAll:()=>links};
    p.elements[prefix+'-body']={scrollTop:650,querySelectorAll:()=>headings,
      getBoundingClientRect:()=>({top:100}),scrollTo:value=>scrolled=value};
    p.elements.duplicate={scrollIntoView(){throw Error('Wrong hidden heading selected');}};
    p.context.document.createElement=()=>({style:{},classList:{toggle(){}},
      addEventListener(name,cb){this[name]=cb;},setAttribute(){},removeAttribute(){}});
    p.run(`buildRenderedToc('${prefix}-toc','${prefix}-body')`);
    let prevented=false;
    links[0].click({preventDefault(){prevented=true;}});
    assert.equal(headings[0].id,prefix+'-body-section-0');
    assert.equal(scrolled.top,854);
    assert.equal(focused,true);assert.equal(prevented,true);
  }
});

test('switching drafts keeps each session input and the unsent new question',()=>{
  const p=page();p.input.value='new draft';p.run('saveConversationDraft();state.currentSessionId="a";restoreConversationDraft()');assert.equal(p.input.value,'');
  p.input.value='draft a';p.run('saveConversationDraft();state.currentSessionId=null;restoreConversationDraft()');assert.equal(p.input.value,'new draft');
  p.run('state.currentSessionId="a";restoreConversationDraft()');assert.equal(p.input.value,'draft a');
});
test('late session response cannot overwrite the selected conversation',async()=>{
  const p=page(),pending={};p.context.fetch=url=>new Promise(resolve=>pending[url]=resolve);
  p.run('rememberSession=()=>{};exitReportChatMode=()=>{};updateLibrarySelectionUI=()=>{};removeRunningCard=()=>{};cancelStreaming=()=>{};clearChatDynamicContent=()=>{};setSessionTitle=()=>{};renderMessages=()=>{};renderSessions=()=>{};renderAgentTimeline=()=>{};updateSendBtn=()=>{};switchView=()=>{};reconnectWS=()=>{};');
  const a=p.run('loadSession("a")'),b=p.run('loadSession("b")');
  pending['/api/sessions/b/messages']({ok:true,json:async()=>({messages:[{id:'b',content:'new'}]})});await b;
  pending['/api/sessions/a/messages']({ok:true,json:async()=>({messages:[{id:'a',content:'stale'}]})});await a;
  assert.equal(p.run('state.messages[0].id'),'b');assert.equal(p.run('state.chatMode'),'analysis');
});
test('IME Enter and Shift Enter do not send a message',()=>{
  const p=page();p.context.document.activeElement=p.input;p.run('sent=0;sendMessage=()=>sent++');
  for(const props of [{isComposing:true},{keyCode:229},{shiftKey:true},{}])for(const cb of p.listeners.keydown)cb({key:'Enter',preventDefault(){},...props});
  assert.equal(p.run('sent'),1);
});
test('history reports read their own message, not the latest report file',()=>{
  const p=page();p.run('state.messages=[{content:"older report"},{content:"newer report"}];openDocumentReader=opts=>opened=opts;readHistoricalReport(0)');
  assert.equal(p.run('opened.content'),'older report');
  assert.equal(p.run('isReportContent("# 单目深度先验：空白分析报告\\nAI Reader 自动生成于 yesterday")'),true);
  assert.equal(p.run('isReportContent("我想看空白分析报告")'),false);
  assert.equal(p.run('isReportContent("# 沿极线交互：研究空白核查\\nAI Reader 自动生成格式演示")'),true);
  assert.equal(p.run('isReportContent("自定义标题",{report_path:"reports/a.md"})'),true);
});
test('discussion referencing a report stays expanded instead of becoming a historical report card',()=>{
  const p=page(),children=[];
  p.elements['chat-content'].querySelectorAll=()=>[];
  p.elements['chat-content'].appendChild=node=>children.push(node);
  p.context.document.createElement=()=>({dataset:{},querySelector:()=>null,innerHTML:''});
  p.context.discussion={id:'reply',role:'assistant',kind:'report',content:'# 研究报告\nAI Reader 自动生成\n\n直接答复：包含这项结果。',
    metadata_json:JSON.stringify({mode:'report_chat',kind:'report',report_path:'reports/existing.md'})};
  assert.equal(p.run('isReportContent(discussion.content,JSON.parse(discussion.metadata_json))'),false);
  p.run('state.messages=[discussion];renderMessages()');
  assert.equal(children.length,1);
  assert.equal(children[0].className,'msg assistant');
  assert.match(children[0].innerHTML,/直接答复：包含这项结果/);
  assert.doesNotMatch(children[0].innerHTML,/历史研究报告|在对话中展开|legacy-report-body|is-collapsed/);
});
test('live discussion completion preserves its mode even if prose contains legacy report headings',()=>{
  const p=page();
  p.run('renderMessages=()=>{};endStreaming({id:"reply",content:"# 研究报告\\nAI Reader 自动生成"})');
  assert.equal(p.run('JSON.parse(state.messages[0].metadata_json).mode'),'report_chat');
  assert.equal(p.run('isReportContent(state.messages[0].content,JSON.parse(state.messages[0].metadata_json))'),false);
});
test('streaming does not steal scroll while reading older messages',()=>{
  const p=page();p.run('state.streaming=true;state.streamingEl={querySelector:()=>({innerHTML:""})};appendStreamingDelta("hello")');
  assert.equal(p.elements['chat-content'].scrollTop,100);assert.equal(p.elements['jump-latest'].hidden,false);
});

test('batch PDF import retains successful selections when one upload fails',async()=>{
  const p=page();let fileInput;const notices=[];
  p.context.document.createElement=()=>fileInput={click(){}};
  p.context.FormData=class{append(){}};
  let calls=0;
  p.context.fetch=async()=>{const i=calls++;return {ok:i!==1,json:async()=>({paper:{id:'p'+i,title:'fixture'}})};};
  p.context.toast=(text)=>notices.push(text);
  p.run('updateLibrarySelectionUI=()=>{};loadWorkspaceTree=()=>{};');
  await p.run('uploadPaper()');assert.equal(fileInput.multiple,true);
  await fileInput.onchange({target:{files:[{name:'one.pdf'},{name:'two.pdf'},{name:'three.pdf'}]}});
  assert.deepEqual([...p.run('state.selectedPaperIds')],['p0','p2']);
  assert.match(notices.at(-1),/2\/3/);assert.match(notices.at(-1),/two.pdf/);
});

test('research session reopens in research mode and ordinary discussion keeps its mode',async()=>{
  for(const mode of ['analysis','report_chat']){
    const p=page();
    p.run('rememberSession=()=>{};exitReportChatMode=()=>{};updateLibrarySelectionUI=()=>{};removeRunningCard=()=>{};cancelStreaming=()=>{};clearChatDynamicContent=()=>{};setSessionTitle=()=>{};renderMessages=()=>{};renderSessions=()=>{};renderAgentTimeline=()=>{};updateSendBtn=()=>{};switchView=()=>{};reconnectWS=()=>{};');
    p.context.fetch=async()=>({ok:true,json:async()=>({messages:[{role:'assistant',metadata_json:JSON.stringify({mode})}]})});
    await p.run('loadSession("history")');assert.equal(p.run('state.chatMode'),mode);
  }
});

test('switching discussion back to research retains conversation and selected papers',()=>{
 const p=page();const notices=[];p.context.toast=text=>notices.push(text);
 p.run('updateLibrarySelectionUI=()=>{};state.currentSessionId="existing";state.chatMode="report_chat";state.messages=[{content:"prior discussion"}];state.selectedPaperIds.add("uploaded");exitReportChatMode(false)');
 assert.equal(p.run('state.currentSessionId'),'existing');assert.equal(p.run('state.chatMode'),'analysis');
 assert.equal(p.run('state.messages[0].content'),'prior discussion');assert.equal(p.run('state.selectedPaperIds.has("uploaded")'),true);
 assert.match(notices.at(-1),/接续本会话和已有论文/);
});
