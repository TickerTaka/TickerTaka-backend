# 2차 재평가(A, 60/70) 대응 개선 계획 (2026-06-20)

## 0. 배경
공식 2차 재평가: [BDAI_Pocat_Team2-e839d98-rerun2-2026-06-20.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-e839d98-rerun2-2026-06-20.md:1)
- **47/70 C → 60/70 (85.7%) A** (+13). 항목 1·2·5·8·9 = 5점, 3·4·6·10·7 이 잔여.
- 항목별: 1(5) 2(5) 3(**3**) 4(**4**) 5(5) 6(**4**) 7(**2**) 8(5) 9(5) 10(**4**).
- 합계: `(5+5+3+5)×2 + (4+5+4+2+5+4)×1 = 36+24 = 60`.

남은 상향 여지(가중점) + 리포트가 짚은 **docker-compose·문서 미동기화**를 정리한다.

---

## 1. 보완 항목 (리포트 우선순위 + 우리 방침)

### P1. 항목3 토론 본경로 sLLM — 3→4 (×2, **+2**) ※ 방침: "전환"이 아니라 **데모+비교**
리포트 권고는 "토론 노드 1개 이상을 OpenRouter(≤300B)/로컬 Qwen으로 전환". **우리 방침**: 토론을 sLLM으로 상시 전환하진 않고, **OpenRouter sLLM 하나로 1회 시연 + 현행 gpt-4o-mini와 비교(시간·비용·성능)** 문서화로 갈음.
- **선행 enabler(필수)**: `llm_factory.get_llm`이 `ChatOpenAI(api_key=openai_api_key)`만 쓰고 **`base_url` 미지원**(`app/core/llm_factory.py:46-52`). → OpenRouter/다른 공급자를 쓰려면 **base_url 주입 1줄 추가**가 필요(아래 P-LLM).
- 산출물: `memo/results/`에 sLLM↔gpt-4o-mini 비교표(응답시간·토큰비용·요약품질). langfuse 키 주입 시 그 호출 trace 1회 캡처하면 [S]→[D] 보강.
- 점수: 데모/비교만으론 "본경로 전환"이 아니라 **3 유지 가능성** — 단 토론 노드 1개라도 실제 sLLM 경로로 돌려 커밋하면 3→4 여지. (방침상 +2는 보수적으로 잡지 않음)

### P2. 항목6 MCP 클라이언트 E2E — 4→5 (×1, **+1**) ※ **이미 증적 확보됨, 커밋만 하면 됨**
리포트가 4점 준 사유 = "클라 E2E가 키/DB 미주입으로 [S]". **그러나 이번 세션에 실제로 확인됨**:
- Notion 발행 E2E 성공(실 페이지 생성 + 멱등) — curl 응답 로그.
- Claude Desktop(실 외부 MCP 클라이언트)에서 `list_available_symbols`→24종목, `get_stock_detail`→현대차 반환.
- **작업**: 이 E2E 증적을 `reports/`에 artifact로 커밋(예: `reports/mcp-e2e-2026-06-20.md` — Notion publish 응답 + Claude Desktop tool 호출 결과). → 신규 SHA 재평가 시 4→5 근거. **가장 쉬운 +1.**

### P3. 항목7 서빙 기동 증적 — 2→3 (×1, **+1**)
리포트: "compose에 ollama 서비스 추가 OR `ANALYSIS_GENERATION_BACKEND=remote` 기동 로그(`POST /v1/chat/completions 200`) artifact 커밋".
- **이미 워커 E2E로 Ollama 호출 200 확인**(2026-06-18, 005380). → 그 로그를 artifact로 커밋하면 2→3.
- + docker-compose에 **ollama 서비스 추가**(아래 §2)하면 "독립 기동" 증적까지.

### P4. 항목4 설계문서 동기화 잔여 2건 — 4→5 (×1, **+1**) ※ 문서만, 최저 risk
리포트 잔존 경미 2건:
- `DebateCreateRequest.decision_agent`(요청바디) 미기재 → `docs/design/interface-definition.md` §3 추가(`schemas/debate.py:15`).
- `judge_agent` 노드가 시퀀스/컴포넌트 설계 누락 → `sequence-diagram.md`·`component-design.md`에 추가(`debate_graph.py:14,63`).
- 코드 변경 0. **빠른 +1.**

### P5. 항목10 토큰단위 스트리밍 — 4→5 (×1, +1)
노드단위 yield → LLM `stream=True` 토큰 패스스루를 SSE에 연결. 난이도 中(스트리밍 체인 변경).

---

## 2. docker-compose 미동기화 (리포트 지적 반영)
현재 `docker-compose.yml`: postgres(profile)·redis·chroma·app·worker. **누락**:
- **Ollama 서비스 없음** (항목7). 추가안:
  ```yaml
  ollama:
    image: ollama/ollama:latest
    container_name: tickertaka-ollama
    ports: ["11434:11434"]
    volumes: [ollamadata:/root/.ollama]
    # 최초 1회: docker exec tickertaka-ollama ollama pull qwen2.5:3b
  ```
  + app/worker에 `ANALYSIS_GENERATION_BACKEND=remote`·`ANALYSIS_GENERATION_BASE_URL=http://ollama:11434/v1`·`ANALYSIS_GENERATION_MODEL=qwen2.5:3b` env(원격 서빙 시).
- **MCP 서버**: stdio라 compose 상시 서비스보다는 외부 클라(Claude Desktop) spawn이 자연스러움 → compose 추가는 선택(문서로만 안내).
- 참고: app/worker가 `env_file: .env`라 `ANALYSIS_GENERATION_*`·`LANGFUSE_*`는 .env로 들어감(노출 OK), 단 **README/compose 주석에 명문화**하면 명확.

## 3. 문서 동기화 체크 (항목4 + 신규 기능)
- interface-definition.md: `decision_agent` 필드 추가(P4).
- sequence-diagram.md / component-design.md: `judge_agent` 노드 반영(P4).
- (확인됨·과장 0) Langfuse/Qwen서빙/MCP/Hybrid 서술은 리포트 교차검증서 "과장 없음" 판정 — 유지.

---

## P-LLM. 공급자 base_url 주입 (Sonnet 데모 + 항목3 sLLM 공통 enabler)
**현재**: `llm_factory`가 OpenAI 전용(base_url 없음) → gpt-4o-mini 외 공급자 못 씀(.env만으론 불가).
**작업(작음)**: `get_llm`·`invoke_with_fallback`의 `ChatOpenAI(...)`에 `base_url=settings.<llm_base_url>` 추가 + config 필드(예 `LLM_BASE_URL`, 비우면 OpenAI 기본). 그러면:
- **Sonnet 시연**: OpenRouter base_url + `BULL_MODEL=anthropic/claude-sonnet-4.6` 등 → **이후 .env만으로 모델 교체**.
- **항목3 sLLM 데모**: 같은 메커니즘으로 `openai/gpt-oss-120b` 등 ≤300B 모델.
→ **이 한 번의 wiring이 Sonnet 데모 + 항목3 sLLM 비교를 모두 .env-only로 만든다.** (RAGAS 평가경로는 이미 base_url로 OpenRouter 사용 중 — 동일 패턴을 토론경로에 적용)

---

## 4. 권장 실행 순서 (ROI)
1. **P4 문서 동기화**(코드0·+1) + **P2 MCP E2E artifact 커밋**(이미 증적 있음·+1) — 즉시·무위험.
2. **P-LLM base_url 주입** → 그 위에서 **P1 sLLM 데모/비교** + Sonnet 시연.
3. **P3 항목7**: Ollama 기동 로그 artifact 커밋(+ compose ollama 서비스).
4. (선택) **P5 토큰 스트리밍**.
5. 신규 SHA로 평가 Agent 재실행 → 점수 확정(P2·P3·P4 반영 시 60→63~64 기대).

> 주의: 모든 코드 변경은 [[branch_strategy]] 따라 작업 브랜치(uc 등)에서 → main merge. 실 발행/외부 호출 증적은 artifact로 `reports/`에 커밋.
