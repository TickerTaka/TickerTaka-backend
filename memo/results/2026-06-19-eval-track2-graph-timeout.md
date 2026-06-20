# 구현 결과 — 토론 그래프 전체 타임아웃 (항목2)

- 작성: 2026-06-19 / 브랜치: `uc`
- 대상 평가항목: **항목2 에러핸들링·폴백** (×2)
- 직전 상태(재평가 리포트 `a134b5b`): 4/5 — 잔여 사유 "그래프 전체 타임아웃(`asyncio.wait_for`) 부재"

---

## 1. 무엇을 했나

토론 그래프 실행 전체에 **데드라인(`DEBATE_TIMEOUT_SECONDS`, 기본 300초)**을 걸었다. 초과 시 `TimeoutError`로 중단되어 **기존 fail-soft 경로**(세션 `failed` 마킹 + 런타임 락 해제 + SSE `error` 이벤트)로 처리된다.

## 2. 문제 (직전)

- LLM 호출 **1건 단위** 방어는 있었음(`llm_factory` `max_retries=3`+timeout, bull/bear의 `TimeoutError` except).
- 그러나 **그래프 전체를 끊는 능동적 데드라인 부재** → 노드 hang(LLM 무응답)이나 누적 지연 시 토론·SSE 스트림·세션이 무한정 매달릴 수 있었다(세션 `running` 좀비).

## 3. 구현 (file:line)

| 변경 | 위치 | 내용 |
|---|---|---|
| config | `app/config.py` | `debate_timeout_seconds`(`DEBATE_TIMEOUT_SECONDS`, 기본 300, 0이면 비활성) |
| 데드라인 적용 | `app/domain/debate_service.py` `_astream_with_config` | `async` 제너레이터로 전환. 각 청크(`__anext__`)를 **남은 예산으로 `asyncio.wait_for`** → ① 단일 노드 hang ② 누적 지연 둘 다 상한선으로 차단. 초과 시 `TimeoutError` raise |
| env 예시 | `.env.example` | `DEBATE_TIMEOUT_SECONDS=300` |

- **공통 경로**: `_astream_with_config`는 `run_session`(일괄)·`stream_session`(SSE) 둘 다의 그래프 실행부 → **한 곳 수정으로 양 경로 커버**.
- **fail-soft 연결**: 두 호출부 모두 `except Exception → fail_session_if_running(...) + finally end_session(...)`이미 존재 → `TimeoutError`가 그대로 흘러 세션 실패 처리 + 락 해제. SSE는 endpoint(`debate.py`)가 `error` 이벤트로 변환.
- `timeout<=0`이면 데드라인 비활성(패스스루) — 무회귀 옵션.

## 4. 검증 (테스트)

`tests/test_agents/test_debate_timeout.py` (mock 그래프, 외부 의존 0):
- `test_graph_timeout_raises`: 1초 데드라인 < 5초 hang 스트림 → **`TimeoutError` 발생** ✅
- `test_graph_completes_within_deadline`: 데드라인 내 정상 완료 → 모든 청크 그대로 통과 ✅
- `test_timeout_disabled_passthrough`: `timeout=0` → 끊지 않고 통과 ✅
- 전체 스위트(RAGAS 제외) **24 passed** — `debate_service` async 전환 무회귀.

## 5. 평가 영향 (예상, 확정 아님)

- 보완 "그래프 전체 타임아웃 부재" 해소 → **항목2 4→5 예상**(×2, **+2**). 정식 확정은 신규 SHA 재평가 후.

## 6. 관련 문서

- 재평가 리포트 보완(항목2): [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 개선 계획 P1-2: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1)
