from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

_NOTION_RICH_TEXT_LIMIT = 2000
_NOTION_BLOCK_LIMIT = 100
_NOTION_URL_RE = re.compile(r"https://www\.notion\.so/[^\s\"'>]+")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{32}\b|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


class NotionMcpError(RuntimeError):
    """Raised when MCP-based Notion publish fails."""


@dataclass(slots=True)
class NotionPublishResult:
    page_id: str
    page_url: str


@dataclass(slots=True)
class DebatePublishPayload:
    session_id: str
    symbol: str
    symbol_name: str
    category: str
    started_at: datetime | None
    summary_content: str | None
    key_points: list[str]
    statements: list[dict[str, Any]]


def _run_async(coro) -> Any:
    """동기 컨텍스트에서 async 코루틴을 안전히 실행.

    실행 중 event loop가 없으면 asyncio.run, 있으면(=async 호출부) 별도 스레드의
    새 loop에서 돌려 nested-loop 에러를 피한다 → publish_debate의 sync 인터페이스 보존.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="notion-mcp") as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


class NotionMcpClient:
    """Notion MCP 서버에 **공식 `mcp` SDK 클라이언트**(`ClientSession`+`stdio_client`)로 접속해
    토론을 발행한다. (이전: 자체 stdio JSON-RPC 구현 → 표준 SDK로 교체, tools/call 핸드셰이크는 SDK가 처리)
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def publish_debate(self, payload: DebatePublishPayload) -> NotionPublishResult:
        if not self._settings.notion_token:
            raise NotionMcpError("NOTION_TOKEN is not configured")
        if not self._settings.notion_database_id:
            raise NotionMcpError("NOTION_DATABASE_ID is not configured")
        if not self._settings.notion_mcp_server_command:
            raise NotionMcpError("NOTION_MCP_SERVER_COMMAND is not configured")

        arguments = {
            "parent": {"database_id": self._settings.notion_database_id},
            "properties": self._build_properties(payload),
            "children": self._build_children(payload),
        }
        result = _run_async(self._call_tool(arguments))
        page_id, page_url = self._extract_page_fields(result)
        if not page_id or not page_url:
            raise NotionMcpError("MCP response did not include Notion page id/url")
        return NotionPublishResult(page_id=page_id, page_url=page_url)

    async def _call_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = os.environ.copy()
        env["NOTION_TOKEN"] = self._settings.notion_token
        params = StdioServerParameters(
            command=self._settings.notion_mcp_server_command,
            args=shlex.split(self._settings.notion_mcp_server_args),
            env=env,
        )
        timeout = self._settings.notion_mcp_timeout_seconds

        async def _do() -> dict[str, Any]:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()  # SDK가 initialize/initialized 핸드셰이크 처리
                    result = await session.call_tool(
                        self._settings.notion_mcp_tool_name, arguments=arguments
                    )
            if getattr(result, "isError", False):
                raise NotionMcpError(f"MCP tool error: {result.model_dump(mode='json')}")
            return result.model_dump(mode="json")  # content/structuredContent/text → 기존 추출기 호환

        try:
            return await asyncio.wait_for(_do(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise NotionMcpError(f"MCP request timed out after {timeout}s.") from exc

    def _build_properties(self, payload: DebatePublishPayload) -> dict[str, Any]:
        # API-post-page properties use Notion REST typed objects keyed by property name.
        published_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        created_at = (
            payload.started_at.replace(microsecond=0).isoformat() if payload.started_at else published_at
        )
        title = f"[{payload.symbol_name or payload.symbol}] {payload.category} debate"
        return {
            "Name": {"title": [self._rich_text(title)]},
            "Session ID": {"rich_text": [self._rich_text(payload.session_id)]},
            "Symbol": {"rich_text": [self._rich_text(payload.symbol)]},
            "Category": {"select": {"name": payload.category}},
            "Created At": {"date": {"start": created_at}},
            "Published At": {"date": {"start": published_at}},
        }

    def _build_children(self, payload: DebatePublishPayload) -> list[dict[str, Any]]:
        # blockObjectRequest only allows paragraph / bulleted_list_item (no headings),
        # and rejects extra keys (no "object": "block").
        blocks: list[dict[str, Any]] = []
        blocks += self._paragraph("Summary")
        blocks += self._paragraph(payload.summary_content or "No summary available.")
        if payload.key_points:
            blocks += self._paragraph("Key Points")
            for point in payload.key_points:
                blocks += self._bullet(point)
        if payload.statements:
            blocks += self._paragraph("Highlights")
            for statement in payload.statements[:8]:
                blocks += self._paragraph(f"{statement['agent_role']} · {statement['round']}")
                blocks += self._paragraph(str(statement["content"]))
                for line in (statement.get("evidence_lines") or [])[:3]:
                    blocks += self._bullet(line)
                if len(blocks) >= _NOTION_BLOCK_LIMIT:
                    break
        return blocks[:_NOTION_BLOCK_LIMIT]

    @staticmethod
    def _rich_text(content: str) -> dict[str, Any]:
        return {"type": "text", "text": {"content": (content or "")[:_NOTION_RICH_TEXT_LIMIT]}}

    def _paragraph(self, content: str) -> list[dict[str, Any]]:
        return [
            {"type": "paragraph", "paragraph": {"rich_text": [self._rich_text(chunk)]}}
            for chunk in self._chunk_text(content)
        ]

    def _bullet(self, content: str) -> list[dict[str, Any]]:
        return [
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [self._rich_text(chunk)]}}
            for chunk in self._chunk_text(content)
        ]

    def _chunk_text(self, content: str) -> list[str]:
        normalized = (content or "").strip()
        if not normalized:
            return [""]
        return [normalized[i : i + _NOTION_RICH_TEXT_LIMIT] for i in range(0, len(normalized), _NOTION_RICH_TEXT_LIMIT)]

    def _extract_page_fields(self, result: Any) -> tuple[str | None, str | None]:
        stack: list[Any] = [result]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                page_id = current.get("page_id") or current.get("id")
                page_url = current.get("page_url") or current.get("url")
                if isinstance(page_url, str) and not isinstance(page_id, str):
                    page_id = self._extract_page_id(page_url)
                if isinstance(page_id, str) and isinstance(page_url, str):
                    return page_id, page_url
                structured = current.get("structuredContent")
                content = current.get("content")
                if structured is not None:
                    stack.append(structured)
                if isinstance(content, list):
                    stack.extend(content)
                text_value = current.get("text")
                if isinstance(text_value, str):
                    stack.extend(self._parse_text_candidates(text_value))
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, str):
                stack.extend(self._parse_text_candidates(current))
        return None, None

    def _parse_text_candidates(self, text: str) -> list[Any]:
        stripped = text.strip()
        if not stripped:
            return []
        candidates: list[Any] = []
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                candidates.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        url_match = _NOTION_URL_RE.search(stripped)
        if url_match:
            page_url = url_match.group(0)
            page_id = self._extract_page_id(stripped) or self._extract_page_id(page_url)
            candidates.append({"page_url": page_url, "page_id": page_id})
        return candidates

    def _extract_page_id(self, text: str) -> str | None:
        match = _UUID_RE.search(text)
        if not match:
            return None
        return match.group(0).replace("-", "")
