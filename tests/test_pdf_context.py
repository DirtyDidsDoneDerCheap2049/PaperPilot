"""PDF glyph, late-page excerpt and same-session attachment regressions."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from src.agents.base import AgentContext
from src.agents.parse_agent import ParseAgent
from src.knowledge.sqlite_store import SQLiteStore
from src.parsing.document_excerpt import document_excerpt
from src.parsing.pymupdf_parser import normalize_pdf_text


def test_pdf_invalid_glyph_keeps_valid_text_and_numbers():
    text='中文 EPE 0.34 😀 '+chr(0xd83d)+chr(0xde00)+' broken '+chr(0xd83d)
    normalized=normalize_pdf_text(text)
    assert normalized=='中文 EPE 0.34 😀 😀 broken \ufffd'
    assert normalized.encode('utf-8').decode('utf-8')==normalized


def test_parse_agent_can_write_pdf_with_invalid_surrogate(tmp_path,monkeypatch):
    import fitz
    class Table:
        col_count=2;row_count=3
        def extract(self):return [['Model','EPE'],['Example','0.34'],['Other','0.35']]
        def to_markdown(self,**kw):return '| Example | 0.34 |\n| Other | 0.35 | '+chr(0xd83d)
    class Page:
        def get_text(self):return ('Synthetic original evidence EPE 0.34.\n'*40)+chr(0xd83d)
        def find_tables(self,**kw):return SimpleNamespace(tables=[Table()])
    class Doc:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def __iter__(self):return iter([Page()])
    monkeypatch.setattr(fitz,'open',lambda *args,**kw:Doc())
    pdf=tmp_path/'paper.pdf';pdf.write_bytes(b'synthetic PDF fixture')
    db=SQLiteStore(tmp_path/'test.db');db.init_schema()
    db.execute("INSERT INTO papers(id,title,fulltext_path) VALUES('p','Synthetic',?)",(str(pdf),))
    try:
        result=asyncio.run(ParseAgent(db)._run_batch(AgentContext(workspace_root=tmp_path,config={'mineru':{'enabled':False}}),
                           {'papers':[{'id':'p','fulltext_path':str(pdf)}],'deep_parse_top_k':1}))
        assert result.data['parsed']==['p'] and not result.data['failed']
        row=db.fetchone("SELECT parsed_markdown_path,retrieval_status FROM papers WHERE id='p'")
        from pathlib import Path
        text=Path(row['parsed_markdown_path']).read_text(encoding='utf-8')
        assert '0.34' in text and '\ufffd' in text and row['retrieval_status']=='parsed'
        assert pdf.read_bytes()==b'synthetic PDF fixture'
    finally:db.close()


@pytest.mark.parametrize('budget',[80,400,10000])
def test_excerpt_bounds_and_missing_content_notice(budget):
    text='Long irrelevant source. '*2500
    excerpt=document_excerpt(text,'unrelated query',budget)
    assert len(excerpt)<=budget and '不能据此' in excerpt


def test_excerpt_finds_result_beyond_prefix():
    text=('Background prose with no experiment.\n'*1000)+'\nExampleDataset EPE result is 0.34.\n'+('References.\n'*300)
    excerpt=document_excerpt(text,'ExampleDataset EPE result',10000)
    assert 'ExampleDataset EPE result is 0.34.' in excerpt
    assert '有限摘录' in excerpt and len(excerpt)<=10000


def test_discussion_inherits_pdf_and_reads_after_page_eight(tmp_path,monkeypatch):
    import fitz
    from src.server.routes_chat import ChatRequest,_run_report_chat
    pdf=tmp_path/'late.pdf'
    with fitz.open() as doc:
        for i in range(12):
            page=doc.new_page()
            page.insert_text((30,50),'\n'.join(['Synthetic background prose without reported metrics.']*50),fontsize=8)
            if i==11:page.insert_text((30,520),'ModelA ExampleDataset EPE 0.34',fontsize=10)
        doc.save(pdf)
    original=pdf.read_bytes()
    db=SQLiteStore(tmp_path/'test.db');db.init_schema()
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','Test')")
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('other','w','Other')")
    db.execute("INSERT INTO papers(id,title,fulltext_path,retrieval_status) VALUES('p','ModelA',?,'downloaded')",(str(pdf),))
    db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
               ('old','s','user','ExampleDataset EPE是多少？',json.dumps({'selected_paper_ids':['p']})))
    db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
               ('unrelated','other','user','Other private question',json.dumps({'selected_paper_ids':['foreign']})))
    class Model:
        async def chat_stream(self,messages,**kw):
            prompt=messages[-1]['content']
            assert 'ModelA ExampleDataset EPE 0.34' in prompt
            assert 'Other private question' not in prompt and 'foreign' not in prompt
            assert '有限摘录' in prompt
            yield '本地合成结果为 0.34。'
    async def broadcast(*args):pass
    monkeypatch.setattr('src.server.websocket.manager.broadcast',broadcast)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db,workspace_root=tmp_path,workspace_id='w',llm=Model())))
    try:
        asyncio.run(_run_report_chat(ChatRequest(mode='report_chat',session_id='s',message='包含啊，你自己看一下'),request,'s','current'))
        meta=json.loads(db.fetchone("SELECT metadata_json FROM messages WHERE id='current'")['metadata_json'])
        assert meta['selected_paper_ids']==[] and meta['context_paper_ids']==['p']
        assert db.fetchone("SELECT parsed_markdown_path FROM papers WHERE id='p'")['parsed_markdown_path'] is None
        assert pdf.read_bytes()==original
    finally:db.close()
