from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from app.config import get_settings
from app.external.chroma_client import (
    ChromaClient,
    ChromaDocument,
)
from app.external.embedding import DeterministicEmbeddingClient

NEWS_VALIDATE_COLLECTION = "news_validate"
FILING_VALIDATE_COLLECTION = "filing_validate"


@dataclass(slots=True)
class ValidationResult:
    heartbeat_ok: bool
    news_count_after_upsert: int
    filing_count_after_upsert: int
    query_hit_id: str | None
    delete_ok: bool


def main() -> None:
    settings = get_settings()
    client = ChromaClient(url=settings.chroma_url, token=settings.chroma_token)
    embeddings = DeterministicEmbeddingClient()

    heartbeat = client.heartbeat()

    client.delete_collection(NEWS_VALIDATE_COLLECTION)
    client.delete_collection(FILING_VALIDATE_COLLECTION)

    client.upsert(
        NEWS_VALIDATE_COLLECTION,
        documents=[
            ChromaDocument(
                id="news-doc-1",
                document="삼성전자 반도체 실적과 시장 반응을 정리한 뉴스 기사 본문입니다.",
                metadata={"symbol": "005930", "source_type": "news", "source_id": "news-doc-1"},
            ),
            ChromaDocument(
                id="news-doc-2",
                document="SK하이닉스 HBM 수요와 메모리 업황을 다룬 뉴스 기사 본문입니다.",
                metadata={"symbol": "000660", "source_type": "news", "source_id": "news-doc-2"},
            ),
        ],
        embedding_client=embeddings,
    )
    client.upsert(
        FILING_VALIDATE_COLLECTION,
        documents=[
            ChromaDocument(
                id="filing-doc-1",
                document="정기 공시 본문입니다. 매출과 영업이익, 투자 계획이 포함됩니다.",
                metadata={"symbol": "005930", "source_type": "filing", "source_id": "filing-doc-1"},
            )
        ],
        embedding_client=embeddings,
    )

    query_result = client.query(
        NEWS_VALIDATE_COLLECTION,
        query_text="메모리 업황과 HBM 수요 기사",
        embedding_client=embeddings,
        where={"symbol": "000660"},
        k=1,
    )
    hit_ids = query_result.get("ids", [[]])
    query_hit_id = hit_ids[0][0] if hit_ids and hit_ids[0] else None

    client.delete(NEWS_VALIDATE_COLLECTION, ids=["news-doc-1"])
    remaining = client.get(NEWS_VALIDATE_COLLECTION, ids=["news-doc-1"])
    delete_ok = not remaining.get("ids")

    result = ValidationResult(
        heartbeat_ok=bool(heartbeat),
        news_count_after_upsert=client.count(NEWS_VALIDATE_COLLECTION),
        filing_count_after_upsert=client.count(FILING_VALIDATE_COLLECTION),
        query_hit_id=query_hit_id,
        delete_ok=delete_ok,
    )
    print(json.dumps(asdict(result), ensure_ascii=False))

    client.delete_collection(NEWS_VALIDATE_COLLECTION)
    client.delete_collection(FILING_VALIDATE_COLLECTION)


if __name__ == "__main__":
    main()
