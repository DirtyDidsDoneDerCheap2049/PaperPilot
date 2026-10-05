"""Request metadata follows its asyncio task, never a shared current paper."""
from contextlib import contextmanager
from contextvars import ContextVar

request_metadata = ContextVar('model_request_metadata', default={})


@contextmanager
def model_context(**metadata):
    token = request_metadata.set({**request_metadata.get(), **metadata})
    try:
        yield
    finally:
        request_metadata.reset(token)
