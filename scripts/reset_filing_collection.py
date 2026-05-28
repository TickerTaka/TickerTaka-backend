"""filing Chroma 컬렉션 삭제 스크립트.

실행:
    python scripts/reset_filing_collection.py

삭제 후 재색인:
    python scripts/reindex_all_filings.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME


def main() -> None:
    client = ChromaClient()
    try:
        count = client.count(FILING_COLLECTION_NAME)
        print(f"[reset] 현재 '{FILING_COLLECTION_NAME}' 컬렉션: {count}개 문서")
    except Exception:
        print(f"[reset] '{FILING_COLLECTION_NAME}' 컬렉션 없음 또는 조회 실패")

    client.delete_collection(FILING_COLLECTION_NAME)
    print(f"[reset] '{FILING_COLLECTION_NAME}' 컬렉션 삭제 완료")
    print("[reset] 다음 reindex 실행 시 새 embedding 차원으로 재생성됩니다")
    print("[reset] -> python scripts/reindex_all_filings.py")


if __name__ == "__main__":
    main()
