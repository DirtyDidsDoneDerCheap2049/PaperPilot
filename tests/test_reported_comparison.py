"""Literature rankings retain provenance without imposing controlled experiments."""
import asyncio
import json
import pytest
from src.analysis.provenance import bind_profile
from src.analysis.question_evidence import model_value_in_quote, numerical_rows, summarize_benchmarks, analyze_question
from src.analysis.research_plan import normalize_plan


@pytest.mark.parametrize('quote,model,value,metric,expected',[
    ('Method\nBaseA [1]\nBaseB [2]\nNewMethod\nEPE (px)\n0.78\n0.42\n0.35−25.53%', 'NewMethod',.35,'EPE',True),
    ('Method\nBaseA\nNewMethod\nEPE\n0.78\n0.34', 'BaseA',.34,'EPE',False),
    ('Method\nBaseA\nNewMethod\nEPE\n0.34', 'NewMethod',.34,'EPE',False),
    ('| Method | BaseA [1] | NewMethod |\n| --- | --- | --- |\n| EPE (px) | 0.78 | **0.34** |', 'NewMethod',.34,'EPE',True),
    ('| Method | BaseA | NewMethod |\n| EPE | 0.78 | 0.42 |\n| Time (s) | 1.1 | 0.34 |', 'NewMethod',.34,'EPE',False),
    ('| Method | EPE | Time (s) |\n| NewMethod | 0.42 | 0.34 |', 'NewMethod',.34,'EPE',False),
    ('| Method | EPE | Time (s) |\n| NewMethod | 0.42 | 0.34 |', 'NewMethod',.34,'Time (s)',True),
    ('NewMethodPlus | ExampleDataset | EPE | 0.34', 'NewMethod',.34,'EPE',False),
    ('BaseA [12] | ExampleDataset | EPE | 0.34', 'BaseA [12]',.34,'EPE',True),
    ('|Full model|Prior|32|**0.37**<br>0.64<br><br>|', 'Full model',.37,'EPE (px)',True),
    ('PipMethod (Ours)\n1\n0.45(-13.5%)\n0.35(-73.4%)', 'PipMethod',.45,'EPE (px)',True),
    ('|1<br>2|First<br>Second|0.45<br>0.35|', 'First',.45,'EPE',True),
    ('|1<br>2|First<br>Second|0.45<br>0.35|', 'First',.35,'EPE',False),
    ('|Full model-<br>4iter|Prior|4|0.42|0.34|', 'Full model-4iter',.42,'EPE',True),
    ('Full model-|4iter<br>Prior|Auxiliary|4|0.42<br>0.34', 'Full model-4iter',.42,'EPE',True),
    ('| Method | EPE | Time |\n| NewMethod | 0.45 | 1 |\n| NewMethod | 0.34 | 2 |', 'NewMethod',.34,'EPE',True),
])
def test_positional_tables_keep_models_and_metric_columns(quote,model,value,metric,expected):
    assert model_value_in_quote(model,value,quote,metric)==expected


@pytest.mark.parametrize('title,role,expected',[
    ('NewMethod: A benchmark study','proposed',True),
    ('_2025_NewMethod_Conference_.pdf','proposed',True),
    ('OtherMethod: A benchmark study','proposed',False),
    ('NewMethod: A benchmark study','baseline',False),
    ('New-Method: A benchmark study','proposed',True),
])
def test_author_alias_requires_proposed_source_identity(title,role,expected):
    quote='Method\nBaseA [1]\nOurs\nEPE\n0.78\n0.34'
    profile=bind_profile({'paper_id':'p','paper_title':title,'evidence':[{'quote':quote}],
        'benchmark_results':[{'model':'NewMethod (Ours)','dataset':'ExampleDataset','metric':'EPE',
                             'value':.34,'group':'accuracy','row_role':role,'evidence_indices':[1]}]},quote,'fixture.md')
    assert numerical_rows([profile],{})[0]['source_bound']==expected


def benchmark_row(model,value,protocol,dataset='ExampleDataset',metric='EPE'):
    return {'model':model,'value':value,'protocol':protocol,'comparison_key':protocol,'dataset':dataset,
            'metric':metric,'group':'accuracy','eligible':True,'paper_id':model}


def test_default_ranks_reported_values_preserving_configuration_notes_and_ties():
    rows=[benchmark_row('MixedTrain',.34,'mixed pretraining'),benchmark_row('DatasetOnly',.35,'dataset only'),
          benchmark_row('Tied',.34,'unknown resolution')]
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy']}},'最低EPE是多少？')
    summary=summarize_benchmarks(rows,plan)[0]
    assert {r['model'] for r in summary['best_reported']}=={'MixedTrain','Tied'}
    assert {r['comparison_key'] for r in summary['best_reported']}=={'mixed pretraining','unknown resolution'}
    assert summary['comparison_mode']=='reported_results'
    assert len(summarize_benchmarks(rows,{**plan,'comparison_mode':'controlled_comparison'})[0]['best_reported'])==3


def test_without_a_reviewed_target_different_datasets_and_metrics_stay_separate():
    rows=[benchmark_row('A',.34,''),benchmark_row('B',.1,'','DifferentDataset'),
          benchmark_row('C',.01,'',metric='Bad1')]
    assert len(summarize_benchmarks(rows,{})[0]['best_reported'])==3


def test_reviewed_target_unifies_equivalent_dataset_and_metric_labels():
    rows=[benchmark_row('A',.34,''),benchmark_row('B',.35,'','ExampleDataset (test set)','EPE (px)')]
    plan={'comparison':{'dataset':'ExampleDataset','metric':'EPE','groups':['accuracy']}}
    assert [r['model'] for r in summarize_benchmarks(rows,plan)[0]['best_reported']]==['A']


def test_multi_target_request_keeps_actual_dataset_rankings_separate():
    rows=[benchmark_row('A',.34,''),benchmark_row('B',.1,'','DifferentDataset')]
    plan={'comparison':{'dataset':'ExampleDataset and DifferentDataset','metric':'EPE','groups':['accuracy']}}
    assert {r['model'] for r in summarize_benchmarks(rows,plan)[0]['best_reported']}=={'A','B'}


def test_unconverted_time_units_are_not_ranked_together():
    rows=[benchmark_row('A',400,'',metric='Latency (ms)'),benchmark_row('B',2,'',metric='Latency (s)')]
    plan={'comparison':{'dataset':'ExampleDataset','metric':'Latency','groups':['accuracy']}}
    assert {r['model'] for r in summarize_benchmarks(rows,plan)[0]['best_reported']}=={'A','B'}


def test_per_paper_limits_do_not_turn_a_confirmed_answer_into_a_global_failure():
    quote='Method\nOurs\nEPE\n0.34'
    profile=bind_profile({'paper_id':'p','paper_title':'NewMethod: Benchmark','evidence':[{'quote':quote}],
        'missing_information':['未说明Dmax','本篇未给另一篇论文的结果'],
        'benchmark_results':[{'model':'NewMethod','dataset':'ExampleDataset','metric':'EPE','value':.34,
                             'group':'accuracy','row_role':'proposed','evidence_indices':[1]}]},quote,'fixture.md')
    class Model:
        reasoning_model='fixture'
        async def chat_json(self,messages,**kwargs):
            assert '默认是文献报告值比较' in messages[0]['content']
            rows=json.loads(messages[-1]['content'])['rows']
            return {'row_reviews':[{'row_id':row['row_id'],'scope_match':True,'group_verified':True} for row in rows]}
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy']}},'最低EPE？')
    result=asyncio.run(analyze_question(Model(),{'research_plan':plan},[profile]))
    assert result['answer_status']=='answered_in_retrieved_sources'
    assert result['missing_information']==[]
    assert result['source_limitations'][0]['missing_information']==profile['missing_information']
    assert result['benchmark_summary'][0]['best_reported'][0]['value']==.34


def test_a_missing_required_group_remains_partial():
    class Model:
        reasoning_model='fixture'
        async def chat_json(self,*args,**kwargs):raise AssertionError('No rows to review')
    result=asyncio.run(analyze_question(Model(),{'research_plan':{'task_type':'benchmark_comparison',
                                 'comparison':{'groups':['accuracy','realtime']}}},[]))
    assert result['answer_status']=='partial' and len(result['missing_information'])==2


def test_report_and_review_use_the_same_reported_comparison_rule():
    from src.analysis.reader_report import write_reader_report
    quote='NewMethod | ExampleDataset | EPE | 0.34'
    profile=bind_profile({'paper_id':'p','paper_title':'NewMethod: Benchmark','source_type':'paper_fulltext',
                          'has_fulltext':True,'evidence':[{'quote':quote}]},quote,'fixture.md')
    text='# 论文报告值对照\n\n## 结论\n本轮核实的最低EPE为NewMethod的0.34 [1]。\n\n## 数值对照\n'
    text+='本轮论文报告值对照，仅比较相同目标数据集与指标。不同训练和评测设置各自保留，没有重新训练。\n'*5
    text+='\n## 口径与依据\n混合预训练与数据集内训练是差异说明，不证明受控实验优劣 [1]。\n\n## 本轮边界\n仅限本轮资料。\n'
    class Model:
        reasoning_model='fixture'
        async def chat(self,messages,**kwargs):
            assert '默认比较论文原文报告值' in messages[0]['content']
            assert json.loads(messages[1]['content'])['direct_answer']['source_limitations']
            return text
        async def chat_json(self,messages,**kwargs):
            assert '差异只需标注' in messages[0]['content']
            return {'pass':True,'issues':[]}
    plan=normalize_plan({'task_type':'benchmark_comparison'},'最低EPE是多少？')
    analysis={'source_limitations':[{'paper_id':'p','missing_information':['未说明Dmax']}]}
    report=asyncio.run(write_reader_report(Model(),{'research_plan':plan},[{'id':'p','title':'NewMethod'}],[profile],analysis))
    assert report.startswith(text) and '## 参考文献' in report and '[1] NewMethod' in report


@pytest.mark.parametrize('value',[None,'invalid',{},[]])
def test_invalid_comparison_modes_default_to_literature_ranking(value):
    assert normalize_plan({'comparison_mode':value},'最低EPE是多少？')['comparison_mode']=='reported_results'


def test_inline_labeled_result_cannot_be_moved_to_another_metric():
    assert not model_value_in_quote('NewMethod',.34,'NewMethod | ExampleDataset | Bad1 | 0.34','EPE')
