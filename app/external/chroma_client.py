from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import chromadb
from chromadb.api.models.Collection import Collection

from app.config import get_settings
from app.external.embedding import EmbeddingClient

NEWS_COLLECTION_NAME = "news"
FILING_COLLECTION_NAME = "filing"


@dataclass(slots=True)
class ChromaDocument:
    id: str
    document: str
    metadata: dict[str, Any]


class ChromaClient:
    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        settings = get_settings()
        parsed = urlparse(url or settings.chroma_url)
        headers: dict[str, str] = {}
        auth_token = token if token is not None else settings.chroma_token
        if auth_token:
            headers["X-Chroma-Token"] = auth_token

        self._client = chromadb.HttpClient(
            host=parsed.hostname or "localhost",
            port=parsed.port or 8080,
            ssl=parsed.scheme == "https",
            headers=headers or None,
        )

    def heartbeat(self) -> Any:
        return self._client.heartbeat()

    def get_or_create_collection(self, name: str, *, metadata: dict[str, Any] | None = None) -> Collection:
        return self._client.get_or_create_collection(name=name, metadata=metadata)

    def delete_collection(self, name: str) -> None:
        try:
            self._client.delete_collection(name=name)
        except Exception:
            # keep idempotent behavior for validation/setup scripts
            return

    def count(self, name: str) -> int:
        return self.get_or_create_collection(name).count()

    def upsert(
        self,
        name: str,
        documents: Sequence[ChromaDocument],
        *,
        embedding_client: EmbeddingClient,
    ) -> None:
        if not documents:
            return
        collection = self.get_or_create_collection(name)
        texts = [doc.document for doc in documents]
        embeddings = embedding_client.embed_texts(texts)
        collection.upsert(
            ids=[doc.id for doc in documents],
            documents=texts,
            metadatas=[doc.metadata for doc in documents],
            embeddings=embeddings,
        )

    def query(
        self,
        name: str,
        *,
        query_text: str,
        embedding_client: EmbeddingClient,
        where: dict[str, Any] | None = None,
        k: int = 5,
    ) -> dict[str, Any]:
        collection = self.get_or_create_collection(name)
        query_embedding = embedding_client.embed_query(query_text)
        return collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
        )

    def get(
        self,
        name: str,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        collection = self.get_or_create_collection(name)
        kwargs: dict[str, Any] = {}
        if ids is not None:
            kwargs["ids"] = list(ids)
        if where is not None:
            kwargs["where"] = where
        if limit is not None:
            kwargs["limit"] = limit
        return collection.get(**kwargs)

    def delete(
        self,
        name: str,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        collection = self.get_or_create_collection(name)
        kwargs: dict[str, Any] = {}
        if ids is not None:
            kwargs["ids"] = list(ids)
        if where is not None:
            kwargs["where"] = where
        collection.delete(**kwargs)
