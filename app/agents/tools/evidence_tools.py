from langchain_core.tools import tool
import logging

from app.domain.evidence_retrieval import search_evidence_for_symbol

logger = logging.getLogger(__name__)

@tool
def search_evidence(query: str, symbol: str, top_k: int = 3) -> list[dict]:
    """ChromaDB + cache metadata를 사용해 뉴스/공시 근거를 검색한다."""
    try:
        return search_evidence_for_symbol(query=query, symbol=symbol, top_k=top_k)
    except Exception:
        logger.exception("search_evidence failed for %s", symbol)
        return []

async def embed_and_store(symbol, articles, source_type="NEWS"):
    pass
