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
