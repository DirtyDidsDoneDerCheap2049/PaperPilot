const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({Map,Set,JSON,document:{},esc:text=>String(text).replaceAll('<','&lt;').replaceAll('>','&gt;'),safeMarkdown:text=>String(text).replaceAll('<','&lt;').replaceAll('>','&gt;')});
vm.runInContext(fs.readFileSync('src/ui/static/chat-research.js','utf8'),context);
test('only authored prose streams, not generic JSON fields or IDs',()=>{
 context.call={text:'{"progress_summary":"已经核对原文 <script>',kind:'research'};
 assert.equal(vm.runInContext('researchProse(call)',context),'已经核对原文 <script>');
 context.call.text='{"paper_id":"secret","key_techniques":["one","two"]}';assert.equal(vm.runInContext('researchProse(call)',context),'');
 context.call.text='{"progress_summary":"first\\nsecond"}';assert.equal(vm.runInContext('researchProse(call)',context),'first\nsecond');
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
