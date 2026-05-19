from langchain_core.tools import tool

@tool
def search_evidence(query: str, symbol: str, top_k: int = 3) -> list[dict]:
    """뉴스/공시 근거 검색 (ChromaDB 데이터 준비 전 더미)"""
    return []

async def embed_and_store(symbol, articles, source_type="NEWS"):
    pass
