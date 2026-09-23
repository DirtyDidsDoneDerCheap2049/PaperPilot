"""SiliconFlow embedding client using its OpenAI-compatible API."""

import os
from openai import OpenAI

SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"


def get_siliconflow_client(config=None) -> OpenAI | None:
    key = os.getenv("SILICONFLOW_API_KEY", "")
    if not key:
        return None
    return OpenAI(api_key=key, base_url=(config or {}).get('base_url',SILICONFLOW_BASE),timeout=60,max_retries=1)


def siliconflow_embedding_fn(config=None):
    """Return a ChromaDB-compatible embedding function using SiliconFlow."""
    client = get_siliconflow_client(config) if config else get_siliconflow_client()
    if client is None:
        return None

    class SfEmbedding:
        @staticmethod
        def name() -> str:
            return "siliconflow"

        def get_config(self) -> dict:
            return {"model": (config or {}).get('model', DEFAULT_MODEL), "base_url": (config or {}).get('base_url', SILICONFLOW_BASE)}

        def default_space(self) -> str:
            return "cosine"

        def supported_spaces(self) -> list[str]:
            return ["cosine", "l2", "ip"]

        @staticmethod
        def build_from_config(config: dict):
            return siliconflow_embedding_fn(config)

        def is_legacy(self) -> bool:
            return False

        def __call__(self, input: list[str]) -> list[list[float]]:
            if not input:
                return []
            resp = client.embeddings.create(
                    model=(config or {}).get('model',DEFAULT_MODEL),
                input=[t[:8000] for t in input],
            )
            return [d.embedding for d in resp.data]

        def embed_query(self, input: list[str]) -> list[list[float]]:
            return self.__call__(input)

    return SfEmbedding()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Direct embedding using SiliconFlow."""
    client = get_siliconflow_client()
    if client is None:
        raise RuntimeError("SILICONFLOW_API_KEY not set")
    resp = client.embeddings.create(
        model=DEFAULT_MODEL,
        input=[t[:8000] for t in texts],
    )
    return [d.embedding for d in resp.data]


def embed_single(text: str) -> list[float]:
    return embed_texts([text])[0]
