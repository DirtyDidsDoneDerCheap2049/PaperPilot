"""Conversation-to-research regressions with synthetic, local-only evidence."""
import asyncio
import json
from src.agents.base import AgentContext, AgentResult
from src.agents.orchestrator import Orchestrator
from src.knowledge.sqlite_store import SQLiteStore
import pytest

QUESTION='核对这两篇论文在 ExampleBenchmark 的结果是否低于 0.36'
SHORT='包含啊，你自己看一下'


def seed(tmp_path):
    db=SQLiteStore(tmp_path/'history.db');db.init_schema()
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','ExampleBenchmark')")
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('other','w','Unrelated')")
    for pid in ('old','a','b','fresh'):
        db.execute('INSERT INTO papers(id,title,abstract) VALUES(?,?,?)',(pid,'Paper '+pid,'Synthetic source '+pid))
    direction={'research_question':QUESTION,'target_task':'ExampleBenchmark',
               'search_queries':['ExampleBenchmark reported results'],
               'research_plan':{'task_type':'benchmark_comparison','objective':QUESTION}}
    db.execute('INSERT INTO gap_analyses(id,session_id,direction,search_log_json) VALUES(?,?,?,?)',
               ('prior','s',json.dumps(direction),json.dumps({'papers':[{'id':'old'}]})))
    db.execute('INSERT INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',('first','s','user',QUESTION))
    db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
               ('uploads','s','user','这两篇都比0.36低',json.dumps({'mode':'report_chat','selected_paper_ids':['a','b']})))
    db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
               ('answer','s','assistant','片段里没有结果表，需要核对原文',json.dumps({'mode':'report_chat'})))
    db.execute('INSERT INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',('secret','other','user','unrelated conversation must stay isolated'))
    return db,direction


def test_short_correction_always_sends_history_to_direction_model():
    class Model:
        async def chat_json(self,messages,**kw):
            content=messages[-1]['content']
            assert '这两篇都比0.36低' in content and '片段里没有结果表' in content
            assert SHORT in content
            assert '历史助手回答可能错误' in messages[0]['content']
            return {'context_relation':'continue','context_paper_ids':['a','b'],'research_question':QUESTION,
                    'target_task':'ExampleBenchmark','research_plan':{'task_type':'claim_verification','objective':QUESTION}}
    result=asyncio.run(Orchestrator(None,Model(),None,None)._parse_direction(SHORT,{},'user:这两篇都比0.36低\nassistant:片段里没有结果表'))
    assert result['context_recovered'] and result['context_relation']=='continue'
    assert result['context_paper_ids']==['a','b'] and result['user_request']==SHORT
    assert result['research_plan']['objective']==QUESTION


def test_first_turn_direction_sees_current_attachments_and_retry_pins_catalog(tmp_path,monkeypatch):
    db,_=seed(tmp_path)
    seen=[]
    class Model:
        async def chat_json(self,messages,**kwargs):
            content=messages[-1]['content'];seen.append(content)
            assert '本次选择/上传论文' in content and 'Paper a' in content and 'Paper b' in content
            assert 'later renamed source' not in content and 'unrelated conversation' not in content
            assert '这两篇都比0.36低' not in content
            return {'context_relation':'new_topic','target_task':'ExampleBenchmark',
                    'research_question':QUESTION,'research_plan':{'task_type':'claim_verification','source_scope':'provided'}}
    async def execute(owner,ctx,direction,ids):
        assert ids==['a','b'] and direction['context_relation']=='new_topic'
        return [],[],{},{}
    async def deliver(*args,**kwargs):return AgentResult(status='completed')
    monkeypatch.setattr('src.analysis.adaptive_research.execute_research',execute)
    monkeypatch.setattr('src.analysis.adaptive_research.deliver_research',deliver)
    owner=Orchestrator(db,Model(),None,None)
    ctx=AgentContext(session_id='new',workspace_id='w',workspace_root=tmp_path,config={})
    request={'message':'核对我上传的两篇论文结果','message_id':'first-upload','selected_paper_ids':['a','b']}
    try:
        result=asyncio.run(owner.run(ctx,request))
        assert result.status=='completed',result.error
        saved=db.fetchone("SELECT content,metadata_json FROM messages WHERE id='first-upload'")
        assert saved['content']==request['message']
        snapshot=json.loads(saved['metadata_json'])['context_snapshot']
        assert [p['id'] for p in snapshot['selected_papers']]==['a','b']
        db.execute("UPDATE papers SET title='later renamed source' WHERE id='a'")
        result=asyncio.run(owner.run(ctx,request))
        assert result.status=='completed',result.error
        assert seen[0]==seen[1]
    finally:db.close()


def test_explicit_new_topic_gets_history_but_does_not_inherit_old_task():
    class Model:
        async def chat_json(self,messages,**kw):
            assert 'Old benchmark' in messages[-1]['content']
            return {'context_relation':'new_topic','research_question':'比较图数据库索引方案',
                    'target_task':'graph indexes','search_queries':['graph index evaluation'],
                    'research_plan':{'task_type':'literature_review','objective':'比较图数据库索引方案'}}
    previous={'research_question':'Old benchmark','target_task':'old benchmark','method_category':'old method'}
    result=asyncio.run(Orchestrator(None,Model(),None,None)._parse_direction('比较图数据库索引方案',previous,'Old benchmark'))
    assert result['context_relation']=='new_topic' and not result.get('context_recovered')
    assert result['target_task']=='graph indexes' and not result['method_category']


def test_compatible_model_without_relation_keeps_supplied_history():
    class Empty:
        async def chat_json(self,*args,**kwargs):return {}
    previous={'research_question':QUESTION,'target_task':'ExampleBenchmark','search_queries':['ExampleBenchmark results']}
    result=asyncio.run(Orchestrator(None,Empty(),None,None)._parse_direction(SHORT,previous,'same conversation'))
    assert result['context_recovered'] and result['research_question']==QUESTION


@pytest.mark.parametrize('relation,expected',[('continue',['a','b']),('new_topic',[])])
def test_mode_switch_restores_attachments_and_retry_pins_original_context(tmp_path,monkeypatch,relation,expected):
    db,previous=seed(tmp_path)
    seen=[]
    class Model:
        async def chat_json(self,messages,**kw):
            content=messages[-1]['content'];seen.append(content)
            assert '这两篇都比0.36低' in content and 'Paper a' in content and 'Paper b' in content
            assert 'unrelated conversation' not in content and 'later message' not in content
            return {'context_relation':relation,'context_paper_ids':['a','b','not-in-session'],
                    'research_question':QUESTION,'target_task':'ExampleBenchmark',
                    'research_plan':{'task_type':'claim_verification','objective':QUESTION,'adaptive':True}}
    inherited=[]
    async def execute(owner,ctx,direction,ids):
        inherited.append(ids);return [],[],{},{}
    async def deliver(owner,ctx,direction,papers,profiles,analysis,pd,selected,mid):
        return AgentResult(status='completed',data={'inherited':analysis['inherited_paper_ids']})
    monkeypatch.setattr('src.analysis.adaptive_research.execute_research',execute)
    monkeypatch.setattr('src.analysis.adaptive_research.deliver_research',deliver)
    owner=Orchestrator(db,Model(),None,None)
    ctx=AgentContext(session_id='s',workspace_id='w',workspace_root=tmp_path,config={})
    request={'message':SHORT,'message_id':'turn','selected_paper_ids':[]}
    try:
        result=asyncio.run(owner.run(ctx,request))
        assert result.status=='completed' and result.data['inherited']==expected
        saved=db.fetchone("SELECT content,metadata_json FROM messages WHERE id='turn'")
        assert saved['content']==SHORT
        snapshot=json.loads(saved['metadata_json'])['context_snapshot']
        assert snapshot['paper_ids']==['a','b','old']
        assert 'unrelated conversation' not in snapshot['recent_context']
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('later','s','assistant','later message')")
        db.execute('INSERT INTO gap_analyses(id,session_id,direction,search_log_json) VALUES(?,?,?,?)',
                   ('later','s','{"target_task":"later task"}','{"papers":[{"id":"fresh"}]}'))
        repeated=asyncio.run(owner.run(ctx,request))
        assert repeated.status=='completed' and inherited==[expected,expected]
        assert seen[0]==seen[1]
        assert db.fetchone("SELECT count(*) AS n FROM messages WHERE id='turn'")['n']==1
    finally:db.close()


def test_recent_history_keeps_latest_correction_under_budget(tmp_path):
    db,_=seed(tmp_path)
    try:
        for i in range(8):
            db.execute('INSERT INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',
                       (str(i),'s','assistant','large report '+('x'*12000)))
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('correction','s','user','specific latest correction')")
        context=Orchestrator(db,None,None,None)._recent_direction_context('s',max_chars=6000)
        assert 'specific latest correction' in context
        assert len(context)<=6020 and 'unrelated conversation' not in context
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('current','s','user','current input')")
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('future','s','assistant','future answer')")
        bounded=Orchestrator(db,None,None,None)._recent_direction_context('s',exclude_message_id='current')
        assert 'current input' not in bounded and 'future answer' not in bounded
    finally:db.close()


def test_empty_failed_report_does_not_hide_prior_papers_and_deleted_uploads_stay_deleted(tmp_path):
    db,_=seed(tmp_path)
    try:
        db.execute("INSERT INTO gap_analyses(id,session_id,direction,search_log_json) VALUES('empty','s','{}','{\"papers\":[]}')")
        db.execute("DELETE FROM messages WHERE id='uploads'")
        snapshot=Orchestrator(db,None,None,None)._conversation_snapshot('s','next',SHORT)
        assert snapshot['paper_ids']==['old']
        assert '这两篇都比0.36低' not in snapshot['recent_context']
    finally:db.close()


def test_attachment_ids_survive_dialogue_budget_and_respect_anchor_and_deletion(tmp_path):
    db,_=seed(tmp_path)
    try:
        for i in range(145):
            db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
                       (f'long-{i}','s','assistant','later discussion','{}'))
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('current','s','user','current input')")
        db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
                   ('future-upload','s','user','later upload',json.dumps({'selected_paper_ids':['fresh']})))
        owner=Orchestrator(db,None,None,None)
        snapshot=owner._conversation_snapshot('s','current',SHORT)
        assert snapshot['paper_ids']==['a','b','old']
        assert '这两篇都比0.36低' not in snapshot['recent_context']
        assert 'fresh' not in snapshot['paper_ids']
        db.execute("DELETE FROM messages WHERE id='uploads'")
        assert owner._conversation_snapshot('s','next',SHORT)['paper_ids']==['fresh','old']
    finally:db.close()


@pytest.mark.parametrize('scope,time,expected',[
    ('provided',{},{}),('open_literature',{},{}),('open_literature',None,{'start':'2024-10-05','end':'2026-10-05','basis':'publication'})])
def test_followup_scope_respects_explicit_empty_time_window(scope,time,expected):
    old={'research_plan':{'task_type':'benchmark_comparison','time_scope':{'start':'2024-10-05','end':'2026-10-05','basis':'publication'}}}
    class Model:
        async def chat_json(self,messages,**kw):
            plan={'task_type':'claim_verification','source_scope':scope,'objective':QUESTION}
            if time is not None:plan['time_scope']=time
            return {'context_relation':'continue','context_paper_ids':['a','b'],'research_question':QUESTION,'research_plan':plan}
    result=asyncio.run(Orchestrator(None,Model(),None,None)._parse_direction(SHORT,old,'this discussion'))
    assert result['research_plan']['source_scope']==scope and result['research_plan']['time_scope']==expected
