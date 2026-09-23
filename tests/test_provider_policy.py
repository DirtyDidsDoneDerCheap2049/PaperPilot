import asyncio
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from src.llm.deepseek_client import DeepSeekClient
from src.server.provider_settings import SettingsUpdate, test_connection as check_connection


def client(monkeypatch, **kwargs):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    return DeepSeekClient(**kwargs)


def test_same_flash_supports_distinct_task_efforts(monkeypatch):
    llm=client(monkeypatch)
    assert llm.fast_model==llm.reasoning_model=='deepseek-flash'
    assert llm.regular_effort==llm.analysis_effort=='max'
    llm.configure(regular_effort='low',analysis_effort='high')
    regular=llm.request_options(llm.fast_model)
    analysis=llm.request_options(llm.reasoning_model,purpose='analysis',structured=True)
    assert regular['reasoning_effort']=='low'
    assert analysis['reasoning_effort']=='high'
    assert analysis['response_format']=={'type':'json_object'}
    assert analysis['max_tokens']==65536
    llm.configure(regular_effort='none',max_output_tokens=12000)
    disabled=llm.request_options(llm.fast_model)
    assert disabled['extra_body']=={'thinking':{'type':'disabled'}}
    assert 'reasoning_effort' not in disabled
    assert disabled['max_tokens']==12000


def test_provider_specific_controls_do_not_leak_to_other_services(monkeypatch):
    llm=client(monkeypatch,base_url='https://example.com/v1',fast_model='custom',reasoning_model='another')
    assert llm.request_options('custom',purpose='analysis',structured=True)=={'model':'custom','max_tokens':4096}


@pytest.mark.parametrize('name',['deepseek-v41-flash','deepseek-v4.1-flash'])
def test_invalid_version_label_rejected_before_paid_request(monkeypatch,name):
    llm=client(monkeypatch)
    with pytest.raises(ValueError,match='deepseek-flash'):llm.request_options(name)
    assert llm.calls==0


def test_connection_checks_selected_model_not_just_http_success(monkeypatch):
    llm=client(monkeypatch)
    async def listing():return NS(data=[NS(id='deepseek-flash'),NS(id='deepseek-v4-pro')])
    llm.client=NS(models=NS(list=listing));llm._ready=True
    request=NS(app=NS(state=NS(llm=llm)))
    assert asyncio.run(check_connection(request))['status']=='ok'
    llm.fast_model='invalid-model'
    with pytest.raises(HTTPException) as failure:asyncio.run(check_connection(request))
    assert failure.value.status_code==400
    assert 'invalid-model' in failure.value.detail and 'deepseek-flash' in failure.value.detail


@pytest.mark.parametrize('values',[{'max_output_tokens':0},{'regular_effort':'ultra'},{'timeout_seconds':1}])
def test_settings_validate_request_controls(values):
    with pytest.raises(ValueError):SettingsUpdate(**values)
