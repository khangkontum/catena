from __future__ import annotations

from collections.abc import Iterable

from openai import AsyncOpenAI

from catena.config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings):
        settings.require_gateway()
        assert settings.gateway_base_url is not None
        assert settings.gateway_api_key is not None
        assert settings.embedding_model is not None
        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.gateway_base_url,
            api_key=settings.gateway_api_key,
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for batch in _batched(texts, self.settings.embedding_batch_size):
            response = await self.client.embeddings.create(
                model=self.settings.embedding_model or "",
                input=batch,
            )
            vectors.extend([list(item.embedding) for item in response.data])
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding gateway returned {len(vectors)} vectors for {len(texts)} texts"
            )
        return vectors


async def embed_query(settings: Settings, query: str) -> list[float]:
    vectors = await EmbeddingClient(settings).embed_texts([query])
    if not vectors:
        raise RuntimeError("Embedding gateway returned no query vector")
    return vectors[0]


def _batched(items: list[str], batch_size: int) -> Iterable[list[str]]:
    size = max(1, batch_size)
    for start in range(0, len(items), size):
        yield items[start : start + size]
