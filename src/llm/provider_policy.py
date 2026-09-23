"""Provider-specific defaults, checked against DeepSeek docs on 2026-09-23."""
from urllib.parse import urlsplit

DEFAULT_MODEL = 'deepseek-flash'
EFFORTS = {'none', 'low', 'high', 'max'}


def is_deepseek(base_url):
    return urlsplit(base_url).hostname == 'api.deepseek.com'


def validate_model(base_url, model):
    if is_deepseek(base_url) and model in {'deepseek-v41-flash', 'deepseek-v4.1-flash'}:
        raise ValueError('DeepSeek V4.1 Flash 的 API 模型名是 deepseek-flash，请勿把版本名称作为 API 名称')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('模型名称不能为空')
    return model.strip()
