import os, json, logging
import asyncio
import time
from openai import AsyncOpenAI
from src.llm.response_style import style_messages
from src.llm.provider_policy import DEFAULT_MODEL, EFFORTS, is_deepseek, validate_model

logger = logging.getLogger(__name__)


class LLMJsonError(Exception):
    pass


class LLMOutputError(RuntimeError):
    """A response arrived, but it is not a complete usable answer."""


def check_output(finish_reason, content):
    if finish_reason == 'length':
        raise LLMOutputError('达到本次请求的输出预算，回答未完成；可在设置中增加输出预算或调整思考强度后重试。这不是模型上下文上限')
    if finish_reason == 'content_filter':
        raise LLMOutputError('模型服务未返回完整回答，请检查问题或服务限制')
    if not content.strip():
        raise LLMOutputError('模型返回了空正文，请检查模型配置或缩小问题后重试')


class DeepSeekClient:
    def __init__(self, base_url: str = "https://api.deepseek.com",
                 api_key_env: str = "DEEPSEEK_API_KEY",
                 fast_model: str = DEFAULT_MODEL,
                 reasoning_model: str = DEFAULT_MODEL,
                 max_output_tokens: int | None = None, timeout_seconds: int = 600,
                 regular_effort: str = 'max', analysis_effort: str = 'max',
                 capability_profile: str = 'auto', token_parameter: str = 'max_tokens',
                 json_output: str = 'auto', extra_body_params: dict | None = None):
        self.api_key_env = api_key_env
        self._base_url = base_url
        self._api_key = os.getenv(api_key_env, "")
        self._ready = bool(self._api_key)
        if not self._ready:
            logger.warning(f"Env var {api_key_env} not set — LLM calls will fail")
        self.client = AsyncOpenAI(api_key=self._api_key,
                                  base_url=self._base_url, timeout=timeout_seconds, max_retries=0) if self._ready else None
        self.calls = 0
        from src.runtime.research_limits import DEFAULT_MAX_CALLS
        self.max_calls = DEFAULT_MAX_CALLS
        self.fast_model = fast_model
        self.reasoning_model = reasoning_model
        self.max_output_tokens = max_output_tokens or (65536 if is_deepseek(base_url) else 4096)
        self.timeout_seconds = timeout_seconds
        self.regular_effort = regular_effort
        self.analysis_effort = analysis_effort
        self.capability_profile=capability_profile
        self.token_parameter=token_parameter
        self.json_output=json_output
        self.extra_body_params=extra_body_params or {}
        self.activity_callback = None

    async def _activity(self, **data):
        if self.activity_callback:
            await self.activity_callback(data)

    async def _stream_response(self, messages, options):
        parts, finish_reason = [], None
        received, last_update = 0, 0.0
        pending, last_delta = '', 0.0
        reasoning_tail, reasoning_chars, last_thinking = '', 0, 0.0
        stream = await self.client.chat.completions.create(messages=messages, stream=True, **options)
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = getattr(choice, 'finish_reason', None) or finish_reason
                reasoning = getattr(choice.delta, 'reasoning_content', None) or ''
                if reasoning:
                    reasoning_chars += len(reasoning)
                    reasoning_tail = (reasoning_tail + reasoning)[-400:]
                    if time.monotonic() - last_thinking >= .35:
                        await self._activity(phase='thinking', call=self.calls,
                                             preview=reasoning_tail.strip().split('\n')[-1][-220:],
                                             reasoning_chars=reasoning_chars)
                        last_thinking = time.monotonic()
                text = getattr(choice.delta, 'content', None) or ''
                if text:
                    parts.append(text)
                    received += len(text)
                    pending += text
                    if time.monotonic() - last_delta >= .2 or len(pending) >= 2000:
                        await self._activity(phase='delta', content=pending, call=self.calls)
                        pending = ''
                        last_delta = time.monotonic()
                # Only a bounded live preview; reasoning never enters response content or reports.
                if time.monotonic() - last_update >= 1:
                    await self._activity(phase='receiving' if received else 'thinking' if reasoning_chars else 'processing', received_chars=received)
                    last_update = time.monotonic()
            content = ''.join(parts)
            if pending:
                await self._activity(phase='delta', content=pending, call=self.calls)
            check_output(finish_reason, content)
            await self._activity(phase='received', received_chars=received)
            return content
        finally:
            await stream.close()

    def configure(self, *, api_key: str | None = None,
                  base_url: str | None = None,
                  fast_model: str | None = None,
                  reasoning_model: str | None = None,
                  max_output_tokens: int | None = None, timeout_seconds: int | None = None,
                  regular_effort: str | None = None, analysis_effort: str | None = None,
                  capability_profile: str | None = None, token_parameter: str | None = None,
                  json_output: str | None = None, extra_body_params: dict | None = None) -> None:
        """Apply settings changed at runtime in place.

        Kept in-place on purpose: the orchestrator and agents hold a reference to
        this client, so replacing the object would leave them using a stale one.
        """
        if fast_model:
            self.fast_model = fast_model
        if reasoning_model:
            self.reasoning_model = reasoning_model
        rebuild = False
        if max_output_tokens is not None:
            self.max_output_tokens = max_output_tokens
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds
            rebuild = True
        if regular_effort is not None:
            self.regular_effort = regular_effort
        if analysis_effort is not None:
            self.analysis_effort = analysis_effort
        for name,value in (('capability_profile',capability_profile),('token_parameter',token_parameter),('json_output',json_output),('extra_body_params',extra_body_params)):
            if value is not None:setattr(self,name,value)
        if base_url and base_url != self._base_url:
            self._base_url = base_url
            rebuild = True
        if api_key is not None:
            self._api_key = api_key
            rebuild = True
        ready_now = bool(self._api_key)
        if ready_now != self._ready:
            self._ready = ready_now
            rebuild = True
        if not self._ready:
            self.client = None
        elif rebuild:
            self.client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, timeout=self.timeout_seconds, max_retries=0)

    def request_options(self, model, *, purpose='regular', structured=False):
        options = {'model': validate_model(self._base_url, model)}
        if self.token_parameter not in {'max_tokens','max_completion_tokens','omit'}:
            raise ValueError('不支持的输出预算参数名')
        if self.token_parameter!='omit':options[self.token_parameter]=self.max_output_tokens
        deepseek=self.capability_profile=='deepseek' or (self.capability_profile=='auto' and is_deepseek(self._base_url))
        if deepseek:
            effort = self.analysis_effort if purpose == 'analysis' else self.regular_effort
            if effort not in EFFORTS:
                raise ValueError('不支持的 DeepSeek 思考强度')
            options['extra_body'] = {'thinking': {'type': 'disabled' if effort == 'none' else 'enabled'}}
            if effort != 'none':
                options['reasoning_effort'] = effort
        if structured and (self.json_output=='json_object' or (self.json_output=='auto' and deepseek)):
            options['response_format'] = {'type': 'json_object'}
        if self.extra_body_params:
            options['extra_body']={**options.get('extra_body',{}),**self.extra_body_params}
        return options

    async def close(self):
        if self.client:
            await self.client.close()

    def _reserve_call(self):
        if self.calls >= self.max_calls:
            raise RuntimeError(f'本次任务达到 {self.max_calls} 次模型调用上限；已有资料保留，未完成分析不能作为完整结论')
        self.calls += 1

    def _ensure_ready(self):
        if not self._ready or self.client is None:
            raise RuntimeError("请先在设置中配置模型服务和 API Key")

    async def _chat_impl(self, messages: list[dict], model: str,
                         max_retries: int = 2, *, purpose='regular', structured=False) -> str:
        self._ensure_ready()
        options = self.request_options(model, purpose=purpose, structured=structured)
        for attempt in range(max_retries + 1):
            try:
                self._reserve_call()
                await self._activity(phase='waiting', model=model, call=self.calls, attempt=attempt+1,
                                     timeout_seconds=self.timeout_seconds, received_chars=0,
                                     presentation=getattr(self,'presentation','research'))
                if self.activity_callback:
                    return await self._stream_response(messages, options)
                resp = await self.client.chat.completions.create(
                    messages=messages, stream=False, **options
                )
                choice = resp.choices[0]
                content = choice.message.content or ""
                check_output(getattr(choice, 'finish_reason', None), content)
                return content
            except Exception as e:
                status = getattr(e, 'status_code', None)
                if status == 402:
                    await self._activity(phase='failed', error_type='ProviderBalanceError')
                    raise LLMOutputError('模型服务余额不足，研究未完成。已取得的资料和分析保留；补充余额后可继续处理。') from e
                if attempt == max_retries or (status is not None and status < 500 and status != 429) or isinstance(e, RuntimeError):
                    await self._activity(phase='failed', error_type=type(e).__name__)
                    raise
                logger.warning('Model call retry %s (%s)', attempt + 1, type(e).__name__)
                await self._activity(phase='retrying', retry_in_seconds=min(2 ** attempt, 8))
                await asyncio.sleep(min(2 ** attempt, 8))

    async def chat(self, messages: list[dict],
                   model: str | None = None, *, purpose='regular') -> str:
        return await self._chat_impl(style_messages(messages), model or self.fast_model, purpose=purpose)

    async def chat_stream(self, messages: list[dict],
                          model: str | None = None):
        """Yield text deltas from a streaming completion."""
        self._ensure_ready()
        options = self.request_options(model or self.fast_model)
        self._reserve_call()
        stream = await self.client.chat.completions.create(
            messages=style_messages(messages), stream=True, **options
        )
        parts = []
        finish_reason = None
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = getattr(choice, 'finish_reason', None) or finish_reason
                delta = choice.delta
                if delta and delta.content:
                    parts.append(delta.content)
                    yield delta.content
            check_output(finish_reason, ''.join(parts))
        finally:
            close = getattr(stream, 'close', None)
            if close:
                await close()

    async def chat_json(self, messages: list[dict],
                        model: str | None = None, *, purpose: str = 'regular') -> dict:
        m = model or self.fast_model
        has_json = any("JSON" in (msg.get("content", "")) for msg in messages)
        if not has_json:
            messages = list(messages) + [
                {"role": "user", "content": "请只输出合法 JSON，不要 markdown 代码块，不要解释。"}
            ]

        messages = style_messages(messages, structured=True)
        raw = await self._chat_impl(messages, m, purpose=purpose, structured=True)
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            raw = "\n".join(lines)
        raw = raw.strip()

        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise LLMJsonError('模型必须返回 JSON 对象')
            return value
        except json.JSONDecodeError as e:
            retry_msg = (
                f"你的输出不是合法 JSON：{e}\n"
                f"原输出（前500字符）：{raw[:500]}\n"
                f"请修正后只输出合法 JSON。"
            )
            messages2 = list(messages) + [
                {"role": "user", "content": retry_msg}
            ]
            raw2 = await self._chat_impl(messages2, m, purpose=purpose, structured=True)
            raw2 = raw2.strip()
            if raw2.startswith("```"):
                lines = raw2.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                raw2 = "\n".join(lines)
            try:
                value = json.loads(raw2.strip())
                if not isinstance(value, dict):
                    raise LLMJsonError('模型必须返回 JSON 对象')
                return value
            except json.JSONDecodeError:
                raise LLMJsonError(
                    "模型返回的 JSON 在修正后仍无法解析"
                )
