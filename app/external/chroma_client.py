from __future__ import annotations

from urllib.parse import urlparse

import chromadb

from app.config import get_settings


class ChromaClient:
    """Small ChromaDB HttpClient wrapper for local RAG indexing."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        settings = get_settings()
        chroma_url = url or settings.chroma_url
        chroma_token = token if token is not None else settings.chroma_token

        parsed = urlparse(chroma_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ssl = parsed.scheme == "https"
        headers = {"Authorization": f"Bearer {chroma_token}"} if chroma_token else None

        self._client = chromadb.HttpClient(
            host=host,
            port=port,
            ssl=ssl,
            headers=headers,
        )

    def heartbeat(self) -> int:
        return self._client.heartbeat()

    def get_or_create_collection(self, name: str):
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_documents(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        collection = self.get_or_create_collection(collection_name)
        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def get_documents(self, collection_name: str, ids: list[str]) -> dict:
        collection = self.get_or_create_collection(collection_name)
        return collection.get(ids=ids)

    def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict:
        collection = self.get_or_create_collection(collection_name)
        return collection.query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

    def delete_by_ids(self, collection_name: str, ids: list[str]) -> None:
        if not ids:
            return
        collection = self.get_or_create_collection(collection_name)
        collection.delete(ids=ids)

    def delete_by_symbol(self, collection_name: str, symbol: str) -> None:
        collection = self.get_or_create_collection(collection_name)
        collection.delete(where={"symbol": symbol})

    def count(self, collection_name: str) -> int:
        collection = self.get_or_create_collection(collection_name)
        return collection.count()

    def get_existing_ids(self, collection_name: str, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        collection = self.get_or_create_collection(collection_name)
        result = collection.get(ids=ids, include=[])
        return set(result.get("ids") or [])
