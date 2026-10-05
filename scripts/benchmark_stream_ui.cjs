/* Fixed event replay: measure script/Markdown rebuild work, not WebView FPS. */
const fs=require('node:fs'),vm=require('node:vm'),{performance}=require('node:perf_hooks');
function measure(path){
 let markdownCalls=0;
 const context=vm.createContext({Map,Set,JSON,document:{},esc:t=>String(t),safeMarkdown:t=>{markdownCalls++;return String(t).replace(/\n/g,'<br>');}});
 vm.runInContext(fs.readFileSync(path,'utf8'),context);
 context.rows=[];let seq=0;
 for(let i=0;i<30;i++){
  context.rows.push({seq:++seq,body:{type:'agent_started',agent:'QuestionExtractor'}});
  context.rows.push({seq:++seq,body:{type:'model_delta',agent:'QuestionExtractor',call:i+1,content:JSON.stringify({method_summary:'Completed evidence summary. '.repeat(50)})}});
  context.rows.push({seq:++seq,body:{type:'agent_completed',agent:'QuestionExtractor'}});
 }
 context.rows.push({seq:++seq,body:{type:'agent_started',agent:'ReportGenerator'}});
 vm.runInContext('var run={job:{id:"offline",token:1,status:"running"},after:0,stages:[],calls:new Map()};rows.forEach(row=>reduceResearch(run,row));researchBlock(run)',context);
 const initialCalls=markdownCalls,start=performance.now();
 for(let i=0;i<200;i++){
  context.row={seq:++seq,body:{type:'model_delta',agent:'ReportGenerator',call:31,presentation:'report_draft',content:'A newly checked fact with a citation.\n'}};
  vm.runInContext('reduceResearch(run,row);researchBlock(run)',context);
 }
 return {completed_stages:30,delta_updates:200,markdown_renders:markdownCalls-initialCalls,
   script_ms:Math.round((performance.now()-start)*1000)/1000,scope:'pure script replay; excludes DOM/layout/provider'};
}
if(require.main===module){
 const [baseline,current,output]=process.argv.slice(2);
 if(!baseline||!current)throw Error('Usage: node scripts/benchmark_stream_ui.cjs baseline.js current.js [output.json]');
 const result={before:measure(baseline),after:measure(current)};
 if(output)fs.writeFileSync(output,JSON.stringify(result,null,2));
 console.log(JSON.stringify(result,null,2));
}
module.exports={measure};
