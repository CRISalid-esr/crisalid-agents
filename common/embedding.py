import os
from abc import ABC, abstractmethod

import aiohttp


class EmbeddingServiceError(Exception):
    pass


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        ...


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

    async def embed_text(self, text: str) -> list[float]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model, "input": [text]}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
            embeddings = data.get("data", [])
            if not embeddings:
                raise EmbeddingServiceError("Embedding service returned no data")
            return sorted(embeddings, key=lambda e: e["index"])[0]["embedding"]
        except aiohttp.ClientError as exc:
            raise EmbeddingServiceError(f"Embedding service unavailable: {exc}") from exc
        except TimeoutError as exc:
            raise EmbeddingServiceError("Embedding service timed out") from exc


def get_embedding_provider() -> EmbeddingProvider:
    provider_type = os.environ.get("EMBEDDING_PROVIDER", "openai_compatible")
    if provider_type == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {provider_type!r}")
