import copy
import os
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, ConfigDict
from src.runtime.settings import SECRET_KEYS, read_secrets, write_secrets, atomic_write
from src.llm.provider_policy import EFFORTS, is_deepseek, validate_model

router=APIRouter()

class SettingsUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    deepseek_api_key: str | None = Field(default=None,max_length=4096)
    mineru_api_token: str | None = Field(default=None,max_length=4096)
    siliconflow_api_key: str | None = Field(default=None,max_length=4096)
    base_url: str | None = None
    fast_model: str | None = Field(default=None,max_length=200)
    reasoning_model: str | None = Field(default=None,max_length=200)
    max_output_tokens: int | None = Field(default=None,ge=256,le=393216)
    timeout_seconds: int | None = Field(default=None,ge=10,le=900)
    regular_effort: str | None = None
    analysis_effort: str | None = None
    capability_profile: str | None = None
    token_parameter: str | None = None
    json_output: str | None = None
    extra_body_params: dict | None = None

    @field_validator('capability_profile','token_parameter','json_output')
    @classmethod
    def capability_valid(cls,v,info):
        allowed={'capability_profile':{'auto','compatible','deepseek'},'token_parameter':{'max_tokens','max_completion_tokens','omit'},'json_output':{'auto','json_object','prompt'}}
        if v is not None and v not in allowed[info.field_name]:raise ValueError('不支持的接口参数设置')
        return v

    @field_validator('extra_body_params')
    @classmethod
    def extras_valid(cls,v):
        import json
        if v is not None:
            allowed={'temperature','top_p','reasoning_effort','thinking','enable_thinking','chat_template_kwargs'}
            if set(v)-allowed or len(json.dumps(v))>4000:raise ValueError('附加参数仅允许温度、采样和思考控制，不接受密钥或请求路由字段')
            if isinstance(v.get('chat_template_kwargs'),dict) and set(v['chat_template_kwargs'])-{'enable_thinking'}:raise ValueError('chat_template_kwargs 仅支持 enable_thinking')
        return v
    mineru_enabled: bool | None = None
    mineru_model_version: str | None = None
    embedding_provider: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = Field(default=None,max_length=200)
    search_max_rounds: int | None = Field(default=None,ge=1,le=5)
    search_top_k_per_round: int | None = Field(default=None,ge=1,le=30)
    search_deep_parse_top_k: int | None = Field(default=None,ge=0,le=100)
    research_max_model_calls: int | None = Field(default=None,ge=1,le=1000)
    research_timeout_minutes: int | None = Field(default=None,ge=1,le=480)
    @field_validator('base_url','embedding_base_url')
    @classmethod
    def url_valid(cls,v):
        if v is None: return v
        p=urlsplit(v.strip())
        if not p.hostname or p.username or p.password or p.query or p.fragment or p.scheme not in {'https','http'}:
            raise ValueError('请输入不含密钥、查询参数的 HTTP(S) 服务地址')
        if p.scheme=='http' and p.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('远程模型服务须使用 HTTPS')
        return v.strip().rstrip('/')
    @field_validator('embedding_provider')
    @classmethod
    def embedding_valid(cls,v):
        if v is not None and v not in {'local','siliconflow','openai'}: raise ValueError('不支持的检索服务类型')
        return v

    @field_validator('regular_effort','analysis_effort')
    @classmethod
    def effort_valid(cls,v):
        if v is not None and v not in EFFORTS:raise ValueError('请选择 none、low、high 或 max')
        return v

@router.get('/api/settings')
async def get_settings(request:Request):
    cfg=request.app.state.config
    from src.runtime.research_limits import research_limits
    llm=request.app.state.llm
    return {'llm':dict(provider='openai-compatible',base_url=cfg.get('llm',{}).get('base_url',''),fast_model=llm.fast_model,reasoning_model=llm.reasoning_model,has_key=bool(os.getenv('DEEPSEEK_API_KEY')),max_output_tokens=llm.max_output_tokens,timeout_seconds=llm.timeout_seconds,regular_effort=llm.regular_effort,analysis_effort=llm.analysis_effort,deepseek_controls=is_deepseek(llm._base_url),capability_profile=llm.capability_profile,token_parameter=llm.token_parameter,json_output=llm.json_output,extra_body_params=llm.extra_body_params),
            'mineru':dict(enabled=cfg.get('mineru',{}).get('enabled',False),model_version=cfg.get('mineru',{}).get('model_version','vlm'),has_token=bool(os.getenv('MINERU_API_TOKEN'))),
            'embedding':dict(provider=cfg.get('embedding',{}).get('provider','local'),model=cfg.get('embedding',{}).get('model',''),base_url=cfg.get('embedding',{}).get('base_url','https://api.siliconflow.cn/v1'),has_key=bool(os.getenv('SILICONFLOW_API_KEY'))),
            'search':cfg.get('search',{}),
            'research':research_limits(cfg),
            'storage':{'backend':getattr(request.app.state.db,'backend','sqlite'),'workspace':str(request.app.state.workspace_root)},
            'secret_storage':'Windows DPAPI' if os.name=='nt' else '本机文件权限保护（未加密）'}

@router.get('/api/settings/status')
async def check_status(request:Request):
    request.app.state.db.fetchone('SELECT 1')
    return {'llm':'已配置，尚未测试' if request.app.state.llm._ready else '未配置','database':getattr(request.app.state.db,'backend','sqlite'),'database_status':'正常','task_engine':getattr(request.app.state,'task_error',None) or 'C++ 本地任务内核','embedding':getattr(request.app.state.vs,'mode','未启用')}

@router.post('/api/settings/test')
async def test_connection(request:Request):
    try:
        llm=request.app.state.llm
        llm._ensure_ready()
        import asyncio
        result=await asyncio.wait_for(llm.client.models.list(), 10)
        available=sorted({model.id for model in result.data})
        aliases={'deepseek-v4-flash':'deepseek-flash','deepseek-v4-flash-vision-exp':'deepseek-flash'} if is_deepseek(llm._base_url) else {}
        missing=[model for model in {llm.fast_model,llm.reasoning_model} if aliases.get(model,model) not in available and model not in available]
        if missing:
            raise HTTPException(400,'服务可连接，但所填模型不在模型列表中：'+', '.join(sorted(missing))+'。可用模型：'+', '.join(available[:30]))
        return {'status':'ok','models':available,'message':'服务可连接，所填的两个模型均在可用列表中。此检查不调用生成接口，也不代表研究效果已验证。'}
    except HTTPException:
        raise
    except Exception as exc:
        if getattr(exc,'status_code',None) in {404,405,501}:
            return {'status':'unsupported','models':[],'message':'该服务不提供模型列表接口，无法用此方式确认模型名称；已保留手动填写的模型，可用小任务验证生成能力。'}
        import logging
        logging.getLogger(__name__).warning('Connection test failed (%s)',type(exc).__name__)
        raise HTTPException(400,'连接测试失败：检查地址和密钥。部分服务不支持模型列表接口，可直接尝试小任务。') from exc

@router.post('/api/settings')
async def update_settings(body:SettingsUpdate,request:Request):
    state=request.app.state
    if body.embedding_provider in {'openai','siliconflow'}:
        import importlib.util
        if importlib.util.find_spec('chromadb') is None:
            raise HTTPException(400,'当前桌面包不包含外部向量组件，请使用本地文本检索；源码版可安装 requirements-vector.txt')
    tasks=getattr(state,'tasks',None)
    if tasks and any(j['status'] in {'queued','running','cancel_requested'} for j in await tasks.rpc('list')):
        raise HTTPException(409,'请先等待任务结束或取消任务，再修改配置')
    cfg=copy.deepcopy(state.config)
    mapping={'base_url':('llm','base_url'),'fast_model':('llm','fast_model'),'reasoning_model':('llm','reasoning_model'),'mineru_enabled':('mineru','enabled'),'mineru_model_version':('mineru','model_version'),'embedding_provider':('embedding','provider'),'embedding_base_url':('embedding','base_url'),'embedding_model':('embedding','model'),'search_max_rounds':('search','max_rounds'),'search_top_k_per_round':('search','top_k_per_round'),'search_deep_parse_top_k':('search','deep_parse_top_k')}
    for field,(section,key) in mapping.items():
        value=getattr(body,field,None)
        if value is not None: cfg.setdefault(section,{})[key]=value
    for field in ('max_output_tokens','timeout_seconds','regular_effort','analysis_effort','capability_profile','token_parameter','json_output','extra_body_params'):
        value=getattr(body,field)
        if value is not None:cfg.setdefault('llm',{})[field]=value
    try:
        for field in ('fast_model','reasoning_model'):
            cfg['llm'][field]=validate_model(cfg['llm']['base_url'],cfg['llm'][field])
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    for field,key in (('research_max_model_calls','max_model_calls'),('research_timeout_minutes','timeout_minutes')):
        value=getattr(body,field)
        if value is not None:cfg.setdefault('research',{})[key]=value
    if body.search_deep_parse_top_k is not None:
        cfg.setdefault('search',{})['deep_parse_max_k']=body.search_deep_parse_top_k
    secrets=read_secrets(state.workspace_root)
    changed=body.model_dump(exclude_none=True)
    for field in SECRET_KEYS:
        value=getattr(body,field,None)
        if value is not None: secrets[field]=value.strip()
    import yaml
    write_secrets(state.workspace_root,secrets)
    atomic_write(state.workspace_manager.config_path,yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False))
    for field,value in secrets.items():
        if field in SECRET_KEYS: os.environ[SECRET_KEYS[field]]=value
    state.config=cfg
    from src.runtime.research_limits import research_limits
    state.llm.max_calls=research_limits(cfg)['max_model_calls']
    await state.llm.close()
    state.llm.configure(api_key=os.getenv('DEEPSEEK_API_KEY',''),base_url=cfg['llm'].get('base_url'),fast_model=cfg['llm'].get('fast_model'),reasoning_model=cfg['llm'].get('reasoning_model'),max_output_tokens=cfg['llm'].get('max_output_tokens'),timeout_seconds=cfg['llm'].get('timeout_seconds'),regular_effort=cfg['llm'].get('regular_effort'),analysis_effort=cfg['llm'].get('analysis_effort'))
    state.llm.configure(**{key:cfg['llm'].get(key) for key in ('capability_profile','token_parameter','json_output','extra_body_params')})
    return {'status':'updated','applied':{k:'updated' for k in changed},'message':'已保存。新任务使用新配置；向量检索类型变更后请重启应用。'}
