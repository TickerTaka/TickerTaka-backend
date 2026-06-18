# app/agents/nodes/utils.py
from __future__ import annotations
import json


def extract_evidences(messages) -> list[dict]:
    """LangChain ToolMessage에서 search_evidence 결과 추출.

    OpenAI(ToolMessage, content=JSON str)와
    Anthropic(tool_result block) 포맷 모두 지원.
    """
    evidences = []
    for msg in messages:
        # OpenAI 포맷: ToolMessage, content는 JSON 문자열
        if hasattr(msg, "type") and msg.type == "tool" and hasattr(msg, "content"):
            try:
                parsed = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(parsed, list):
                    evidences.extend(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        # Anthropic 포맷: tool_result block
        elif hasattr(msg, "content") and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if isinstance(block.get("content"), list):
                        evidences.extend(block["content"])
    return evidences
