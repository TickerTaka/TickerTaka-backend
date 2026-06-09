from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

_JSONRPC_VERSION = "2.0"
_MCP_PROTOCOL_VERSION = "2024-11-05"
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


class _StdioJsonRpcClient:
    def __init__(self, command: str, args: list[str], *, env: dict[str, str], timeout_seconds: int) -> None:
        self._command = command
        self._args = args
        self._env = env
        self._timeout_seconds = timeout_seconds
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._last_stderr = ""

    def __enter__(self) -> "_StdioJsonRpcClient":
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="notion-mcp-read")
        self._proc = subprocess.Popen(
            [self._command, *self._args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._terminate_process()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def initialize(self) -> None:
        _ = self.request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tickertaka-backend", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": _JSONRPC_VERSION, "id": req_id, "method": method, "params": params})
        while True:
            message = self._read_with_timeout()
            if "id" not in message:
                continue
            if message["id"] != req_id:
                continue
            if "error" in message:
                raise NotionMcpError(f"MCP request failed: {message['error']}")
            result = message.get("result")
            if isinstance(result, dict) and result.get("isError") is True:
                raise NotionMcpError(f"MCP tool error: {result}")
            return result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": _JSONRPC_VERSION, "method": method, "params": params})

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise NotionMcpError("MCP process is not running")
        body = json.dumps(payload).encode("utf-8") + b"\n"
        self._proc.stdin.write(body)
        self._proc.stdin.flush()

    def _read_with_timeout(self) -> dict[str, Any]:
        if self._executor is None:
            raise NotionMcpError("MCP read executor is not running")
        future = self._executor.submit(self._read)
        try:
            return future.result(timeout=self._timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            self._terminate_process()
            stderr = f" stderr={self._last_stderr}" if self._last_stderr else ""
            raise NotionMcpError(
                f"MCP request timed out after {self._timeout_seconds}s.{stderr}"
            ) from exc

    def _read(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise NotionMcpError("MCP process is not running")
        while True:
            line = self._proc.stdout.readline()
            if not line:
                self._collect_stderr()
                stderr = f" stderr={self._last_stderr}" if self._last_stderr else ""
                raise NotionMcpError(f"Empty MCP response body.{stderr}")
            decoded = line.decode("utf-8").strip()
            if not decoded:
                continue
            try:
                return json.loads(decoded)
            except json.JSONDecodeError as exc:
                self._collect_stderr()
                stderr = f" stderr={self._last_stderr}" if self._last_stderr else ""
                raise NotionMcpError(f"Failed to decode MCP response body.{stderr}") from exc

    def _terminate_process(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._collect_stderr()

    def _collect_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            stderr = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
        except Exception:
            stderr = ""
        if stderr:
            self._last_stderr = stderr[-2000:]


class NotionMcpClient:
    def __init__(self) -> None:
        self._settings = get_settings()

    def publish_debate(self, payload: DebatePublishPayload) -> NotionPublishResult:
        if not self._settings.notion_token:
            raise NotionMcpError("NOTION_TOKEN is not configured")
        if not self._settings.notion_database_id:
            raise NotionMcpError("NOTION_DATABASE_ID is not configured")
        if not self._settings.notion_mcp_server_command:
            raise NotionMcpError("NOTION_MCP_SERVER_COMMAND is not configured")

        env = os.environ.copy()
        env["NOTION_TOKEN"] = self._settings.notion_token

        arguments = {
            "parent": {"database_id": self._settings.notion_database_id},
            "properties": self._build_properties(payload),
            "children": self._build_children(payload),
        }
        with _StdioJsonRpcClient(
            self._settings.notion_mcp_server_command,
            shlex.split(self._settings.notion_mcp_server_args),
            env=env,
            timeout_seconds=self._settings.notion_mcp_timeout_seconds,
        ) as client:
            client.initialize()
            result = client.request(
                "tools/call",
                {
                    "name": self._settings.notion_mcp_tool_name,
                    "arguments": arguments,
                },
            )
        page_id, page_url = self._extract_page_fields(result)
        if not page_id or not page_url:
            raise NotionMcpError("MCP response did not include Notion page id/url")
        return NotionPublishResult(page_id=page_id, page_url=page_url)

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
