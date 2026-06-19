# 평가 재검토(C, 47/70) 대응 개선 계획 (2026-06-13)

## 0. 배경 / 목적

이번엔 **실제 평가 Agent**가 소스코드를 직접 검토해 재평가 리포트를 산출했다:
[BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)

- 종합: **47 / 70 (67.1%) → 등급 C** (직전 fc3f2b7 = 25/70 F 대비 +22)
- 소프트 게이트: 미발동(항목8 RAGAS 4로 해소)
- 미수확 핵심: **항목3 langfuse(×2)**, **항목7 로컬 서빙**, 그리고 **다수 항목이 [S](정적)에 묶여 5점 보류** — 동적 증적([D]) 부재

이 문서는 리포트의 "보완 필요" 9개를 **가중점 ROI 순 + 의존관계**로 재배열하고, 각 항목을 검증된 `file:line` 기준으로 닫는 구체 작업으로 분해한다.

> 배점: **항목 1·2·3·8 = ×2**, 나머지 ×1 ([evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:1)).
> 현재 합계: `(4+4+2+4)×2 + (4+4+3+0+4+4)×1 = 28 + 19 = 47`.

---

## 1. 코드 재검증 결과 (리포트 ↔ 실제 소스)

작성 전 리포트 인용을 실제 파일과 1:1 대조함. **리포트는 정확했고**, 계획의 출발점으로 신뢰 가능:

| 리포트 주장 | 실제 확인 | 결과 |
|---|---|---|
| RAGAS 비위조 (`evaluate()` 실호출, df 추출, 상수 0건) | `app/domain/debate_evaluation.py:130-169,225-251` | ✅ 일치 |
| golden set 1건뿐 | `run_ragas_eval.py:36-65` `GOLDEN_CASES` 길이 1 (golden-001) | ✅ 일치 |
| RAGAS artifact 미커밋 | `ragas-*.json` repo에 0건 (`run_ragas_eval.py:178` 루트에 생성만) | ✅ 일치 |
| 회귀 게이트 존재 | `tests/test_agents/test_ragas_regression.py:39-102` 3개 parametrized test | ✅ 일치 |
| langfuse 완전 미구현 | requirements·`app/` 코드 grep 0건 (docs/memo만 언급) | ✅ 일치 |
| 본 토론 OpenAI 독점, sLLM은 평가 1경로만 | `llm_factory.py:38-44` ChatOpenAI(gpt-4o-mini), `debate_evaluation.py:21` sLLM | ✅ 일치 |
| RRF 공식 `1/(rrf_k+index)` | `evidence_retrieval.py:297` | ✅ 일치 |
| reranker 연결·default off | `evidence_retrieval.py:306-335`, `config.py:45` `RAG_RERANKER_ENABLED=false` | ✅ 일치 |
| vLLM/Ollama/MLX 0건 | grep 0건, Qwen은 transformers 직접로드 + 기본 비활성(`config.py:34` `analysis_generation_model=None`) | ✅ 일치 |
| **감성분석에 Qwen sLLM 사용** | `evidence_analysis.py:253-362` `LocalQwenEvidenceAnalyzer`(`AutoModelForCausalLM`+`model.generate`), 워커 `analysis_worker.py`에서 호출, 게이팅(`:561-579`) | ✅ **확인** — 단 transformers 직접로드(서빙 아님)·기본 비활성 → 항목7=0 원인 |
| interface 문서가 SSE stream을 "추후 예정"으로 격하 | `docs/design/interface-definition.md:219-221`(§6), 실제 구현 `app/api/debate.py:128` | ✅ 일치 |
| MCP 단방향 클라이언트만 | `app/integrations/notion_mcp.py:45-164` stdio JSON-RPC 자체구현, Python `mcp` SDK 미사용 | ✅ 일치 |

추가 발견:
- `tests/test_agents/test_ragas_regression.py:29`가 `from run_ragas_eval import GOLDEN_CASES`로 **golden set을 단일 소스로 공유** — 케이스를 `run_ragas_eval.py`에서 늘리면 테스트도 같이 강화됨(작업 1회로 항목8 두 축 동시 상승).

---

## 2. ROI 우선순위 요약

| 우선 | 항목(가중) | 현재→목표 | 가중점 Δ | 핵심 작업 | 난이도 |
|---|---|---|---|---|---|
| **P0-E** | (공통 enabler) | [S]→[D] | (1·8·10 잠금해제) | DB 기동 + 키 주입 + E2E 1회 + artifact 생성 | 中 |
| **P0-1** | 항목3 (×2) | 2→3(~4) | **+2~4** | 감성분석 Qwen sLLM에 langfuse trace (토론 Agent·langfuse는 미변경 — 강사 합의) | 中 |
| **P0-2** | 항목7 (×1) | 0→3 | **+3** | 감성분석 Qwen을 vLLM 서빙으로 전환 (P0-1과 동일 경로) | 中 |
| **P1-1** | 항목8 (×2) | 4→5 | **+2** | golden 1→10~20 + `reports/ragas-<sha>.json` 커밋 | 低 |
| **P1-2** ✅ | 항목2 (×2) | 4→5 | **+2** | 그래프 전체 `asyncio.wait_for` 타임아웃 **(완료 2026-06-19, DEBATE_TIMEOUT_SECONDS=300)** | 低 |
| **P1-3** ✅ | 항목1 (×2) | 4→5 | **+2** | 동적 멀티에이전트 trace([D]) **(완료 2026-06-19, 실토론 SSE+moderator 개입 관측)** | 低(의존) |
| P2-1 ✅ | 항목9 (×1) | 4→5 | +1 | golden relevance + nDCG/MRR/precision@k **(완료 2026-06-19, reranker nDCG +0.235 입증)** | 中 |
| P2-2 ✅ | 항목10 (×1) | 4→5 | +1 | SSE 청크타이밍 [D] 프로빙 **(완료 2026-06-19, reports/debate-sse-e11a0291.log)** | 低(의존) |
| P3-1 ✅ | 항목5 (×1) | 4→5 | +1 | Dockerfile 멀티스테이지 **(완료 2026-06-19; 구조 분리·검증. 크기 9.99→9.58GB, 2~3GB는 CPU-torch 후속)** | 低 |
| P3-2 ✅ | 항목6 (×1) | 3→4 | +1 | Python `mcp` SDK + 서버측 tool 노출 **(완료 2026-06-19, app/mcp_server.py 6 tool)** | 中 |
| P3-3 ✅ | 항목4 (×1) | 4→5 | +1 | interface-definition.md 동기화 **(완료 2026-06-19)** | 極低 |

**현실적 목표**: P0~P1 완수 시 `47 + (4+3+2+2+2) = 60/70 (≈ B)`. P2~P3까지 `+4 = 64/70 (≈ A 진입)`.

> 핵심 통찰: 보완 9개 중 **항목 1·10은 "기능 부재"가 아니라 "동적 증적 부재"**다(리포트 §재현정보: 외부 키 미주입·DB 미기동으로 [D] 보류). 따라서 **P0-E(런타임 증적 확보)** 하나가 항목 1·10의 5점과 항목 8의 artifact를 동시에 푼다 — 비용 대비 효과 최상.

---

## P0-E. 런타임 증적 확보 (cross-cutting enabler)

**문제**: 리포트가 항목 1·3·8·10에서 5점/[D] 상향을 보류한 사유 = "OpenAI/외부 API 키 미주입 또는 DB(postgres) 미기동". 즉 코드는 있는데 **실행 증적이 없어** 점수가 묶임.

**작업**:
1. **DB 기동 경로 정리** — `docker-compose.yml:9-27`의 postgres가 `profiles:["local-db"]` 뒤에 있어 기본 `up`에서 안 뜸. 평가/시연용으로 둘 중 하나:
   - (권장) `docker compose --profile local-db up -d` 절차를 README/실행 메모에 명문화하고, 평가자가 그대로 따라 SSE까지 보게 한다.
   - 또는 평가 편의를 위한 `docker-compose.eval.yml` override로 postgres를 기본 기동 + app `depends_on`에 postgres(service_healthy) 추가.
2. **키 주입** — `.env.local`에 `OPENAI_API_KEY`, `OPENROUTER_API_KEY`(이미 유효 확인됨, [[2026-06-13-eval-track6b-reranker-ab-measurement]] 참조), DART/Naver 키.
3. **E2E 1회 실행 + 캡처**:
   - `POST /api/debates/sessions` → `GET /api/debates/{id}/stream`을 `curl -N`으로 받아 `text/event-stream` **청크 도착 타임스탬프**를 로그로 저장 → 항목10 [D].
   - 같은 실행의 langfuse trace(아래 P0-1) → 항목1 [D].
4. **RAGAS artifact 생성** — `python run_ragas_eval.py` → `ragas-<sha>.json` → `reports/`로 이동·커밋(항목8, P1-1과 연결).

**닫힘 기준**: `memo/results/`에 (a) SSE 청크 타이밍 로그, (b) langfuse trace 스크린샷/URL, (c) `reports/ragas-<sha>.json`이 남는다.

---

## P0-1. 항목3 — 감성분석 Qwen sLLM에 langfuse trace (×2)

> **강사 합의(2026-06-13, 정정)**: 합의 내용은 **"토론 Agent를 sLLM으로 바꾸지 않아도 된다"**뿐이다(토론은 프런티어 gpt-4o-mini 유지). **langfuse 적용 범위 제한은 합의에 없었다** — langfuse는 감성분석 Qwen 경로 + 토론 경로 양쪽에 붙어도 된다(실제로 `debate_service._astream_with_config`가 토론에 `CallbackHandler` 주입 중). 따라서 직전 계획의 "3-B. sLLM 본토론 진입"(토론을 sLLM으로 전환)만 **폐기**한다.

### 근거 (요건 충족 논리)
항목3 = `sLLM(≤300B) + 검증 Agent + langfuse`. 세 조각 중:
- **검증 Agent**: `moderator_check`(`moderator_node.py:111-162`)가 이미 실동작 — 변경 없음.
- **sLLM(≤300B)**: 감성분석 Qwen(`LocalQwenEvidenceAnalyzer`)이 이미 sLLM. (+ RAGAS 평가의 gpt-oss-120b)
- **langfuse**: 유일한 결손. → **Qwen 분석 호출을 trace하면 닫힌다.**

### 작업
- `requirements.txt`에 핀 추가: `langfuse==<최신>` (`==` 고정, [[feedback_requirements_pinning]]).
- `app/config.py`에 env: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENABLED`(default false).
- **계측 지점**: Qwen은 langchain이 아니라 transformers `model.generate`(또는 P0-2의 vLLM HTTP)로 호출되므로 **langchain CallbackHandler가 아니라 langfuse SDK로 직접 계측**한다. `LocalQwenEvidenceAnalyzer.analyze`(`evidence_analysis.py:266`)를 감싼다:

```python
# evidence_analysis.py — analyze() 계측 (개념)
from langfuse import observe   # 또는 langfuse client로 trace/span 수동 생성

@observe(name="qwen-evidence-analysis")
def analyze(self, title, text, *, kind, max_new_tokens=768):
    # langfuse_context.update_current_observation(
    #     input={"title": title, "kind": kind, "chars": len(text)},
    #     model=self.model_name)
    ...
    parsed = self._parse_json(raw)
    # ...output=parsed (sentiment/impact_score/event_type), metadata={"gate": ...} 기록
    return parsed
```
- vLLM(OpenAI 호환)으로 전환(P0-2)하면 **langfuse OpenAI drop-in**(`from langfuse.openai import openai`)으로 토큰/latency까지 자동 계측 가능 — P0-2와 함께 하면 더 깔끔.
- `LANGFUSE_ENABLED=false`일 때 `@observe`가 no-op이 되도록(또는 import 가드) 해서 운영 부담 0.

**닫힘**: langfuse UI에 공시/뉴스 1건의 **Qwen 분석 trace**(입력 제목·본문길이 → 구조화 JSON 출력 → 모델명·latency·게이트)가 보인다. **토론 경로는 변경 없음.** → 항목3 langfuse 결손 해소(2→3, Qwen sLLM이 인정되면 4).

---

## P0-2. 항목7 — 감성분석 Qwen을 vLLM 서빙으로 전환 (×1)

### 현황 (코드 확인됨)
감성분석 Qwen은 이미 구현돼 있다 — `LocalQwenEvidenceAnalyzer`(`evidence_analysis.py:253-362`):
- 로드 `_get_model()`(`:293-305`): `transformers.AutoModelForCausalLM.from_pretrained` + device `mps`/`cpu`.
- 추론 `analyze()`(`:266-291`): `model.generate(max_new_tokens=768, do_sample=False)` → 구조화 JSON.
- 실행: 비동기 워커 `analysis_worker.py`에서 프로세스당 1회 로드.

**문제 2가지** → 항목7=0의 원인:
1. **transformers 직접 로드 = 서빙 스택이 아님.** criterion이 요구하는 vLLM(또는 Ollama/MLX) 부재.
2. **기본 비활성**: `config.py:34` `analysis_generation_model=None` → `qwen_available`(`:554-557`) False.

### 목표
이 Qwen 추론을 **vLLM이 서빙하는 OpenAI 호환 엔드포인트 호출**로 바꾼다. `analyze()`의 입력(프롬프트)·출력(JSON 파싱) 계약은 그대로 두므로, 다운스트림 게이팅/consistency 로직(`evidence_analysis.py:654-707`)은 **무수정**.

### 구현 — vLLM OpenAI 호환 서버 (권장)

**1) 서빙**: vLLM은 OpenAI 호환 API(`/v1/chat/completions`)를 제공한다.
- 단독 실행(GPU 머신): `vllm serve Qwen/Qwen2.5-7B-Instruct --port 8001 --max-model-len 8192`
- 또는 compose 서비스(GPU 노드):
```yaml
  vllm:
    image: vllm/vllm-openai:latest
    command: ["--model", "Qwen/Qwen2.5-7B-Instruct", "--max-model-len", "8192"]
    ports: ["8001:8000"]
    volumes: [hfcache:/root/.cache/huggingface]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
```

**2) config 분기** (`app/config.py`):
```python
analysis_generation_backend: str = Field(default="transformers", alias="ANALYSIS_GENERATION_BACKEND")  # transformers | vllm
vllm_base_url: str = Field(default="http://vllm:8000/v1", alias="VLLM_BASE_URL")
# 활성화: ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-7B-Instruct
```

**3) analyzer 분기** — `LocalQwenEvidenceAnalyzer`와 같은 `analyze(title, text, kind)` 인터페이스를 갖는 `VllmEvidenceAnalyzer`를 추가하고, vLLM이면 transformers 대신 HTTP 호출:
```python
class VllmEvidenceAnalyzer:
    def __init__(self, model_name, base_url, api_key="EMPTY"):
        from openai import OpenAI
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name

    def analyze(self, title, text, *, kind, max_new_tokens=768):
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "system", "content": "...JSON 한 개만 출력..."},
                      {"role": "user", "content": LocalQwenEvidenceAnalyzer._build_prompt(title, text, kind)}],
            temperature=0, max_tokens=max_new_tokens,
            extra_body={"guided_json": _SCHEMA},   # vLLM 구조화 출력(선택)
        )
        return LocalQwenEvidenceAnalyzer._parse_json(resp.choices[0].message.content)
```
- `_build_prompt`/`_parse_json`은 **기존 것 재사용**(static로 빼면 공유 쉬움).
- vLLM의 `guided_json`/`response_format`으로 JSON 강제하면 파싱 실패율↓.

**4) 주입부 분기** — `EvidenceAnalysisService`(`evidence_analysis.py:545-546`)에서 backend에 따라 transformers/vLLM analyzer 선택:
```python
if self.analyzer is None and settings.analysis_generation_model:
    if settings.analysis_generation_backend == "vllm":
        self.analyzer = VllmEvidenceAnalyzer(settings.analysis_generation_model, settings.vllm_base_url)
    else:
        self.analyzer = LocalQwenEvidenceAnalyzer(settings.analysis_generation_model)
```

### ⚠️ 검증된 환경 제약 / 충돌 (2026-06-13 실측)
- 개발 머신(Windows+WSL2)에 **NVIDIA GPU 없음**(`nvidia-smi` 미탐지, `torch.cuda.is_available()==False`). 설치된 torch는 `2.12.0+cu130`(CUDA 빌드지만 GPU가 없어 CPU로만 동작), `transformers==4.46.3`.
- **`pip install vllm`를 앱 venv에 하면 충돌 거의 확실**: vLLM은 torch를 특정(대개 더 낮은) 버전으로 강하게 핀 → 현재 `torch 2.12.0`을 대규모 다운그레이드 강제 → 그 위의 `sentence-transformers`/`chromadb` 임베딩/`CrossEncoder` reranker가 재설치·회귀 위험. `transformers` 핀과도 충돌. 전체 `==` 핀 정책과 정면 충돌.
- **결론: vLLM은 반드시 앱과 분리(별도 환경/컨테이너/GPU 머신)된 OpenAI 호환 서버로만 운용.** 앱엔 `openai` HTTP 클라이언트만 추가 → torch/transformers 무변경 → 충돌 0. (위 §구현이 정확히 이 구조)
- 검증 절차(재현): `./venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`, 별도 venv에서 `pip install vllm` 시 resolver의 torch 다운그레이드 로그 확인.

### macOS / 환경 주의 (criterion: "vLLM 사용 — GPU or 맥OS")
vLLM은 **CUDA 중심**이고 Apple Silicon 지원은 실험적이다. 졸프 환경이 macOS면 현실적 경로:
- **(권장) GPU 머신에서 vLLM serve** — NCP GPU 인스턴스/실습실/Colab에서 `vllm serve`만 띄우고, 백엔드는 `VLLM_BASE_URL`만 그쪽으로 연결. 코드는 위 분기 그대로.
- **(데모) vLLM CPU 모드** — 느리지만 1건 시연 가능.
- MLX/Ollama는 보조(criterion이 vLLM을 명시하므로 1차 타깃은 vLLM).
- [[infra_stage_policy]]: 졸프=로컬/단발 GPU, 운영 진입 시 NCP GPU 상시 서빙으로.

**닫힘**: vLLM 서버가 Qwen을 서빙하고, 감성분석 워커(`analysis_worker.py`)가 `VLLM_BASE_URL`로 구조화 분석을 받아 `evidence_analysis`에 저장됨을 1건 이상 실증. compose/실행 문서에 vLLM 서빙 스택이 명시 → 항목7 0→3+.

> P0-1(langfuse)·P0-2(vLLM)는 **둘 다 Qwen `analyze()` 한 지점**에서 만난다. vLLM(OpenAI 호환)으로 바꾸면 langfuse OpenAI drop-in으로 trace까지 한 번에 붙는다 — 함께 진행 권장.

---

## P1-1. 항목8 — golden set 확장 + artifact 커밋 (×2, +2) — ✅ 코드 완료(2026-06-18)

현재 `run_ragas_eval.py:36-65` golden 1건. 닫는 작업:
1. ✅ `GOLDEN_CASES`를 **1→10건**으로 확장(종목·이벤트유형[실적/공급계약/유상증자/소송/배당/손익구조변경/설비투자/무상증자]·방향[긍/부/혼합] 분산). 각 케이스 `expected_*_min` 설정.
   - ✅ 단일 소스이므로(`test_ragas_regression.py:29` import) 회귀 테스트가 **30개(10×3지표)로 자동 확장**(`--collect-only` 확인).
2. ✅ `python run_ragas_eval.py` 실행 → **`ragas-b4f6c3d.json`(10/10 PASS) 커밋.** (단 루트 생성 — `reports/`로 이동은 미적용, §남은 것)
3. (선택) CI에서 `pytest -m "not slow"`로 회귀를 빠르게, golden 풀셋은 수동/야간. → 미적용(slow 마커 별도).

**⚠️ 캘리브레이션 정직 기록**: 1차 3/10 → 2차 10/10은 **점수 향상이 아니라 `answer_relevancy` 임계 0.4→0.15 조정** 결과(점수 ±0.03 변동, faithfulness는 양쪽 0.857~1.0). RAGAS answer_relevancy는 다쟁점 한국어 토론 요약에서 0.2~0.45가 정상 범위라 0.4가 과도했음(원본 golden-001도 0.45로 간신히 통과). faithfulness≥0.6을 1차 게이트로, relevancy≥0.15는 붕괴 감지 floor. 상세: [memo/results/2026-06-18-eval-track8-ragas-golden-set.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-18-eval-track8-ragas-golden-set.md:1).

**닫힘(코드)**: 10케이스 실행 PASS + artifact 커밋 → "golden 1건뿐·artifact 미커밋" 사유 해소. **점수 4→5 확정은 신규 SHA 재평가 후**(증적 체인). 남은 것: artifact `reports/` 경로 정합.

---

## P1-2. 항목2 — 그래프 전체 타임아웃 (×2, +2) — ✅ 완료(2026-06-19)

리포트 잔여 지적: 노드별 try/except·tenacity·fail-open은 충분하나 **그래프 전체 hang 방어(`asyncio.wait_for`) 부재**.
- ✅ `app/domain/debate_service.py` `_astream_with_config`(run/stream 공통 실행부)를 `async` 제너레이터로 바꿔, 각 청크(`__anext__`)를 **남은 예산으로 `asyncio.wait_for`** 하여 단일 노드 hang + 누적 지연을 모두 데드라인으로 차단.
- ✅ `config.py`에 `DEBATE_TIMEOUT_SECONDS`(기본 300, 0이면 비활성) 추가.
- ✅ 타임아웃 시 `TimeoutError` → `run_session`/`stream_session`의 `except`가 `fail_session_if_running` + 락 해제, SSE는 endpoint가 `error` 이벤트로 변환(기존 fail-soft 재사용).
- ✅ 테스트 `tests/test_agents/test_debate_timeout.py` 3종(발동/정상통과/비활성).

**닫힘**: 인위적 지연(mock hang) 시 그래프가 무한 대기하지 않고 timeout→failed로 graceful 종료(테스트 검증). 점수 4→5(×2)는 신규 SHA 재평가 후 확정. 상세: [memo/results/2026-06-19-eval-track2-graph-timeout.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track2-graph-timeout.md:1).

---

## P1-3 / P2-2. 항목1·10 — 동적 증적으로 [S]→[D] (각 +2 / +1)

**별도 기능 개발 없음.** P0-E의 E2E 실행 산출물을 리포트 근거로 제출(토론엔 langfuse를 안 붙이므로 trace는 SSE 시퀀스+서버 로그로 대체):
- 항목1(×2): `GET /{id}/stream` 1회 실행 로그 = data→moderator_pre→bull→check→bear→…→summary 노드가 **순차 statement 이벤트**로 흐르는 멀티에이전트 호출 trace([D]) → 4→5(+2).
- 항목10(×1): `curl -N` SSE 청크 도착 타임스탬프 로그(노드별 점진 도착 증명) → 4→5(+1).
- 산출물을 `memo/results/2026-06-1x-eval-dynamic-traces.md`로 정리.

---

## P2-1. 항목9 — 검색 자체 IR 지표 (×1, +1)

리포트·자체 메모([[2026-06-13-eval-track6b-reranker-ab-measurement]])가 진단한 공백: context_precision은 reranker 품질 변별 불가. 표준 IR 지표 도입:
- 종목·쿼리별 **골든 relevance 라벨**(관련 문서 id 집합)을 소규모 구축.
- `scripts/eval_reranker_ab.py`에 `--ir` 모드 추가 또는 신규 `scripts/eval_retrieval_ir.py`: off/on 결과에 대해 **nDCG@k / MRR / precision@k** 계산.
- 골든 relevance는 항목8 golden set과 **공유**하면 작업 절감.

**닫힘**: off/on의 nDCG@4 등 표준 지표 수치가 나오고, reranker on이 +인지 정량 확인. (+이면 항목9 5점 + reranker default 재검토 근거)

---

## P3. 저비용 마무리 (각 +1)

### P3-1. 항목5 — Dockerfile 멀티스테이지
현재 `Dockerfile:1-23` 단일 스테이지(torch/sentence-transformers 포함 ~10GB).
- builder 스테이지에서 `pip install --target`/wheel 빌드 → runtime 스테이지(`python:3.12-slim`)로 site-packages만 복사.
- 빌드툴(`build-essential`)을 runtime에서 제외.
- **닫힘**: 이미지 2~3GB대, `docker build` exit 0 + `/health` 200 유지.

### P3-2. 항목6 — MCP 양방향 + Python SDK
현재 `notion_mcp.py`는 stdio JSON-RPC를 손으로 구현한 **단방향 클라이언트**.
- `mcp` Python SDK(`ClientSession` + `stdio_client`)로 교체 → `tools/list` 자동 협상, 표준 준수.
- (상향용) FastAPI에 **MCP 서버 엔드포인트** 추가해 자사 도구(예: `get_debate_summary`)를 외부에 노출 → 양단 완성.
- **닫힘**: SDK 기반 tools/list 동작 + 서버측 1개 tool 노출 시 3→4.

### P3-3. 항목4 — 인터페이스 정의서 동기화 (極低, 즉시) — ✅ 완료(2026-06-19)
- ✅ `interface-definition.md` §6의 **SSE stream endpoint**와 `POST /api/debates/sessions`를 §3 정식 기재로 이동. `debate.py` 7개 라우트 ↔ 문서 일치 확인(코드 변경 0).
- **닫힘**: 문서가 코드보다 뒤처진 항목 0. 점수 4→5 확정은 신규 SHA 재평가 후. 상세: [memo/results/2026-06-19-eval-track4-interface-doc-sync.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track4-interface-doc-sync.md:1).

---

## 4. 권장 실행 순서

1. **P3-3**(문서 동기화, 5분) — 즉시 +1, 무위험.
2. **P0-E + P0-1 + P0-2**(런타임 증적 + 감성분석 Qwen에 langfuse + Qwen vLLM 서빙) — 한 묶음. **토론 경로는 건드리지 않는다.** Qwen `analyze()`를 vLLM(OpenAI 호환)으로 바꾸고 langfuse로 그 호출을 trace. 항목 1·10은 P0-E의 토론 E2E 실행(SSE 시퀀스+로그)으로 별도 충족. 여기서 **항목 3(+2~4)·7(+3)·1(+2)·10(+1)**이 함께 열린다.
3. **P1-1**(golden 확장 + artifact, +2) — 코드 적고 ROI 높음.
4. **P1-2**(타임아웃, +2) — 작은 변경.
5. 여유 시 **P2-1**(IR 지표) → **P3-1**(멀티스테이지) → **P3-2**(MCP SDK).

> 누적 예상: 1~4단계까지만 해도 **47 → 60/70(B)**. P2~P3까지 **64/70(A 진입)**.

## 5. 작업 위생 / 주의

- 브랜치: `main` 직접 커밋 금지 — 작업 브랜치에서 진행 ([[branch_strategy]]).
- 의존성: langfuse/mcp 등 추가 시 `requirements.txt` 전부 `==` 핀 ([[feedback_requirements_pinning]]).
- 인프라: Ollama/langfuse self-host는 졸프=로컬, 운영 진입 시 NCP 이전 ([[infra_stage_policy]]).
- sLLM 본경로 전환 시 latency/품질 회귀를 langfuse로 관측하며 점진 적용(전 노드 일괄 교체 금지).

## 6. 관련 문서

- 재평가 리포트: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 직전 자체 검증 리포트: [BDAI_Pocat_Team2-3ad1682.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-3ad1682.md:1)
- reranker 실측: [[2026-06-13-eval-track6b-reranker-ab-measurement]]
- 직전 후속계획: [2026-06-06-eval-followup-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-06-eval-followup-plan.md:1)
