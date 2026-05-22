from __future__ import annotations

from uuid import uuid4

from app.external.chroma_client import ChromaClient
from app.external.embedding import EmbeddingClient


def main() -> None:
    chroma = ChromaClient()
    heartbeat = chroma.heartbeat()
    print(f"[OK] ChromaDB heartbeat={heartbeat}")

    collection_name = f"test_{uuid4().hex[:8]}"
    doc_id = f"test:{uuid4()}"
    document = "삼성전자 분기보고서"

    try:
        embedder = EmbeddingClient()
        embedding = embedder.embed_texts([document])[0]
        print(f"[OK] embedding dim={len(embedding)}")

        chroma.upsert_documents(
            collection_name,
            ids=[doc_id],
            documents=[document],
            embeddings=[embedding],
            metadatas=[{"symbol": "005930", "source_type": "test"}],
        )
        print("[OK] upsert")

        query_result = chroma.query(collection_name, query_embeddings=[embedding], n_results=1)
        if not query_result.get("ids") or not query_result["ids"][0]:
            raise RuntimeError("query returned no result")
        print("[OK] query")

        chroma.delete_by_ids(collection_name, [doc_id])
        if chroma.count(collection_name) != 0:
            raise RuntimeError("delete failed: collection is not empty")
        print("[OK] delete")
    finally:
        try:
            chroma._client.delete_collection(collection_name)
        except Exception:
            pass

    print("[PASS] validate_chroma_connection")


if __name__ == "__main__":
    main()
