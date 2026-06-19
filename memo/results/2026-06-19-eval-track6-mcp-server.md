# 구현 결과 — MCP 서버(도구 제공자) 신설 (항목6)

- 작성: 2026-06-19 / 브랜치: `uc`
- 대상 평가항목: **항목6 MCP / A2A** (×1)
- 직전 상태(재평가 리포트 `a134b5b`): 3/5 — "단방향 클라이언트만(서버측 tool 노출·tools/list·Python `mcp` SDK 미사용)"

---

## 1. 무엇을 했나

공식 **`mcp` SDK(FastMCP)** 로 **TickerTaka MCP 서버**(`app/mcp_server.py`)를 신설해 도메인 기능을 **tool 로 노출**했다. 기존엔 우리가 Notion MCP 의 **클라이언트**(소비자)였는데, 이제 **서버(제공자)** 도 되어 **양방향**을 이룬다.

**핵심 설계(low risk)**: tool 은 **기존 FastAPI route 함수를 `db=<session>` 으로 직접 호출**해 로직을 100% 재사용 → 코드 중복 0, **production 코드 무수정**(순수 추가, 회귀 risk 0).

## 2. 노출 tool (6개)

| tool | 재사용 대상 | 비고 |
|---|---|---|
| `list_available_symbols` | NewsCache/FilingCache distinct | **데이터 있는 종목**(토론·조회 가능 대상)부터 안내 |
| `get_stock_detail(symbol)` | `market_data.get_stock_detail` | 가격·재무·기술지표 |
| `get_watchlist_feed(user_id)` | `watchlist.get_watchlist_feed` | 뉴스/공시 + 감성분석 |
| `list_debates(user_id, symbol)` | `debate.list_debates` | 토론 목록 |
| `get_debate(session_id)` | `debate.get_debate` | 토론 결과(발언·요약) |
| `start_debate(user_id, symbol, category)` | `debate.create_debate`(async) | 토론 실행(데이터 있는 종목만·수십 초·LLM 비용) |

> **데이터 제약 반영**: 우리 토론/조회는 수집·인덱싱된 종목만 의미 있음 → `list_available_symbols` 로 가능한 종목을 먼저 제공하고, `start_debate` 는 그 종목에 한해 동작(미수집이면 명확한 에러).

## 3. 검증 (인-프로세스)

`mcp.list_tools()` + tool 직접 호출로 확인(외부 GUI 없이):
- **tools/list = 6개 정상 등록** (FastMCP 자동): `list_available_symbols, get_stock_detail, get_watchlist_feed, list_debates, get_debate, start_debate`
- `list_available_symbols()` → **24개 종목** 반환
- `get_stock_detail("005380")` → `현대차` + 최신가 존재
- `list_debates(symbol="005380")` → 1건(이전 세션 토론)

→ 서버측 tool 노출 + tools/list + 공식 `mcp` SDK **3요소 충족**.

## 4. Claude Desktop 연결법 (사용자 측)

서버는 stdio 로 기동: `python -m app.mcp_server`. Claude Desktop(Windows)에서 WSL 의 서버를 spawn 하려면 `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tickertaka": {
      "command": "wsl.exe",
      "args": ["bash", "-lc", "cd ~/TickerTaka-backend && source venv/bin/activate && exec python -m app.mcp_server"]
    }
  }
}
```
연결 후 Claude Desktop에서 "현대차 상세 보여줘" / "내 토론 목록" → 우리 tool 호출. (또는 `mcp dev app/mcp_server.py` 로 MCP Inspector GUI에서 tool 호출 시연.)

## 5. 정직한 범위/한계
- **서버측은 공식 SDK로 완성**, 단 **클라이언트(Notion `notion_mcp.py`)는 아직 자체 JSON-RPC** — 표준 `mcp.ClientSession` 교체는 **동작 중 코드 수정이라 risk**가 있어 별도 트랙으로 남김. (서버측 + tools/list + SDK 사용으로 항목6 핵심은 충족)
- **GUI 연결 시연(Claude Desktop/Inspector)은 사용자 측 작업** — 코드상 tool 등록·호출 동작은 인-프로세스로 검증함.
- `start_debate` 는 verified `create_debate` 경로를 감싸기만(비용/시간 때문에 본 검증에선 read tool만 실행, 토론 실행 경로는 직전 SSE 세션 `e11a0291` 으로 이미 입증).

## 6. 평가 영향 (예상, 확정 아님)
- "서버측 tool 노출·tools/list·`mcp` SDK 미사용" 사유 해소 → **항목6 3→4 예상**(×1, +1). 클라이언트 SDK 교체까지 하면 추가 여지. 정식 확정은 신규 SHA 재평가 후.

## 7. 관련/artifact
- 도구: `app/mcp_server.py`, `requirements.txt`(`mcp==1.28.0`)
- 재평가 보완 #7 / 개선계획 P3-2: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
