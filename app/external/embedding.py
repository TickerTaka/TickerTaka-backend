from __future__ import annotations

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError
from sentence_transformers import SentenceTransformer
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    _DEFAULT_HF_MODEL = "jhgan/ko-sroberta-multitask"
    _OPENAI_DEFAULT_MODELS = {"text-embedding-3-small", "openai/text-embedding-3-small"}

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = (provider or settings.embedding_provider or "huggingface").lower()
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.embedding_model
        self.client: OpenAI | None = None
        self.local_model: SentenceTransformer | None = None

        if self.provider in {"hf", "huggingface", "sentence-transformers", "sentence_transformers"}:
            self.provider = "huggingface"
            if self.model in self._OPENAI_DEFAULT_MODELS:
                self.model = self._DEFAULT_HF_MODEL
            self.local_model = SentenceTransformer(self.model)
        elif self.provider == "openai":
            if not self.api_key:
                raise EmbeddingError("OPENAI_API_KEY is not configured")
            if self.model == "openai/text-embedding-3-small":
                self.model = "text-embedding-3-small"
            self.client = OpenAI(api_key=self.api_key)
        else:
            raise EmbeddingError(
                f"Unsupported EMBEDDING_PROVIDER={self.provider!r}; use 'huggingface' or 'openai'"
            )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if self.provider == "huggingface":
            return self._embed_texts_huggingface(texts)

        embeddings: list[list[float]] = []
        for start in range(0, len(texts), 100):
            batch = [text if text.strip() else " " for text in texts[start : start + 100]]
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]

    def _embed_texts_huggingface(self, texts: list[str]) -> list[list[float]]:
        if self.local_model is None:
            raise EmbeddingError("HuggingFace embedding model is not initialized")
        normalized = [text if text.strip() else " " for text in texts]
        embeddings = self.local_model.encode(
            normalized,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [embedding.tolist() for embedding in embeddings]

    @retry(
        retry=retry_if_exception_type((APIError, RateLimitError, APITimeoutError, APIConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
        reraise=True,
    )
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.client is None:
            raise EmbeddingError("OpenAI embedding client is not initialized")
        response = self.client.embeddings.create(model=self.model, input=texts)
        data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in data]
