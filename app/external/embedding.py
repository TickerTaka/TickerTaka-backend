from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib
import math
from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


class EmbeddingClient(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(slots=True)
class DeterministicEmbeddingClient:
    """Local-only deterministic embeddings for connection/flow validation.

    This is not intended for production retrieval quality. It exists so local
    Chroma validation can run without external embedding API calls.
    """

    dimensions: int = 64

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_single(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_single(text)

    def _embed_single(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        normalized = text.strip().lower()
        if not normalized:
            return vector

        for token in normalized.split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(self.dimensions):
                byte = digest[index % len(digest)]
                vector[index] += (byte / 255.0) - 0.5

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


@dataclass(slots=True)
class OpenAIEmbeddingClient:
    model: str
    api_key: str
    _client: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed_batch(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


@dataclass(slots=True)
class HuggingFaceEmbeddingClient:
    model: str
    _model: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sentence-transformers is required for EMBEDDING_PROVIDER=huggingface. "
                "Install it with `pip install sentence-transformers` or `pip install -r requirements.txt`."
            ) from exc

        self._model = SentenceTransformer(self.model)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()


def get_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    provider = settings.embedding_provider.strip().lower()
    if provider == "huggingface":
        return HuggingFaceEmbeddingClient(model=settings.embedding_model)
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings")
        return OpenAIEmbeddingClient(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
    raise RuntimeError(f"unsupported embedding provider: {settings.embedding_provider}")
