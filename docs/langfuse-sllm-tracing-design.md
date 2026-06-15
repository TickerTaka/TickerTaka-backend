# sLLM(Qwen) 운영 고도화 — Langfuse Trace(1단계) → vLLM 서빙(2단계)

> 작성: 2026-06-13
> 관련: [Qwen 구조화 분석 구현/성과](./evidence-qwen-structured-analysis-results.md), [고도화 로드맵](./evidence-analysis-enhancement-roadmap.md)
> 상태: **1단계(Langfuse) 구현 완료** (2026-06-15, 실행 문서: [langfuse-tracing-implementation.md](./langfuse-tracing-implementation.md)). 2단계(vLLM)는 미착수.

---

## 0. 두 단계로 나누는 이유

같은 지점([`LocalQwenEvidenceAnalyzer`](../app/domain/evidence_analysis.py#L253))에서 만나는 두 작업을 **순서대로** 진행한다.

- **1단계 — Langfuse trace (지금)**: Qwen 경로에 계측을 붙여 "결과가 왜 안 좋은지"를 데이터로 본다.
- **2단계 — vLLM 서빙 (나중)**: 추론을 `transformers.generate()` → vLLM 서빙으로 전환한다.

**왜 langfuse가 먼저인가**
1. 원래 동기가 *진단*이다. vLLM은 성능/서빙 개선이지 "왜 결과가 나쁜지"를 알려주지 않는다. **먼저 trace로 원인을 봐야** vLLM이 맞는 처방인지(아니면 모델 크기·프롬프트·grounding 임계 문제인지) 판단된다.
2. **선후 의존성이 없다.** langfuse는 현재 transformers 경로에 그대로 붙고, vLLM은 나중에 추론부만 교체. 서로 막지 않는다.
3. **작업이 버려지지 않는다.** 1단계 계측의 대부분이 2단계에서 그대로 재사용된다(§3 표 참고).

```
[1단계] transformers.generate()  + Langfuse 수동 계측   ← 지금 구현
[2단계] vLLM 서빙(OpenAI 호환)    + Langfuse 자동계측    ← §3-B만 교체, 나머지 재사용
```

---

## 1. trace란 무엇이고, 무엇을 바꾸나

**trace = "요청 1건이 시스템을 통과한 전 과정의 기록"**, 그 안에 단계별 구간(**span**)이 트리로 들어간다. 택배 송장처럼 공시/뉴스 1건의 분석 과정이 한 trace에 묶인다.

- **trace**: 공시/뉴스 1건의 enrich 전체.
- **span**: 그 안의 한 구간(gate, 트리밍, grounding, noise, consistency, validate).
- **generation**: LLM 호출 전용 span(프롬프트/출력/토큰/latency 특별 취급).

지금 이 정보는 `evidence_analysis.raw_response` JSON으로 **DB에 흩어져** 있어 건별 SQL로 까야 한다. trace는 같은 정보를 **UI에서 타임라인·필터·점수로** 보게 만든다.

**핵심 원칙: 로직은 안 바꾼다.** gate·trim·grounding·noise·consistency·validate 흐름은 그대로 두고, 각 단계가 들고 있는 값을 옆에서 보고만 하는 **계측선**을 끼운다. 결과(sentiment/impact)는 달라지지 않는다.

### 현황 / 제약
- **Langfuse 미설치**: deps·코드·`.env` 어디에도 없음.
- **로컬 transformers 모델**: `model.generate()` 직접 호출([analyze:266](../app/domain/evidence_analysis.py#L266)) → 자동계측 불가, **수동 span 필요**(1단계). 2단계 vLLM 전환 후엔 자동계측 가능.
- **별도 워커**([analysis_worker](../app/workers/analysis_worker.py)): 배치/단발성 → **flush 누락 시 trace 유실**.
- **Qwen 기본 OFF**: [analysis_generation_model=None](../app/config.py#L34) → `qwen_available=False`. **trace를 보려면 먼저 Qwen을 켜야 한다.**

---

# 1단계 — Langfuse Trace (지금 구현)

## 2. 무엇을 / 어떻게 바꾸나 (변경점 4곳)

코드 변경은 **딱 4곳.** 로직 추가/수정 없음, 계측선만 삽입. 우측 "2단계 영향" 열은 vLLM 전환 시 운명.

| 변경점 | 위치 | 2단계 영향 |
|---|---|---|
| A. 부모 span | [analyze_text:623](../app/domain/evidence_analysis.py#L623) | ✅ 그대로 재사용 |
| B. generation span | [analyze:266](../app/domain/evidence_analysis.py#L266) | ♻️ `langfuse.openai` 자동계측으로 교체 |
| C. 게이트 클라이언트 | 신규 `app/core/tracing.py` | ✅ 그대로 재사용 |
| D. 워커 flush | [analysis_worker.py](../app/workers/analysis_worker.py) | ✅ 그대로 재사용 |

→ **버려지는 건 B 한 조각뿐.** 진단 가치의 핵심인 A(하네스 단계 가시화)는 영구 자산.

### A. 부모 span — [analyze_text:623](../app/domain/evidence_analysis.py#L623) (trace의 뿌리)
**Before** — 결과를 `raw_response` dict에만 남기고 끝:
```python
def analyze_text(self, *, source_type, symbol, title, text, ...):
    ... gate / trim / analyze / grounding / noise / consistency / validate ...
    self.repository.upsert_analysis(**result.to_dict())
    return result
```
**After** — 전체를 부모 span으로 감싸고 단계 산출물·drop 수를 metadata로 보고 + 점수 부착:
```python
from app.core.tracing import get_langfuse
lf = get_langfuse()
parent = lf.start_as_current_span(
    name="evidence-enrich", input={"source_type": source_type, "title": title},
) if lf else None
try:
    ... 기존 본문 그대로 ...
    if parent:
        parent.update(
            output={"sentiment": sentiment, "impact": impact_score,
                    "event_type": event_type, "status": raw_response["status"]},
            metadata={"gate": gate, "qwen": qwen_raw,                  # 이미 모으는 dict 재사용
                      "failure_reason": raw_response.get("failure_reason"),
                      "key_points_dropped":
                          len(parsed.get("key_points", [])) - len(cand_keys)},
        )
        lf.score_current_trace(name="grounding_survival",
            value=len(cand_keys) / max(len(parsed.get("key_points", [])), 1))
finally:
    if parent: parent.end()
```
> 이미 [L695-704](../app/domain/evidence_analysis.py#L695-L704)의 `qwen_raw` dict가 `consistency`·`summary_grounded`·`llm_input_chars`를 모으고 있어 **그대로 흘리면 됨.**

### B. generation span — [analyze:266](../app/domain/evidence_analysis.py#L266) (2단계에서 교체될 부분)
**Before** — 무엇이 들어가고 나왔는지 흔적 없음:
```python
generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
raw = tokenizer.decode(output_ids, skip_special_tokens=True)
return self._parse_json(raw)
```
**After** — 프롬프트·raw 출력·토큰·truncated·에러 기록:
```python
cm = lf.start_as_current_generation(
    name="qwen-evidence", model=self.model_name, input=prompt,
    metadata={"kind": kind, "input_chars": len(text)}) if lf else None
try:
    ... 기존 generate ...
    parsed = self._parse_json(raw)
    if cm: cm.update(output=raw,
        usage_details={"input": int(inputs.input_ids.shape[-1]),
                       "output": int(output_ids.shape[-1])},
        metadata={"parsed_ok": parsed is not None,
                  "truncated": output_ids.shape[-1] >= max_new_tokens})
    return parsed
except Exception as exc:
    if cm: cm.update(level="ERROR", status_message=str(exc))
    return None
finally:
    if cm: cm.end()
```
> `truncated`는 과거 `qwen_no_output`(표 통째 복사 → 절단)을 즉시 잡는 신호.

### C. 게이트 클라이언트 — 신규 `app/core/tracing.py`
키/토글 없으면 **진입 자체 차단** → 운영 영향 0 + 임포트 비용·MPS 메모리 보호:
```python
from functools import lru_cache
from app.config import get_settings

@lru_cache
def get_langfuse():
    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        return None
    from langfuse import Langfuse
    return Langfuse(public_key=s.langfuse_public_key,
                    secret_key=s.langfuse_secret_key, host=s.langfuse_host)
```

### D. 워커 flush — [analysis_worker.py](../app/workers/analysis_worker.py)
배치/단발이라 **flush 없으면 trace 유실**(1순위 함정). 종료/예외 `finally`에 `get_langfuse().flush()`.

### 부수 설정 (deps / config / env)
```
# requirements.txt
langfuse>=3.0.0
```
```python
# app/config.py (Settings)
langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_TRACING_ENABLED")
```
```bash
# .env  (키 없으면 no-op, 기본 off → 진단 시에만 on)
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com   # self-host면 URL만 교체
LANGFUSE_TRACING_ENABLED=True
ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-1.5B-Instruct   # 이미 .env엔 있음 — Qwen ON 전제
```

## 3. 어떤 효과를 내나
| Before (지금) | After (trace) |
|---|---|
| "결과가 이상하다"만 알고 원인 모름 | **어느 단계에서 깎였는지** 구간별로 보임 |
| 건별 `raw_response` SQL 조회 | UI에서 정렬·필터·검색 |
| 건별로만 봄 | `grounding_survival < 0.3` 등 **조건으로 문제군 일괄 추출** |
| 단계별 소요시간 모름 | 어디서 30s 걸리는지 latency 분해 |

→ 즉답 가능: Qwen은 맞게 냈는데 grounding이 버렸나(output✓ + `key_points_dropped`↑) / 표 통째복사로 잘렸나(`truncated=True`) / 게이트가 빡빡해 호출이 안 되나(`gate=False`↑) / 1.5B 한계인가(`consistency=conflict`↑).
→ 최종 효과: **2단계(vLLM/모델 상향)가 맞는 처방인지를 데이터로 판단.**

## 4. 진단 매핑
| trace 신호 | 해석 | 조치 |
|---|---|---|
| output 멀쩡 + `key_points_dropped`↑ | grounding/noise 과다 | [_verify_numerical_grounding:975](../app/domain/evidence_analysis.py#L975) 정규식 점검 |
| `truncated=True` | tokens 부족 / 표 통째복사 절단 | 프롬프트 evidence 제한 강화 |
| `gate=False` 다수 | [_is_table_heavy:813](../app/domain/evidence_analysis.py#L813) 임계 빡빡 | 게이트 완화 |
| `consistency=conflict` 다수 | 1.5B 품질 천장 | **→ 2단계(vLLM 서빙 + 3B/7B)** |

## 5. 1단계 구현 순서
1. deps + config + `app/core/tracing.py`(C) — no-op 가드 확인.
2. 워커 flush(D).
3. generation span(B).
4. 부모 span + score(A).
5. Qwen ON + `LANGFUSE_TRACING_ENABLED=True` → 진단 → §4 매핑으로 원인 분류.

## 6. 리스크 / 주의
- **flush 누락이 1순위 함정**(D 빠지면 trace 안 올라감) — 가장 먼저 검증.
- raw 본문(최대 6000자)이 trace에 올라감 → `input`은 트리밍본/길이만 남기는 옵션 고려.
- span 생성은 가볍고 전송은 비동기 배치 → MPS 추론(12~30s) 대비 무시 가능.
- 기본 off → 상시 오버헤드 0.

---

# 2단계 — vLLM 서빙 (나중, 1단계 진단 후 착수)

> **착수 조건**: 1단계 진단에서 `consistency=conflict`/품질 천장이 확인되어 모델 상향·서빙 전환이 정당화될 때.

## 7. 무엇을 바꾸나
추론을 인프로세스 `generate()` → **vLLM 서빙(OpenAI 호환 API)** 호출로 전환. `_build_prompt`·`_parse_json`·게이팅·하네스(§2-A)는 **전부 재사용**.

```python
# analyze() 내부 — generate() 블록만 교체
resp = self._client().chat.completions.create(
    model=self.model_name,
    messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
    temperature=0, max_tokens=max_new_tokens,
    response_format={"type": "json_object"},   # JSON 강제 → _salvage 부담 감소
)
return self._parse_json(resp.choices[0].message.content)
```
- `_get_model()`/torch/MPS 로드([:293-305](../app/domain/evidence_analysis.py#L293-L305)) **삭제** → 워커 경량화.
- config에 `analysis_generation_base_url` 추가(`http://<host>:8000/v1`).
- 미설정/서버 다운 시 `analyze()`가 `None` → 기존 baseline(FinBERT) 폴백([L662, L707](../app/domain/evidence_analysis.py#L707)) 그대로 동작.

## 8. Langfuse 자동계측으로 전환 (§2-B 대체)
OpenAI 호환이 되므로 **import 한 줄**로 generation trace 자동화:
```python
from langfuse.openai import OpenAI   # 표준 openai 대신
```
→ 1단계의 수동 generation span(§2-B)은 제거, 부모 span(§2-A)·flush(§2-D)·클라이언트(§2-C)는 유지.

## 9. 서빙 위치 / 옵션
서버는 워커와 HTTP로 분리되므로 **내 PC일 필요 없음**(`base_url`만 가리키면 됨).

| 방식 | 비용 | 항목7(서빙) | 비고 |
|---|---|---|---|
| 클라우드 GPU 임대(RunPod 등) | 시간당 | ✅✅ | 표준 vLLM+CUDA, `vllm serve` 직접 운영 = 가장 강력. **권장** |
| 맥 vllm-metal | 무료 | ✅ | Python 3.12 arm64·MLX 모델(`mlx-community/...`) 필요, 내 머신 점유 |
| Colab/Kaggle 무료 GPU | 무료 | △ | 세션 만료·터널 필요, 데모용 |
| 서드파티 호스팅 API(OpenRouter 등) | 종량 | ❌ | "내 서빙"이 아니라 항목7 근거로 약함 |

> 외부 노출 시 방화벽 + `--api-key`(raw 공시 본문이 오감). 워커(맥)↔서버(원격)는 Python 버전 달라도 무방.

## 10. 2단계 구현 순서
1. 서빙 위치 결정(권장: 임대 GPU) → `vllm serve Qwen/Qwen2.5-1.5B-Instruct`(또는 3B/7B).
2. `curl .../v1/chat/completions`로 응답 확인.
3. config에 `analysis_generation_base_url` 추가, `analyze()` generate→client 교체(§7).
4. `langfuse.openai`로 import 교체(§8), §2-B 수동 span 제거.
5. baseline 폴백 동작 확인 후 워커 가동.
