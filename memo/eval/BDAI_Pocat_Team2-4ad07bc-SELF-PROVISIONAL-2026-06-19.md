# ⚠️ 임시 자가평가 (PROVISIONAL SELF-ASSESSMENT) — BDAI_Pocat_Team2 @ `4ad07bc`

> 🚨 **이 문서는 공식 평가 Agent의 산출물이 아닙니다.**
> - **작성 주체**: Claude(자가평가) — `a134b5b` 공식 리포트의 채점 기준을 차용해 **이번 세션 변경분을 코드/증적 기준으로 스스로 추정**한 값.
> - **용도**: 팀 내부에서 "지금까지 작업이 점수로 어느 정도일지" 가늠하는 **임시 참고용**. 강사 제출·공식 점수로 사용 금지.
> - **공식 확정 절차**: 동일 SHA(`4ad07bc`)를 **실제 평가 Agent**(5-auditor + Supervisor)로 재실행 → `BDAI_Pocat_Team2-4ad07bc-rerun-<date>.md`로 별도 발행해야 함.
> - 점수는 **보수적**으로 잡았고, 항목별 upside/caveat를 함께 적었다.

- 기준 비교: 공식 [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1) (47/70, C)
- 대상: backend `4ad07bc` (uc) — 이번 세션 보완 8개 반영(항목6만 미착수)
- 잠정 종합: **약 64 / 70** (자가 추정, 보수적)

---

## 잠정 스코어카드 (a134b5b → 4ad07bc 자가추정)

| # | 항목 | 가중 | a134b5b | 잠정 | 근거(이번 세션) | caveat / upside |
|---|------|:--:|:--:|:--:|------|------|
| 1 | Multi-Agent | ×2 | 4 | **5** | [D] 실토론 SSE로 data→bull→bear→moderator 실행 + **moderator 환각 개입·강제종료** 런타임 관측 (`reports/debate-sse-e11a0291.log`) | 동적 증적 확보 |
| 2 | 에러핸들링·폴백 | ×2 | 4 | **5** | 그래프 전체 타임아웃 `DEBATE_TIMEOUT_SECONDS`(asyncio.wait_for) + 테스트 3종 | 유일 지적이던 hang 방어 해소 |
| 3 | sLLM+검증+langfuse | ×2 | 2 | **4** | langfuse **양쪽 경로**(Qwen 분석 + 토론 CallbackHandler) 실 trace 검증 + sLLM(Qwen) + 검증(moderator_check) | upside 5(Qwen 기본활성 시); 보수적 4 |
| 4 | 5대 설계문서 | ×1 | 4 | **5** | interface 문서 ↔ 코드 7라우트 동기화(SSE/sessions 정식 기재) | 문서-코드 정합 회복 |
| 5 | Dockerise | ×1 | 4 | **4~5** | 멀티스테이지(builder/runtime) 전환 + 빌드·/health 검증 | **9.99→9.58GB(소폭)·postgres profile 미변경** → 4 유지 가능. CPU-torch 시 5 |
| 6 | MCP / A2A | ×1 | 3 | **3** | 미착수(이번 스킵) | 양방향(SDK+서버) 시 4+ |
| 7 | vLLM(서빙) | ×1 | 0 | **3** | Ollama 원격 서빙(`RemoteQwenEvidenceAnalyzer`) + 워커 E2E 실검증 | 기준 원문 "vLLM" → 강사 인정 확인 시 안정. upside 4 |
| 8 | RAGAS | ×2 | 4 | **5** | golden 1→10 + artifact 커밋 + 회귀 30개 | **10/10은 answer_relevancy 임계 0.4→0.15 캘리브레이션 결과(점수향상 아님, 정직 기록)** |
| 9 | RAG 고도화 | ×1 | 4 | **5** | IR 지표(nDCG/MRR/p@k) + 골든 relevance로 reranker 품질 입증(nDCG +0.235) | 경량(1종목·4쿼리·LLM초안 라벨) |
| 10 | 스트리밍·비동기 | ×1 | 4 | **5** | [D] SSE 청크 도착 타임스탬프(~43초 분산) = 실시간 스트리밍 입증 | 동적 증적 확보 |

### 잠정 합계 (보수적)
`(5+5+4+5)×2 + (5+4+3+3+5+5)×1 = 38 + 25 = 63~64 / 70` (항목5를 5로 보면 64, 4로 보면 63)

→ 공식 47 → **잠정 ~63-64/70** (자가추정). a134b5b 대비 **+16~17**. 등급은 공식 Agent 판정 사항(참고: 47/70=C였음 → 64/70=91%면 상위 등급권이나 **자가평가라 확정 불가**).

---

## 항목별 증적 문서 (재평가 시 [D] 근거로 제출)
- 항목1·10: `memo/results/2026-06-19-eval-track1-10-dynamic-evidence.md` + `reports/debate-sse-e11a0291.log`
- 항목2: `memo/results/2026-06-19-eval-track2-graph-timeout.md`
- 항목3·7: `memo/results/2026-06-18-eval-track7-ollama-qwen-serving.md` + langfuse trace(UI, 태그 `debate`/공시 454910·005380)
- 항목4: `memo/results/2026-06-19-eval-track4-interface-doc-sync.md`
- 항목5: `memo/results/2026-06-19-eval-track5-dockerfile-multistage.md`
- 항목8: `memo/results/2026-06-18-eval-track8-ragas-golden-set.md` + `reports/ragas-b4f6c3d.json`
- 항목9: `memo/results/2026-06-19-eval-track9-retrieval-ir.md` + `reports/ir-005380-*.json`

## 정직성 메모 (재평가 시 검증돼야 할 부분)
1. **항목8 10/10은 임계 캘리브레이션 결과**(요약 품질 향상 아님). faithfulness는 견고(0.857~1.0)하나 answer_relevancy 합격선을 0.4→0.15로 낮춤 — 공식 Agent가 이 캘리브레이션을 인정하는지 별도 판단 필요.
2. **항목7**은 기준 원문이 "vLLM"인데 Ollama로 충족 — 강사 인정 1줄 컨펌 전제.
3. **항목5** 멀티스테이지는 구조만 개선, 이미지 크기는 거의 그대로(9.58GB) — 채점이 "크기"를 보면 4 유지 가능.
4. **항목9**는 경량(1종목·LLM초안 라벨) — 표본 작음.
5. 위 잠정 점수는 **Claude 자가추정**이며 공식 Agent 결과와 다를 수 있음.

---

## 미착수 (남은 1개)
- **항목6 MCP 양방향**(3→4+): 표준 `mcp` SDK 클라이언트 교체 + 우리 앱을 MCP 서버로 노출. risk·복잡도 큼 → 별도 진행.
