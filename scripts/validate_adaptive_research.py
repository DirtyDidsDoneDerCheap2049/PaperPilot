"""Run a budgeted online research acceptance check in an existing isolated workspace.

Default is credential/configuration preflight, with zero model requests. Run under
the same Windows identity that saved the desktop credentials. No key is printed.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid
import yaml
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def process_running(pid):
    if not pid:return True
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return False
        raise RuntimeError('Cannot verify the previous acceptance process')
    try:return kernel.WaitForSingleObject(handle,0)!=0
    finally:kernel.CloseHandle(handle)


def ensure_acceptance_session(state,attempt,question):
    """A checkpoint bypasses Orchestrator.run, so retain its real question in the UI."""
    sid=attempt['session_id']
    with state.db.transaction():
        state.db.execute('INSERT OR IGNORE INTO sessions(id,workspace_id,title,status) VALUES(?,?,?,?)',
            (sid,state.workspace_id,question[:80],'idle'))
        state.db.execute('INSERT OR IGNORE INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',
            (sid+'-request',sid,'user',question))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'work/preview/workspace')
    parser.add_argument('--base',type=Path,default=ROOT/'work/testing/research-acceptance-resumed')
    parser.add_argument('--question',default='现在最近一年监督双目模型的精度模型和实时性模型在sceneflow上达到的最低的EPE分别是多少？')
    parser.add_argument('--max-requests',type=int,default=100)
    parser.add_argument('--max-papers',type=int,default=40)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--rerun',action='store_true',help='Start a new attempt while retaining the cumulative request count.')
    parser.add_argument('--resume-snapshot',type=Path,help='Resume an existing research checkpoint in this isolated workspace; recheck analysis without repeating completed reads.')
    parser.add_argument('--deliver-only',action='store_true',help='Rewrite the report from a completed analysis checkpoint without new retrieval or evidence extraction.')
    args=parser.parse_args()
    if args.deliver_only and not args.resume_snapshot:raise SystemExit('--deliver-only requires an existing completed analysis checkpoint.')
    if not 1<=args.max_requests<=100 or not 1<=args.max_papers<=40:
        raise SystemExit('This acceptance command is limited to 100 requests / 40 papers.')
    base=args.base.resolve();source=args.source.resolve();workspace=base/'workspace'
    if not base.is_relative_to((ROOT/'work/testing').resolve()) or not (workspace/'db/ai_reader.db').is_file():
        raise SystemExit('An existing isolated test workspace is required; no empty demo is created.')
    for directory in (source,base,workspace):
        if directory.is_symlink() or (hasattr(directory,'is_junction') and directory.is_junction()):
            raise SystemExit('Linked workspaces are not supported.')
    from src.runtime.settings import read_secrets, atomic_write
    try:credentials=read_secrets(source)
    except OSError:
        print('Current Windows identity cannot decrypt desktop credentials. Run from your normal Windows account.')
        return 2
    env=dotenv_values(source/'.env')
    key=credentials.get('deepseek_api_key') or env.get('DEEPSEEK_API_KEY')
    source_config=yaml.safe_load((source/'config.yaml').read_text(encoding='utf-8')) or {}
    llm_config=source_config.get('llm',{})
    print(json.dumps({'api_configured':bool(key),'model':llm_config.get('reasoning_model'),
        'effort':llm_config.get('analysis_effort'),'max_requests':args.max_requests,'max_papers':args.max_papers},ensure_ascii=False))
    if not args.execute:return 0
    if not key:raise SystemExit('The source desktop workspace has no configured API key.')
    ledger=base/'adaptive-review.json'
    record=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {'model_requests':0,'attempts':[]}
    restored=None
    if args.resume_snapshot:
        checkpoint=args.resume_snapshot.resolve()
        if not checkpoint.is_relative_to((workspace/'.agent_history/research').resolve()) or not checkpoint.is_file():
            raise SystemExit('The checkpoint must belong to this existing isolated workspace.')
        if record.get('question')!=args.question:raise SystemExit('Checkpoint continuation must retain the authorized question.')
        restored=json.loads(checkpoint.read_text(encoding='utf-8'))
        if len(restored['papers'])>args.max_papers:raise SystemExit('Checkpoint paper budget exceeded.')
        if args.deliver_only and not restored.get('analysis'):raise SystemExit('This checkpoint has no completed analysis to deliver.')
    if record.get('status')=='running':
        if not args.rerun or process_running(record.get('pid')):
            raise SystemExit('An acceptance attempt is already marked running; inspect its process and checkpoint first.')
        record['attempts'][-1].update(result_status='interrupted',used_requests=record['model_requests']-record['attempts'][-1]['starting_requests'])
    sent=set(record.get('sent_paper_ids',[]))
    events=base/'adaptive-events.jsonl'
    if events.is_file():
        with events.open(encoding='utf-8') as stream:
            for line in stream:
                event=json.loads(line)
                if event.get('stage')=='paper_extract_started' and event.get('detail',{}).get('paper_id'):
                    sent.add(event['detail']['paper_id'])
    record['sent_paper_ids']=sorted(sent)
    if record.get('status')=='completed' and not args.rerun:
        print('Already completed; no repeated model requests. See '+str(ledger))
        return 0
    if record['model_requests']>=args.max_requests:raise SystemExit('The cumulative authorized request budget is exhausted.')
    prior=json.loads((base/'result.json').read_text(encoding='utf-8')) if (base/'result.json').is_file() else {}
    record['previous_acceptance_requests']=prior.get('model_calls',prior.get('model_requests'))
    record['question']=args.question
    record['authorized_requests']=args.max_requests
    record['status']='running'
    record['pid']=os.getpid()
    attempt={'session_id':'adaptive-'+uuid.uuid4().hex,'starting_requests':record['model_requests']}
    if restored:attempt['resume_source']=checkpoint.relative_to(ROOT).as_posix()
    if args.deliver_only:attempt['delivery_only']=True
    record['attempts'].append(attempt)
    def save():atomic_write(ledger,json.dumps(record,ensure_ascii=False,indent=2))
    save()
    os.environ['READER_STORAGE']='sqlite'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    async def run():
        from src.app.factory import build_app
        from src.agents.base import AgentContext
        state=None
        try:
            app,config=build_app(workspace)
            state=app.state
            state.llm.configure(api_key=key,**{k:llm_config[k] for k in ('base_url','fast_model','reasoning_model','max_output_tokens',
                'timeout_seconds','regular_effort','analysis_effort','capability_profile','token_parameter','json_output','extra_body_params') if k in llm_config})
            state.llm.max_calls=args.max_requests-record['model_requests']
            config['research']={**config.get('research',{}),'max_new_papers':args.max_papers,'max_agent_steps':18}
            config['search']={**config.get('search',{}),'deep_parse_top_k':args.max_papers,'max_rounds':6}
            config['mineru']={**config.get('mineru',{}),'enabled':False}
            current={'agent':'Orchestrator'}
            async def event(data):
                if data.get('stage')=='paper_extract_started':
                    pid=data.get('detail',{}).get('paper_id')
                    if pid:
                        updated=set(record['sent_paper_ids'])|{pid}
                        if len(updated)>args.max_papers:raise RuntimeError('The cumulative authorized paper budget is exhausted')
                        record['sent_paper_ids']=sorted(updated);save()
                with (base/'adaptive-events.jsonl').open('a',encoding='utf-8') as out:
                    out.write(json.dumps(dict(data,session_id=attempt['session_id']),ensure_ascii=False)+'\n')
                if data.get('type')=='agent_started':current['agent']=data['agent']
                if data.get('type') not in {'model_delta','model_activity'}:
                    print(data.get('type'),data.get('agent'),data.get('message',''),flush=True)
            async def activity(data):
                record['model_requests']=attempt['starting_requests']+state.llm.calls
                save()
                if data.get('phase')=='waiting':print('model request',record['model_requests'],current['agent'],flush=True)
                await event({'type':'model_delta' if data.get('phase')=='delta' else 'model_activity','agent':current['agent'],**data})
            async def broadcast(sid,data):await event(data)
            state.llm.activity_callback=activity
            state.orchestrator.ws_manager=type('Events',(),{'broadcast':staticmethod(broadcast)})()
            ctx=AgentContext(workspace_root=workspace,workspace_id=state.workspace_id,session_id=attempt['session_id'],config=config)
            async def research():
                if restored:
                    from src.analysis.adaptive_research import execute_research,deliver_research
                    ensure_acceptance_session(state,attempt,args.question)
                    direction=restored['direction']
                    if args.deliver_only:
                        return await deliver_research(state.orchestrator,ctx,direction,restored['papers'],restored['innovations'],
                            restored['analysis'],restored['parse_data'],[],None)
                    papers,profiles,analysis,parsed=await execute_research(state.orchestrator,ctx,direction,[],resume_snapshot=restored)
                    return await deliver_research(state.orchestrator,ctx,direction,papers,profiles,analysis,parsed,[],None)
                return await state.orchestrator.run(ctx,{'message':args.question})
            result=await asyncio.wait_for(research(),timeout=5400)
            attempt.update(result_status=result.status,error=result.error,report_path=result.data.get('report_path'),
                report_summary=result.data.get('report_summary'),task_type=result.data.get('task_type'),
                verified_rows=result.data.get('benchmark_verified_count'),answer_status=result.data.get('answer_status'))
            record['status']='completed' if result.status=='completed' else 'failed'
            print(json.dumps(attempt,ensure_ascii=False,indent=2))
            return 0 if result.status=='completed' else 1
        finally:
            if state is not None:
                record['model_requests']=attempt['starting_requests']+state.llm.calls
                await state.llm.close();state.vs.close();state.db.close()
            if record['status']=='running':record['status']='interrupted'
            save()
    try:return asyncio.run(run())
    except KeyboardInterrupt:
        record['status']='interrupted';save()
        return 130


if __name__=='__main__':raise SystemExit(main())
