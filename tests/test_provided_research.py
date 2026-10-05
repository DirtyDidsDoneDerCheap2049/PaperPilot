"""A focused source check must terminate without becoming a literature survey."""
import asyncio
from types import SimpleNamespace
import pytest
from src.agents.base import AgentContext,AgentResult
from src.analysis.adaptive_research import execute_research
from src.analysis.research_plan import normalize_plan,scoped_papers


@pytest.mark.parametrize('failed,missing',[(False,False),(True,True),(False,True)])
@pytest.mark.parametrize('action',['search','read','lookup','null_ids','null_queries','scalar_ids','invalid_action'])
def test_closed_source_check_stops_after_read_and_analysis(tmp_path,monkeypatch,failed,missing,action):
    papers=[]
    for pid in ('a','b'):
        path=tmp_path/(pid+'.pdf');path.write_bytes(b'local test PDF')
        papers.append({'id':pid,'title':'Test '+pid,'year':2020,'fulltext_path':str(path)})
    plan=normalize_plan({'task_type':'claim_verification','source_scope':'provided',
                         'time_scope':{'start':'2025-01-01','end':'2026-10-05'}},'核对指定两篇论文')
    assert len(scoped_papers(papers,{'research_plan':plan})[0])==2
    read_calls=[];saved=[]
    class Model:
        calls=0;max_calls=100
        async def chat_json(self,messages,**kwargs):
            import json
            self.calls+=1
            state=json.loads(messages[-1]['content'])
            assert 'search' not in state['allowed_actions']
            if failed and self.calls==2:
                assert any(r['paper_id']=='b' and 'invalid glyph' in r['error'] for r in state['read_results'])
            # Deliberately ignore the instruction and request unrelated papers.
            if action=='null_ids':return {'action':'read','paper_ids':None,'queries':None}
            if action=='null_queries':return {'action':'search','queries':None}
            if action=='scalar_ids':return {'action':'read','paper_ids':42}
            if action=='invalid_action':return {'action':['read'],'paper_ids':['a','b']}
            return {'action':action,'paper_ids':['outside-the-task'],'queries':['unrelated worldwide ranking']}
    async def parse(self,ctx,data):
        ids=[p['id'] for p in data['papers']];read_calls.append(ids)
        return AgentResult(status='completed',data={'parsed':[p for p in ids if not(failed and p=='b')],
            'failed':[{'paper_id':'b','reason':'invalid glyph'}] if failed and 'b' in ids else [],'metadata_only':[]})
    async def extract(ctx,parsed,targets,**kwargs):
        return [{'paper_id':p,'has_fulltext':True,'source_type':'paper_fulltext','findings':['verified local fact']} for p in parsed.data['parsed']]
    async def analyze(*args):
        return {'task_type':'claim_verification','answer_status':'answered_in_retrieved_sources',
            'answers':[{'answer':'Synthetic verified fact'}], 'missing_information':['field not reported'] if missing else [],
            'baseline_followups':[{'model':'UnrelatedModel','dataset':'Other','metric':'EPE'}]}
    async def create(*args,**kwargs):return 'run'
    async def finish(run,agent,sid,result):saved.append((agent,dict(result.data)))
    async def upsert(*args):pass
    async def progress(*args):pass
    async def forbidden(*args,**kwargs):raise AssertionError('Focused check expanded to unrelated sources')
    monkeypatch.setattr('src.agents.parse_agent.ParseAgent.run',parse)
    monkeypatch.setattr('src.analysis.question_evidence.analyze_question',analyze)
    monkeypatch.setattr('src.analysis.adaptive_research.local_baseline_papers',forbidden)
    owner=SimpleNamespace(llm=Model(),db=None,_selected_papers=lambda ids:papers,_parse_budget=lambda *args:60,
        _create_agent_run=create,_finish_agent_run=finish,_extract_innovations=extract,_progress=progress,_search=forbidden,
        kb=SimpleNamespace(upsert_paper=upsert,save_innovation_profile=upsert))
    direction={'research_plan':plan}
    ctx=AgentContext(workspace_root=tmp_path,session_id='s',config={})
    actual,profiles,analysis,pd=asyncio.run(execute_research(owner,ctx,direction,['a','b']))
    assert [p['id'] for p in actual]==['a','b']
    assert read_calls==([['a','b'],['b']] if failed else [['a','b']])
    assert owner.llm.calls==(3 if failed else 2)
    assert analysis['agent_decisions'][-1]['action']=='deliver'
    assert not any(d['action']=='search' for d in analysis['agent_decisions'])
    assert direction['search_execution']['stop_reason']==('provided_sources_exhausted' if failed or missing else 'provided_sources_answered')
    assert analysis['reading_coverage']['status']==('partial' if failed else 'complete')
    if action in {'null_ids','null_queries','scalar_ids','invalid_action'}:
        assert any(d.get('planner_output_errors') for d in analysis['agent_decisions'])


@pytest.mark.parametrize('last_action',['read','search','lookup'])
@pytest.mark.parametrize('dirty',[False,True])
def test_last_step_preserves_analyzed_results_instead_of_abandoning_report(tmp_path,monkeypatch,last_action,dirty):
    papers=[]
    for pid in ('a','b','c'):
        path=tmp_path/(pid+'.pdf');path.write_bytes(b'local test PDF')
        papers.append({'id':pid,'title':'Test '+pid,'fulltext_path':str(path)})
    actions=[{'action':'read','paper_ids':['a']},{'action':'analyze'},
             {'action':'read','paper_ids':['b']} if dirty else {'action':'analyze'},
             {'action':last_action,'paper_ids':['c'],'queries':['new unrelated result']}]
    read_calls=[]
    class Model:
        calls=0;max_calls=100
        async def chat_json(self,*args,**kwargs):
            self.calls+=1
            return actions.pop(0)
    async def parse(self,ctx,data):
        ids=[p['id'] for p in data['papers']];read_calls.extend(ids)
        return AgentResult(status='completed',data={'parsed':ids,'failed':[],'metadata_only':[]})
    async def extract(ctx,parsed,targets,**kwargs):
        return [{'paper_id':p,'has_fulltext':True,'findings':['verified local fact']} for p in parsed.data['parsed']]
    async def analyze(llm,direction,profiles):
        return {'answer_status':'answered_in_retrieved_sources',
                'answers':[{'answer':p['paper_id']} for p in profiles]}
    async def create(*args,**kwargs):return 'run'
    async def finish(*args,**kwargs):pass
    async def upsert(*args):pass
    async def progress(*args):pass
    async def forbidden(*args,**kwargs):raise AssertionError('Last step cannot fetch evidence without time to analyze it')
    monkeypatch.setattr('src.agents.parse_agent.ParseAgent.run',parse)
    monkeypatch.setattr('src.analysis.question_evidence.analyze_question',analyze)
    monkeypatch.setattr('src.tools.paper_source.lookup_paper_source',forbidden)
    owner=SimpleNamespace(llm=Model(),db=None,_selected_papers=lambda ids:papers,_parse_budget=lambda *args:60,
        _create_agent_run=create,_finish_agent_run=finish,_extract_innovations=extract,_progress=progress,_search=forbidden,
        kb=SimpleNamespace(upsert_paper=upsert,save_innovation_profile=upsert))
    direction={'research_plan':normalize_plan({'task_type':'claim_verification','source_scope':'open_literature'},'核对这些论文')}
    ctx=AgentContext(workspace_root=tmp_path,session_id='s',config={'research':{'max_agent_steps':4}})
    actual,profiles,analysis,pd=asyncio.run(execute_research(owner,ctx,direction,['a','b','c']))
    expected=['a','b'] if dirty else ['a']
    assert read_calls==expected and [a['answer'] for a in analysis['answers']]==expected
    assert analysis['agent_decisions'][-1]['action']==('analyze' if dirty else 'deliver')
    assert analysis['reading_coverage']['status']=='partial' and analysis['answer_status']=='partial'
    assert 'c' in analysis['reading_coverage']['unread_ids']
    assert owner.llm.calls==4
