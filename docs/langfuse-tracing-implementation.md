# Langfuse Trace — 구현 실행 문서 (1단계)

> 작성: 2026-06-15
> 설계 근거: [langfuse-sllm-tracing-design.md](./langfuse-sllm-tracing-design.md) (왜/무엇)
> 본 문서: **지금 상태에서 정확히 무엇을 어떤 순서로 만들지** (어떻게)
> 상태: **1단계 구현·실 trace 검증 완료** (2026-06-15). 실측 진단 1호 → 층1 조치까지 반영(§5).

---

## 왜 하는가 (문제)

지금 Qwen(sLLM) 보강 결과가 "잘 안 나오는" 케이스가 있는데 **원인을 사후에 알 수 없다.**

- 실패/강등 사유는 `evidence_analysis.raw_response` JSON에만 남아 **건별로 DB를 SQL로 까봐야** 한다.
- 그나마도 "결과가 이상하다"까지만 보이고, **파이프라인의 어느 단계에서 깎였는지**(Qwen 출력 자체가 나쁜 건지, grounding이 멀쩡한 걸 버린 건지, 표를 통째 복사해 잘린 건지)는 추적이 안 된다.
- 핵심 통찰: "sLLM이 잘 안 된다"의 정체는 대부분 **Qwen 출력이 아니라 하네스 단계에서 깎여나가는 것**이다. 이걸 구분 못 하면 엉뚱한 곳을 고치게 된다.

→ 즉, **진단 수단이 없어서 개선 방향을 데이터가 아니라 감으로 정하고 있는 상태.**

## 무엇을 위해 하는가 (목적)

1. **단계별 가시화(진단)** — 호출 1건을 1 trace로 묶어, gate → 트리밍 → 생성 → 파싱 → grounding → noise → consistency → validate 각 단계의 입출력·drop을 Langfuse UI에서 본다. "어디서 깎였나"를 즉답.
2. **문제군 일괄 추출** — `grounding_survival < 0.3`, `truncated=True` 같은 **조건으로 나쁜 trace만 필터링** → 패턴을 본다(건별 SQL 탈피).
3. **2단계(vLLM/모델 상향) 의사결정 근거** — `consistency=conflict`가 잦으면 1.5B 품질 천장 → 서빙 전환/큰 모델이 정당화된다. 반대로 grounding 임계 문제면 정규식만 고치면 된다. **vLLM이 맞는 처방인지를 감이 아니라 데이터로 판단**하기 위함.
4. **평가 항목 충족** — 항목3(관측가능성/트레이싱)을 Qwen 분석 경로에서 충족. 토론 Agent는 손대지 않는다.

> 한 줄 요약: **"왜 안 좋은지 모른 채 고치는 것"을 멈추고, 단계별 trace로 원인을 본 뒤 → 그 결과로 2단계(vLLM) 착수 여부를 정한다.**

---

## 0. 현재 상태 (점검 결과)

| 항목 | 상태 | 비고 |
|---|---|---|
| `.env` 키 | ✅ | `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, **`LANGFUSE_BASE_URL`** |
| 토글 | ❌ | `LANGFUSE_TRACING_ENABLED` 없음 → 추가 필요 |
| 패키지 | ❌ | `langfuse` 미설치, `requirements.txt`에도 없음 |
| `app/config.py` | ❌ | langfuse 필드 없음 |
| 계측 코드 | ❌ | `app/core/tracing.py` + 4곳 |
| Qwen 활성 | ⚠️ | trace를 보려면 ON 전제 ([config 기본 None](../app/config.py#L34)) |

**변수명 결정**: env는 `LANGFUSE_BASE_URL`로 들어가 있음. env를 건드리지 않고 **config의 alias로 그대로 흡수**한다(아래 Step 2). → env 수정 불필요.

---

## 1. 구현 순서 (5 step)

설계 §5 순서를 현재 상태에 맞춰 구체화. 각 step은 독립 검증 가능.

### Step 0 — 설치
```bash
pip install "langfuse>=3.0.0"
```
`requirements.txt`에 추가:
```
langfuse>=3.0.0
```
검증: `python -c "import langfuse; print(langfuse.__version__)"` → 3.x 출력.

### Step 1 — config 필드 (env alias 맞춤)
[app/config.py](../app/config.py) `Settings`에 추가. **`langfuse_host`의 alias를 `LANGFUSE_BASE_URL`로** 둬서 현재 env를 그대로 받는다:
```python
langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_BASE_URL")
langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_TRACING_ENABLED")
```
`.env`에 토글 1줄 추가:
```bash
LANGFUSE_TRACING_ENABLED=True
```
검증:
```bash
python -c "from app.config import get_settings as g; s=g(); print(s.langfuse_enabled, bool(s.langfuse_public_key), s.langfuse_host)"
# → True True https://...   (셋 다 채워지면 OK)
```

### Step 2 — 게이트 클라이언트: 신규 `app/core/tracing.py`
키/토글 없으면 `None` 반환 → 호출부 전부 no-op. (운영 영향 0, 임포트 비용/MPS 메모리 보호)
```python
from functools import lru_cache
from app.config import get_settings

@lru_cache
def get_langfuse():
    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        return None
    from langfuse import Langfuse
    return Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
        host=s.langfuse_host,
    )
```
검증:
```bash
python -c "from app.core.tracing import get_langfuse; print(get_langfuse())"
# 토글 True+키 → <Langfuse ...>, 아니면 None
```

### Step 3 — 워커 flush (가장 먼저 동작 보장해야 할 곳)
[app/workers/analysis_worker.py](../app/workers/analysis_worker.py) 메인 루프 종료/예외 `finally`에:
```python
from app.core.tracing import get_langfuse
...
finally:
    lf = get_langfuse()
    if lf:
        lf.flush()
```
> **1순위 함정**: 워커가 단발/배치라 flush 없으면 trace가 서버로 안 올라간다. 다른 계측보다 이걸 먼저 넣고, "trace가 실제로 도착하는지"를 Step 4에서 확인한다.

### Step 4 — generation span: [analyze:266](../app/domain/evidence_analysis.py#L266)
LLM 호출 구간. raw 출력/토큰/truncated 기록. (2단계 vLLM 전환 시 이 블록만 `langfuse.openai`로 교체)
```python
def analyze(self, title, text, *, kind, max_new_tokens=768):
    from app.core.tracing import get_langfuse
    lf = get_langfuse()
    prompt = self._build_prompt(title, text, kind)
    cm = lf.start_as_current_generation(
        name="qwen-evidence", model=self.model_name, input=prompt,
        metadata={"kind": kind, "input_chars": len(text)},
    ) if lf else None
    try:
        # ── 기존 generate/decode 그대로 ──
        parsed = self._parse_json(raw)
        if cm:
            cm.update(
                output=raw,
                usage_details={"input": int(inputs.input_ids.shape[-1]),
                               "output": int(output_ids.shape[-1])},
                metadata={"parsed_ok": parsed is not None,
                          "truncated": output_ids.shape[-1] >= max_new_tokens},
            )
        return parsed
    except Exception as exc:
        if cm: cm.update(level="ERROR", status_message=str(exc))
        logger.info("local Qwen analysis unavailable: %s", exc)
        return None
    finally:
        if cm: cm.end()
```
검증: Qwen ON 상태로 공시 1건 처리 → Langfuse UI에 `qwen-evidence` generation 1개 표시.

### Step 5 — 부모 span + score: [analyze_text:623](../app/domain/evidence_analysis.py#L623)
하네스 단계 가시화(진단 핵심, 2단계에서도 그대로 재사용). 이미 모으는 `qwen_raw` dict를 흘리고, drop 수/생존율만 추가:
```python
from app.core.tracing import get_langfuse
lf = get_langfuse()
parent = lf.start_as_current_span(
    name="evidence-enrich", input={"source_type": source_type, "title": title},
) if lf else None
try:
    # ── 기존 본문 전부 그대로 ──
    if parent:
        parent.update(
            output={"sentiment": sentiment, "impact": impact_score,
                    "event_type": event_type, "status": raw_response["status"]},
            metadata={"gate": gate, "qwen": qwen_raw,
                      "failure_reason": raw_response.get("failure_reason"),
                      "key_points_dropped":
                          len(parsed.get("key_points", [])) - len(cand_keys)},
        )
        lf.score_current_trace(
            name="grounding_survival",
            value=len(cand_keys) / max(len(parsed.get("key_points", [])), 1))
finally:
    if parent: parent.end()
```
> 주의: `parsed`/`cand_keys`는 gate 미통과 시 정의되지 않을 수 있음 → `metadata`/score 계산을 `if gate and parsed:` 가드 안에서 하거나 기본값 처리.

---

## 2. 동작 검증 (end-to-end)

1. `.env`: `ANALYSIS_GENERATION_MODEL` 지정([이미 있음](../.env#L36)) + `LANGFUSE_TRACING_ENABLED=True`.
2. 워커 기동: `python -m app.workers.analysis_worker`.
3. 게이트 통과 공시 1건 유입(또는 기존 pending job 처리) 대기.
4. Langfuse UI(=`LANGFUSE_BASE_URL`) → Traces에 `evidence-enrich` 1건, 하위 `qwen-evidence` generation 확인.
5. trace에 `grounding_survival` score, metadata(`gate`/`truncated`/`key_points_dropped`) 보이면 성공.

체크포인트:
- trace 안 보임 → ① 토글/키(Step 1 검증) ② flush(Step 3) ③ Qwen ON 여부 순으로 점검.
- generation은 보이나 부모 span 없음 → Step 5 가드(`parsed` 미정의)로 예외 → 로그 확인.

---

## 3. 완료 정의 (DoD)
- [x] `langfuse` 설치 + requirements 반영 (설치본 **4.7.1**)
- [x] config 4필드(+`LANGFUSE_BASE_URL` alias) / env 토글
- [x] `app/core/tracing.py` no-op 가드 동작
- [x] 워커 flush
- [x] generation span(B) + 부모 span/score(A)
- [x] Langfuse UI에서 trace 1건 end-to-end 확인 (공시 454910)
- [x] 토글 off 시 기존 동작 무변화(회귀 없음) 확인 (test 14 passed)
- [x] 테스트가 운영 Langfuse 오염 안 하도록 격리 ([tests/conftest.py](../tests/conftest.py))

### 구현 메모 (플랜 대비 차이)
- 설치 langfuse가 **v4.7.1** → 플랜의 `start_as_current_generation` API 없음. v4 통합 API
  `start_as_current_observation(as_type="generation"|"span")` + `score_current_trace(name=, value=)` 로 적응.
- 부모-자식 중첩: `analyze_text` 를 wrapper(부모 span)로, `analyze()` 를 자식 generation 으로 두어
  OTel current-context 로 자동 중첩. 진단 카운트(`key_points_dropped`/`grounding_survival`)는 기존
  `qwen_raw` dict 에 실어 wrapper 가 꺼내 쓴다(본문 로직 무변).

---

## 4. 다음 (2단계 예고)
1단계 진단에서 `consistency=conflict`/품질 천장 확인되면 → [설계 §7~10](./langfuse-sllm-tracing-design.md) vLLM 서빙 전환. 그때 Step 4(generation span)만 `langfuse.openai` 자동계측으로 교체, 나머지(Step 1·2·3·5)는 유지.

---

## 5. 실측 진단 1호 — summary 신뢰불가 + 층1 조치 (2026-06-15)

trace를 붙이자마자 첫 실측에서 잡힌 문제. **트레이싱이 곧바로 값을 한 사례.**

### 발견 (공시 454910 매출액또는손익구조변경, 손익구조변경)
generation span(raw Qwen 출력)과 부모 span(최종본)을 비교하니 **summary가 두 경로 모두 불량**:
- **generative 경로**: 1.5B가 당해/직전/증감 칸을 못 가려 *방향 틀린 비문* 생성
  (예: "32,978,338에서 46,829,944로 29.6% 감소" — 작은 수→큰 수인데 '감소").
- **extractive 폴백 경로**: Qwen summary 탈락 시 룰엔진이 *표 행을 그대로 덤프*
  (예: `... | 연결: 32,978,338 | 연결: 46,829,944 | 연결: -29.6`).
- **하네스 구멍**: [_validate_summary](../app/domain/evidence_analysis.py#L876)가 `len>300 && pipe_density>3%`
  조건이라 **짧은 표덤프(<300자)는 검사를 빠져나감**. 또 수치 grounding은 "숫자 존재"만 보고
  **방향(증가/감소) 의미오류는 못 잡음**.
- 반면 **key_points(매출액/영업이익/당기순이익 감소)는 깨끗** → `grounding_survival: 1.0`.

→ 결론: **표 공시에서 자연어 summary는 모델(1.5B)도 룰엔진도 신뢰 불가. key_points는 신뢰 가능.**

### 조치 (층1 — 모델/서빙 손 안 대고 즉시)
| | 변경 | 위치 |
|---|---|---|
| **A. 결정적 summary** | 표 공시 summary 를 검증된 key_points 로 조립(`{title} — k1 · k2 · k3`). 모델 산문/표덤프 차단. 뉴스(prose)는 기존 generative 유지. | [evidence_analysis.py](../app/domain/evidence_analysis.py#L751) (`summary_provider="structured"`) |
| **B. 검증 강화** | `_validate_summary` 의 `len>300` 게이트 제거 → **파이프 2개 이상이면 노이즈**로 폴백. 짧은 표덤프도 차단. | [_validate_summary](../app/domain/evidence_analysis.py#L876) |

### 검증 (동일 공시 재실행)
```
[전] FINAL summary: ...재무제표의 종류: - 매출액 | 연결: 32,978,338 | 연결: 46,829,944 | 연결: -29.6   (provider=extractive)
[후] FINAL summary: 매출액또는손익구조...변경 — 매출액 감소 · 영업이익 감소 · 당기순이익 감소        (provider=structured)
```
sentiment/impact/event_type/key_points 불변, `grounding_survival 1.0` 유지, test_domain 14 passed.

### 남은 과제 → 2단계로
- **방향 의미오류 자체**(1.5B의 표 칸 혼동)는 층1로 못 고침 — summary를 회피했을 뿐.
  모델 summary를 다시 신뢰하려면 **vLLM + 3B/7B + guided JSON**(2단계)가 필요.
- 선택: grounding에 *방향 일관성* 검증 추가(증감 부호 ↔ "증가/감소" 단어 대조) — 별도 트랙.
