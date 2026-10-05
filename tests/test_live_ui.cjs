const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({Map,Set,JSON,document:{},esc:text=>String(text).replaceAll('<','&lt;').replaceAll('>','&gt;'),safeMarkdown:text=>String(text).replaceAll('<','&lt;').replaceAll('>','&gt;')});
vm.runInContext(fs.readFileSync('src/ui/static/chat-research.js','utf8'),context);
test('only authored prose streams, not generic JSON fields or IDs',()=>{
 context.call={text:'{"progress_summary":"已经核对原文 <script>',kind:'research'};
 assert.equal(vm.runInContext('researchProse(call)',context),'已经核对原文 <script>');
 context.call.text='{"paper_id":"secret","key_techniques":["one","two"]}';assert.equal(vm.runInContext('researchProse(call)',context),'');
 context.call.text='{"progress_summary":"first\\nsecond"}';assert.equal(vm.runInContext('researchProse(call)',context),'first\nsecond');
});
test('adaptive actions use their supplied label in the conversation',()=>{
 vm.runInContext('var dynamicRun={job:{token:1},after:0,stages:[],calls:new Map()};reduceResearch(dynamicRun,{seq:1,body:{type:"agent_started",agent:"ResearchPlanner",label:"决定下一步"}});reduceResearch(dynamicRun,{seq:2,body:{type:"agent_completed",agent:"ResearchPlanner"}});reduceResearch(dynamicRun,{seq:3,body:{type:"agent_started",agent:"QuestionExtractor",label:"读取问题证据"}})',context);
 assert.equal(vm.runInContext('dynamicRun.stages[0].label',context),'决定下一步');
 assert.equal(vm.runInContext('dynamicRun.stages[1].label',context),'读取问题证据');
});
test('reducer deduplicates sequence and rejects previous attempts',()=>{
 vm.runInContext('var run={job:{id:"j",token:2},after:0,stages:[],calls:new Map()};reduceResearch(run,{seq:1,body:{attempt_token:2,type:"agent_started",agent:"AnalyzeAgent"}})',context);
 context.rows=[{seq:2,body:{attempt_token:1,type:'model_delta',call:1,content:'old'}},{seq:3,body:{attempt_token:2,type:'model_delta',agent:'AnalyzeAgent',call:1,content:'first'}},{seq:3,body:{attempt_token:2,type:'model_delta',agent:'AnalyzeAgent',call:1,content:'duplicate'}}];
 vm.runInContext('rows.forEach(r=>reduceResearch(run,r))',context);assert.equal(vm.runInContext('run.calls.get("1").text',context),'first');
});
test('old execution page and raw-output selector are removed',()=>{
 const index=fs.readFileSync('src/ui/static/index.html','utf8'),script=fs.readFileSync('src/ui/static/chat-research.js','utf8');
 assert.doesNotMatch(index,/data-tab="agent"|id="panel-agent"|agent-live.js/);
 assert.doesNotMatch(script,/view-agent|agent-model-call|原始输出/);assert.ok(!fs.existsSync('src/ui/static/agent-live.js'));
});
test('queued task accepts the first claimed attempt before the SSE status frame',()=>{
 vm.runInContext('var queued={job:{id:"q",token:0},after:0,stages:[],calls:new Map()};reduceResearch(queued,{seq:1,body:{attempt_token:1,type:"agent_started",agent:"AnalyzeAgent"}});reduceResearch(queued,{seq:2,body:{attempt_token:1,type:"model_delta",agent:"AnalyzeAgent",call:1,content:"first"}})',context);
 assert.equal(vm.runInContext('queued.calls.get("1").text',context),'first');
});

test('transport heartbeat does not replace active model reasoning',()=>{
 vm.runInContext('var heartbeat={job:{token:1},after:0,stages:[],calls:new Map()};reduceResearch(heartbeat,{seq:1,body:{type:"agent_started",agent:"AnalyzeAgent"}});reduceResearch(heartbeat,{seq:2,body:{type:"model_activity",agent:"AnalyzeAgent",call:1,phase:"thinking",preview:"正在核对"}});reduceResearch(heartbeat,{seq:3,body:{type:"model_activity",agent:"AnalyzeAgent",call:1,phase:"processing"}})',context);
 assert.equal(vm.runInContext('heartbeat.calls.get("1").phase',context),'thinking');
});

test('heartbeat and unchanged state do not schedule a visual update',()=>{
 vm.runInContext('var quiet={job:{status:"running",token:1},after:0,stages:[],calls:new Map()};reduceResearch(quiet,{seq:1,body:{type:"agent_started",agent:"AnalyzeAgent"}})',context);
 assert.equal(vm.runInContext('reduceResearch(quiet,{seq:2,body:{type:"worker_heartbeat",agent:"AnalyzeAgent"}})',context),false);
 assert.equal(vm.runInContext('quiet.after',context),2);
});

test('new output leaves completed stages and unchanged Markdown cached',()=>{
 let renders=0;context.safeMarkdown=text=>{renders++;return text;};
 vm.runInContext('var cachedRun={job:{id:"cached",status:"running",token:1},after:0,stages:[],calls:new Map()};reduceResearch(cachedRun,{seq:1,body:{type:"agent_started",agent:"InnovationExtractor"}});reduceResearch(cachedRun,{seq:2,body:{type:"model_delta",agent:"InnovationExtractor",call:1,content:\'{"method_summary":"old answer"}\'}});reduceResearch(cachedRun,{seq:3,body:{type:"agent_completed",agent:"InnovationExtractor"}});reduceResearch(cachedRun,{seq:4,body:{type:"agent_started",agent:"ReportGenerator"}});researchBlock(cachedRun)',context);
 const before=renders,oldHTML=vm.runInContext('cachedRun.stages[0].html',context);
 vm.runInContext('reduceResearch(cachedRun,{seq:5,body:{type:"model_activity",agent:"ReportGenerator",call:2,phase:"thinking",preview:"checking"}});researchBlock(cachedRun)',context);
 assert.equal(renders,before);assert.equal(vm.runInContext('cachedRun.stages[0].html',context),oldHTML);
});

test('incremental prose handles split escapes and ignores nested internal fields',()=>{
 const local=vm.createContext({Map,Set,JSON,document:{}});
 vm.runInContext(fs.readFileSync('src/ui/static/chat-research.js','utf8'),local);
 local.text=JSON.stringify({audit:{progress_summary:'private'},progress_summary:'中文\nquote " \\ 😀',paper_id:'hidden'});
 vm.runInContext('var splitRun={job:{token:1},after:0,stages:[],calls:new Map()};reduceResearch(splitRun,{seq:1,body:{type:"agent_started",agent:"QuestionExtractor"}})',local);
 let last='';for(let i=0;i<local.text.length;i++){
  local.chunk=local.text[i];local.seq=i+2;
  last=vm.runInContext('reduceResearch(splitRun,{seq,body:{type:"model_delta",agent:"QuestionExtractor",call:1,content:chunk}});researchProse(splitRun.calls.get("1"))',local);
  assert.doesNotMatch(last,/private|hidden/);
 }
 assert.equal(last,'中文\nquote " \\ 😀');
 assert.equal(vm.runInContext('splitRun.calls.get("1").proseScan.offset',local),local.text.length);
});

test('frame scheduling coalesces dense events without a fixed 160 ms delay',()=>{
 const frames=[];const local=vm.createContext({Map,Set,JSON,document:{getElementById:()=>null},requestAnimationFrame:fn=>{frames.push(fn);return frames.length;}});
 vm.runInContext(fs.readFileSync('src/ui/static/chat-research.js','utf8'),local);
 vm.runInContext('scheduleResearch();scheduleResearch();scheduleResearch()',local);assert.equal(frames.length,1);
 frames[0]();vm.runInContext('scheduleResearch()',local);assert.equal(frames.length,2);
});
