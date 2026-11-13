import os
from abc import ABC, abstractmethod

import aiohttp


class EmbeddingServiceError(Exception):
    pass


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Default: one call per text. Providers with a batch endpoint override this.
        return [await self.embed_text(text) for text in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(self):
        api_url = os.environ.get("EMBEDDING_API_URL")
        if not api_url:
            raise EmbeddingServiceError("EMBEDDING_API_URL is required for the openai_compatible embedding provider")
        api_model = os.environ.get("EMBEDDING_API_MODEL")
        if not api_model:
            raise EmbeddingServiceError("EMBEDDING_API_MODEL is required for the openai_compatible embedding provider")
        self._url = api_url.rstrip("/") + "/v1/embeddings"
        self._model = api_model
        self._api_key = os.environ.get("EMBEDDING_API_KEY", "")
        timeout = float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "30"))
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._batch_size = max(1, int(os.environ.get("EMBEDDING_BATCH_SIZE", "64")))
        self.model_name = api_model

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # One request per batch of EMBEDDING_BATCH_SIZE texts (the OpenAI embeddings API accepts a list).
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._request(texts[start:start + self._batch_size]))
        return vectors

    async def _request(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model, "input": texts}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
            embeddings = data.get("data", [])
            if len(embeddings) != len(texts):
                raise EmbeddingServiceError(
                    f"Embedding service returned {len(embeddings)} vectors for {len(texts)} inputs"
                )
            return [e["embedding"] for e in sorted(embeddings, key=lambda e: e["index"])]
        except aiohttp.ClientError as exc:
            raise EmbeddingServiceError(f"Embedding service unavailable: {exc}") from exc
        except TimeoutError as exc:
            raise EmbeddingServiceError("Embedding service timed out") from exc


def get_embedding_provider() -> EmbeddingProvider:
    provider_type = os.environ.get("EMBEDDING_PROVIDER", "openai_compatible")
    if provider_type == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {provider_type!r}")
