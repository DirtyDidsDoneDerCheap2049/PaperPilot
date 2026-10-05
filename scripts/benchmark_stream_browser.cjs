/* Local browser replay: real project renderer and DOM, no provider or user workspace. */
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.READER_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'..'),app=fs.readFileSync(path.join(root,'src/ui/static/app.js'),'utf8');
function renderer(name){
 const start=app.indexOf('function '+name+'(');assert.ok(start>=0,name);
 const end=app.indexOf('\n}',start);return app.slice(start,end+2);
}
const renderers=['esc','safeMarkdown','renderMarkdownTables'].map(renderer).join('\n');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function percentile(values,p){const a=values.slice().sort((a,b)=>a-b);return a[Math.min(a.length-1,Math.ceil(a.length*p)-1)]||0;}
async function replay(browser,script,scenario){
 let seq=0;const history=[];
 const row=body=>({seq:++seq,body:{attempt_token:1,...body}});
 if(scenario==='history'){
  history.push(row({type:'agent_started',agent:'InnovationExtractor'}));
  for(let i=0;i<9000;i++)history.push(row({type:'worker_heartbeat'}));
  history.push(row({type:'agent_completed',agent:'InnovationExtractor',fixture_done:true}));
 }
 const job={id:'fixture',token:1,session_id:'fixture',message_id:'input',mode:'analysis',status:scenario==='history'?'succeeded':'running'};
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/'){
   res.setHeader('Content-Type','text/html; charset=utf-8');res.end(`<!doctype html><meta charset="utf-8"><style>:root{--text-primary:#24342c;--text-secondary:#65736b;--bg-base:#f7f8f4;--bg-elevated:#edf1e9;--border:#d9e0d5;--accent-blue:#365e4b}body{margin:0;background:var(--bg-base);font:14px system-ui}#chat-content{height:720px;overflow:auto}</style><link rel="stylesheet" href="/stream.css"><div id="chat-content"><div class="msg user" data-message-id="input">本地流式显示验证</div></div><script>const state={currentSessionId:'fixture'};function toast(){}</script><script src="/renderer.js"></script><script src="/stream.js"></script>`);return;
  }
  if(url.pathname==='/renderer.js'){res.setHeader('Content-Type','text/javascript');res.end(renderers);return;}
  if(url.pathname==='/stream.js'){res.setHeader('Content-Type','text/javascript');res.end(fs.readFileSync(script));return;}
  if(url.pathname==='/stream.css'){res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(path.join(root,'src/ui/static/chat-research.css')));return;}
  if(url.pathname==='/api/jobs'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({jobs:[job]}));return;}
  if(url.pathname.endsWith('/events')){
   await pause(20);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({events:history.filter(r=>r.seq>Number(url.searchParams.get('after')||0)).slice(0,200)}));return;
  }
  if(url.pathname.endsWith('/stream')){
   res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
   const batch=Number(url.searchParams.get('batch')||1);
   const send=rows=>{for(let i=0;i<rows.length;i+=batch){const page=rows.slice(i,i+batch);res.write(`id: ${page.at(-1).seq}\ndata: ${JSON.stringify(batch===1?{kind:'event',row:page[0]}:{kind:'events',rows:page})}\n\n`);}};
   if(scenario==='history'){
    for(let i=0;i<history.length&&!res.destroyed;i+=200){send(history.slice(i,i+200));await pause(20);}
   }else{
    const initial=[row({type:'agent_started',agent:'ReportGenerator'})];
    for(let i=1;i<=64;i++){
     initial.push(row({type:'model_delta',agent:'ReportGenerator',call:i,presentation:'report_draft',content:'已核对的资料。'.repeat(60)}));
     initial.push(row({type:'model_activity',agent:'ReportGenerator',call:i,phase:'received'}));
    }
    initial.push(row({type:'model_activity',agent:'ReportGenerator',call:65,presentation:'report_draft',phase:'waiting'}));send(initial);
    await pause(500);
    for(let i=1;i<=40&&!res.destroyed;i++){
     send([row({type:'model_delta',agent:'ReportGenerator',call:65,presentation:'report_draft',fixture_index:i,
      content:`## 证据 ${i}\n\n[片段${String(i).padStart(3,'0')}] `+'本地固定输入，检查原文引用和方法差异。'.repeat(60)+'\n\n'})]);
     await pause(20);
    }
    send([row({type:'model_activity',agent:'ReportGenerator',call:65,phase:'received'}),row({type:'agent_completed',agent:'ReportGenerator',fixture_done:true})]);
   }
   res.write(`data: ${JSON.stringify({kind:'status',job:{...job,status:'succeeded'}})}\n\n`);res.end();return;
  }
  res.writeHead(404);res.end();
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const page=await browser.newPage({viewport:{width:1100,height:800}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(()=>{
   window.measure={start:performance.now(),arrivals:new Map(),latencies:[],seen:new Set(),first:null,markdownCalls:0,peer:null,paragraph:null,peerStable:true,paragraphStable:true,done:false};
   const original=reduceResearch;window.reduceResearch=(run,row)=>{
    if(row.body.fixture_index)measure.arrivals.set(row.body.fixture_index,performance.now());
    if(row.body.fixture_done)measure.done=true;
    return original(run,row);
   };
   const markdown=safeMarkdown;window.safeMarkdown=text=>{measure.markdownCalls++;return markdown(text);};
   const observer=new MutationObserver(()=>{
    if(measure.first===null&&document.querySelector('.research-step summary'))measure.first=performance.now()-measure.start;
    const calls=document.querySelectorAll('.research-call');
    if(calls.length>=65){
     if(!measure.peer)measure.peer=calls[0];else if(measure.peer!==calls[0])measure.peerStable=false;
     const paragraph=calls[64].querySelector('.research-prose p');
     if(paragraph){if(!measure.paragraph)measure.paragraph=paragraph;else if(!measure.paragraph.isConnected&&measure.seen.size<39)measure.paragraphStable=false;}
    }
    const text=document.querySelector('#chat-content').textContent;
    for(const [i,time] of measure.arrivals){if(!measure.seen.has(i)&&text.includes(`[片段${String(i).padStart(3,'0')}]`)){measure.seen.add(i);measure.latencies.push(performance.now()-time);}}
   });observer.observe(document.querySelector('#chat-content'),{subtree:true,childList:true,characterData:true});
   window.started=attachResearchChat('fixture');
  });
  await page.waitForFunction(()=>measure.done,{timeout:15000});await pause(350);
  if(scenario==='live'){
   // Completed stages stay lazy; opening them must still show the full final answer.
   await page.locator('.research-step summary').click();await pause(80);
  }
  const result=await page.evaluate(()=>({first_stage_dom_ms:measure.first,dom_update_samples:measure.seen.size,latencies:measure.latencies,
   markdown_calls:measure.markdownCalls,peer_node_preserved:measure.peerStable,completed_paragraph_preserved:measure.paragraphStable,
   final_contains_last:document.querySelector('#chat-content').textContent.includes('[片段040]')}));
  assert.deepEqual(errors,[]);
  if(scenario==='live')assert.ok(result.final_contains_last,'Final streamed output lost text');
  result.dom_update_median_ms=percentile(result.latencies,.5);result.dom_update_p95_ms=percentile(result.latencies,.95);delete result.latencies;
  if(scenario==='live'){
   const interaction=await page.evaluate(()=>{
    const container=document.querySelector('#chat-content'),details=document.querySelector('.research-step');
    container.scrollTop=120;const oldTop=container.scrollTop;
    const run=researchChat.jobs.get('fixture');
    reduceResearch(run,{seq:run.after+1,body:{type:'agent_progress',agent:'ReportGenerator',message:'本地交互验证'}});renderResearchChat();
    const scrollPreserved=Math.abs(container.scrollTop-oldTop)<1;
    details.querySelector('summary').click();
    reduceResearch(run,{seq:run.after+1,body:{type:'model_delta',agent:'ReportGenerator',call:65,presentation:'report_draft',content:'\n\n补充验证内容。'}});renderResearchChat();
    const closedPreserved=!details.open;
    details.querySelector('summary').click();renderResearchChat();
    return {user_scroll_preserved:scrollPreserved,user_closed_stage_preserved:closedPreserved,
     reopened_contains_tail:details.textContent.includes('补充验证内容。')};
   });
   assert.ok(interaction.user_scroll_preserved,'Reading position moved during update');
   assert.ok(interaction.user_closed_stage_preserved,'User-closed stage reopened itself');
   assert.ok(interaction.reopened_contains_tail,'Reopened stage lost the final tail');Object.assign(result,interaction);
  }
  return result;
 }finally{await page.close();await new Promise(resolve=>server.close(resolve));}
}
async function main(){
 const [before,current,output]=process.argv.slice(2);if(!before||!current)throw Error('Usage: benchmark_stream_browser.cjs before.js current.js [output.json]');
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const result={scope:'Local Chrome replay with actual project Markdown renderer. Latency is receipt-to-DOM-mutation, not paint or FPS; excludes provider and desktop WebView. History pages have fixed 20 ms transport delay.',before:{},after:{}};
  for(const [name,script] of [['before',before],['after',current]])for(const scenario of ['live','history'])result[name][scenario]=await replay(browser,path.resolve(script),scenario);
  assert.ok(result.after.live.peer_node_preserved);assert.ok(result.after.live.completed_paragraph_preserved);
  if(output){fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(result,null,2));}
  console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
