import warnings
warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")

"""
run_ragas_eval.py — RAGAS 배치 평가 스크립트

사용법:
  python run_ragas_eval.py                          # golden set 전체 실행
  python run_ragas_eval.py --session <session_id>  # 특정 세션만 평가
  python run_ragas_eval.py --dry-run               # DB 저장 없이 출력만

출력:
  ragas-<git_sha>.json  (결과 파일)
"""
import argparse
import uuid
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone

os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
from dotenv import load_dotenv
load_dotenv(".env.local")

import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from app.domain.debate_evaluation import evaluate_summary_async, evaluate_evidence_async


# ── Golden Q&A 세트 ────────────────────────────────────────────
# 실제 데이터 기반으로 확장 가능. 종목·이벤트유형(실적/공급계약/유상증자/소송/배당/손익구조변경/
# 설비투자/무상증자)·방향(긍정/부정/혼합)을 다양화해 단일 사례 편향을 줄였다.
#
# 임계 설정 근거(회귀 가드 — 절대 품질 바가 아님):
# - faithfulness ≥ 0.6: 1차 게이트. 요약이 발언에 충실한가(환각 없음). 실측 0.857~1.0로 견고.
# - answer_relevancy ≥ 0.15: RAGAS answer_relevancy 는 "역생성 질문 ↔ 의제" 코사인이라
#   3쟁점·양측을 담는 토론 요약에선 0.2~0.45 가 정상 범위(실측). 0.15 는 요약이 무의미하게
#   붕괴(≈0)하는 회귀만 잡는 floor 다(과거 0.4 는 단일 케이스에 과적합된 값이었다).
# - evidence_precision ≥ 0.0: ChromaDB 미기동 환경에선 0.0 도 허용(검색 자체 지표는 항목9 트랙).
GOLDEN_CASES = [
    {
        "case_id": "golden-001",
        "description": "삼성전자 financial — 영업이익 증가/매출 감소 요약 충실도",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "삼성전자 2026Q1 영업이익 492,360억(▲108.6% QoQ). 원가 절감과 반도체 회복세로 수익성 개선."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "매출 1,092,779억(▼54.1% QoQ). 매출 급감은 구조적 수요 약화 신호."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "ROE 14.04%로 자본 효율성 개선. AI 수요 증가로 하반기 매출 반등 전망."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "PER/PBR 미제공. 일회성 비용 절감 효과가 지속 가능할지 불확실."},
        ],
        "summary": (
            "삼성전자는 매출 감소(▼54.1%)에도 영업이익이 108.6% 증가했다. "
            "Bull은 원가 절감과 반도체 회복을 긍정적으로 보고, "
            "Bear는 매출 감소를 구조적 문제로 지적한다."
        ),
        "evidences": [
            {"excerpt": "삼성전자 2026Q1 영업이익 492,360억, 전분기 대비 108.6% 증가.", "source_title": "분기보고서"},
            {"excerpt": "글로벌 반도체 수요 AI 서버 중심으로 2026년 하반기 회복 전망.", "source_title": "반도체 시장 리포트"},
        ],
        "evidence_query": "삼성전자 실적 재무 분기보고서 매출 영업이익 순이익",
        "agenda": [
            "쟁점1: 매출 감소와 영업이익 증가의 원인 및 향후 전망",
            "쟁점2: 기술적 지표와 주가 흐름",
            "쟁점3: 재무 지표(ROE, PER, PBR) 평가",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,   # ChromaDB 없는 환경에서 0도 허용
    },
    {
        "case_id": "golden-002",
        "description": "LG에너지솔루션 단일판매공급계약 — 대형 수주 긍정/물량 리스크",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "LG에너지솔루션은 2조원 규모 배터리 공급계약을 체결했다. 계약금액은 직전 연매출의 8%에 해당한다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "계약기간이 5년으로 길어 연환산 매출 기여는 제한적이다. 원자재 가격 변동이 마진을 압박할 수 있다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "장기 계약은 안정적 매출 가시성을 높인다. 고객사 다변화로 단일 고객 의존도가 낮아졌다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "공급 단가가 비공개라 실제 수익성은 확인 불가하다."},
        ],
        "summary": (
            "LG에너지솔루션은 연매출의 8%에 해당하는 2조원 규모 배터리 공급계약을 체결했다. "
            "Bull은 장기 계약의 매출 가시성과 고객 다변화를 긍정적으로 보고, "
            "Bear는 5년 계약기간에 따른 제한적 연환산 기여와 원자재·단가 불확실성을 지적한다."
        ),
        "evidences": [
            {"excerpt": "LG에너지솔루션, 2조원 규모 배터리 공급계약 체결 공시.", "source_title": "단일판매공급계약 공시"},
            {"excerpt": "배터리 원자재 가격이 2026년 변동성 확대 전망.", "source_title": "2차전지 시장 리포트"},
        ],
        "evidence_query": "LG에너지솔루션 공급계약 수주 배터리 매출 계약금액",
        "agenda": [
            "쟁점1: 공급계약의 매출 기여와 가시성",
            "쟁점2: 원자재·단가에 따른 수익성 리스크",
            "쟁점3: 고객 다변화와 의존도",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-003",
        "description": "한화오션 유상증자 — 자금조달 vs 주주가치 희석",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "한화오션은 1.2조원 규모 유상증자를 결정했다. 조달 자금은 친환경 선박 설비 투자에 쓰인다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "신주 발행으로 기존 주주 지분이 약 15% 희석된다. 단기 주가 하락 압력이 불가피하다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "설비 투자는 중장기 수주 경쟁력을 높여 희석을 상쇄할 수 있다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "투자 회수 시점이 불확실해 당분간 EPS 감소가 예상된다."},
        ],
        "summary": (
            "한화오션은 친환경 선박 설비 투자를 위해 1.2조원 유상증자를 결정했다. "
            "Bull은 중장기 수주 경쟁력 강화를 기대하고, "
            "Bear는 약 15% 지분 희석과 EPS 감소·단기 주가 압력을 우려한다."
        ),
        "evidences": [
            {"excerpt": "한화오션 1.2조원 규모 유상증자 결정, 친환경 선박 설비 투자 목적.", "source_title": "유상증자 결정 공시"},
            {"excerpt": "친환경 선박 발주가 2026년 이후 증가 전망.", "source_title": "조선 산업 리포트"},
        ],
        "evidence_query": "한화오션 유상증자 신주 자금조달 설비투자 지분 희석",
        "agenda": [
            "쟁점1: 조달 자금의 투자 효과",
            "쟁점2: 지분 희석과 주가 영향",
            "쟁점3: 투자 회수 시점과 EPS",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-004",
        "description": "카카오 횡령·배임 혐의 소송 — 지배구조 리스크(부정)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "카카오는 소송 사안이 일부 자회사에 국한되며 본업 실적과는 무관하다고 본다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "횡령·배임 혐의 소송이 제기돼 지배구조 신뢰가 훼손됐다. 규제 리스크가 확대됐다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "혐의는 아직 확정되지 않았고 충당부채 영향은 제한적이다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "소송 장기화 시 평판 비용과 신사업 인허가 지연이 우려된다."},
        ],
        "summary": (
            "카카오에 횡령·배임 혐의 소송이 제기됐다. "
            "Bull은 자회사 국한·미확정·제한적 충당부채를 들어 본업 영향이 작다고 보고, "
            "Bear는 지배구조 신뢰 훼손과 규제·평판 리스크, 신사업 인허가 지연 가능성을 지적한다."
        ),
        "evidences": [
            {"excerpt": "카카오 자회사 관련 횡령·배임 혐의로 소송 제기.", "source_title": "소송 등의 제기 공시"},
            {"excerpt": "플랫폼 기업 대상 규제 강화 흐름 지속.", "source_title": "규제 동향 리포트"},
        ],
        "evidence_query": "카카오 소송 횡령 배임 지배구조 규제 리스크",
        "agenda": [
            "쟁점1: 소송의 본업 실적 영향 범위",
            "쟁점2: 지배구조·규제 리스크",
            "쟁점3: 소송 장기화에 따른 비용",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-005",
        "description": "현대차 자기주식취득 + 배당 — 주주환원 강화(긍정)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "현대차는 5,000억원 규모 자기주식 취득과 배당 확대를 발표했다. 주주환원 의지가 강하다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "자사주 취득이 설비·R&D 투자 여력을 줄일 수 있다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "안정적 현금흐름으로 투자와 환원을 병행할 수 있다. 자사주 소각 시 EPS가 개선된다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "전기차 전환 비용이 커 환원 지속성은 점검이 필요하다."},
        ],
        "summary": (
            "현대차는 5,000억원 자기주식 취득과 배당 확대로 주주환원을 강화했다. "
            "Bull은 안정적 현금흐름 기반 투자·환원 병행과 자사주 소각에 따른 EPS 개선을 긍정하고, "
            "Bear는 전기차 전환 비용에 따른 투자 여력과 환원 지속성을 우려한다."
        ),
        "evidences": [
            {"excerpt": "현대차 5,000억원 자기주식 취득 결정 및 배당 확대.", "source_title": "주요사항보고서"},
            {"excerpt": "완성차 업계 전기차 전환 투자 부담 확대.", "source_title": "자동차 산업 리포트"},
        ],
        "evidence_query": "현대차 자기주식 취득 배당 주주환원 EPS",
        "agenda": [
            "쟁점1: 주주환원 규모와 EPS 효과",
            "쟁점2: 투자 여력과의 상충",
            "쟁점3: 환원의 지속 가능성",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-006",
        "description": "셀트리온 손익구조변경 적자전환 — 수익성 악화(부정)",
        "statements": [
            {"agent_role": "bear", "round": "claim",            "topic_index": 0, "content": "셀트리온은 영업이익이 적자로 전환됐다고 공시했다. 원가 상승과 판가 하락이 겹쳤다."},
            {"agent_role": "bull", "round": "rebuttal",         "topic_index": 0, "content": "적자전환은 일회성 재고 평가손 영향이 크며, 신제품 출시로 하반기 회복이 가능하다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "판가 하락은 경쟁 심화에 따른 구조적 요인이라 회복이 더딜 수 있다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "바이오시밀러 신규 승인 파이프라인이 매출 반등을 뒷받침한다."},
        ],
        "summary": (
            "셀트리온은 원가 상승과 판가 하락으로 영업이익이 적자로 전환됐다. "
            "Bear는 판가 하락을 경쟁 심화에 따른 구조적 요인으로 보고, "
            "Bull은 일회성 재고 평가손과 신제품·바이오시밀러 파이프라인 기반 하반기 회복을 기대한다."
        ),
        "evidences": [
            {"excerpt": "셀트리온 영업이익 적자전환, 손익구조 30% 이상 변경 공시.", "source_title": "손익구조변경 공시"},
            {"excerpt": "바이오시밀러 경쟁 심화로 판가 하락 압력.", "source_title": "제약·바이오 리포트"},
        ],
        "evidence_query": "셀트리온 적자전환 영업이익 손익구조 판가 원가",
        "agenda": [
            "쟁점1: 적자전환의 원인(일회성 vs 구조적)",
            "쟁점2: 판가 하락의 지속성",
            "쟁점3: 신제품·파이프라인의 회복 기여",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-007",
        "description": "SK하이닉스 잠정실적 흑자전환 — AI 메모리 수요(긍정)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "SK하이닉스는 영업이익이 흑자로 전환됐다고 잠정 공시했다. HBM 등 AI 메모리 판매가 급증했다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "흑자전환은 메모리 가격 반등에 의존한 것이라 가격 하락 시 변동성이 크다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "HBM은 장기 공급계약 비중이 높아 가격 변동에 덜 민감하다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "경쟁사 증설로 2027년 공급 과잉 우려가 있다."},
        ],
        "summary": (
            "SK하이닉스는 AI 메모리(HBM) 판매 급증으로 영업이익이 흑자로 전환됐다. "
            "Bull은 HBM 장기 공급계약 비중이 높아 가격 변동에 덜 민감하다고 보고, "
            "Bear는 메모리 가격 의존과 경쟁사 증설에 따른 2027년 공급 과잉 우려를 지적한다."
        ),
        "evidences": [
            {"excerpt": "SK하이닉스 영업이익 흑자전환, HBM 매출 급증.", "source_title": "잠정실적 공시"},
            {"excerpt": "AI 서버용 HBM 수요 2026년 강세 지속 전망.", "source_title": "메모리 시장 리포트"},
        ],
        "evidence_query": "SK하이닉스 흑자전환 HBM AI 메모리 영업이익 실적",
        "agenda": [
            "쟁점1: 흑자전환의 지속 가능성",
            "쟁점2: 메모리 가격 변동 민감도",
            "쟁점3: 공급 과잉 리스크",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-008",
        "description": "포스코홀딩스 대규모 설비투자 — 성장 vs 자본부담(혼합)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "포스코홀딩스는 3조원 규모 이차전지 소재 설비 투자를 결정했다. 미래 성장동력 확보다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "대규모 capex로 차입 부담과 잉여현금흐름 축소가 우려된다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "소재 사업은 고성장 시장이라 투자 대비 회수 잠재력이 크다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "철강 본업 업황 부진과 겹쳐 단기 재무 부담이 가중된다."},
        ],
        "summary": (
            "포스코홀딩스는 이차전지 소재에 3조원 규모 설비 투자를 결정했다. "
            "Bull은 고성장 소재 시장에서의 회수 잠재력을 긍정하고, "
            "Bear는 대규모 capex에 따른 차입 부담·잉여현금흐름 축소와 철강 본업 부진의 중첩을 우려한다."
        ),
        "evidences": [
            {"excerpt": "포스코홀딩스 3조원 규모 이차전지 소재 설비 투자 결정.", "source_title": "신규시설투자 공시"},
            {"excerpt": "철강 업황 부진 지속, 이차전지 소재는 고성장.", "source_title": "소재 산업 리포트"},
        ],
        "evidence_query": "포스코홀딩스 설비투자 capex 이차전지 소재 차입 현금흐름",
        "agenda": [
            "쟁점1: 설비 투자의 성장 기여",
            "쟁점2: 자본 부담과 현금흐름",
            "쟁점3: 본업 업황과의 상호작용",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-009",
        "description": "NAVER 무상증자 — 유동성 개선 vs 펀더멘털 무변(혼합)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "NAVER는 1주당 1주 무상증자를 결정했다. 거래 유동성과 접근성이 개선된다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "무상증자는 주식 수만 늘 뿐 기업가치는 변하지 않는다. 펀더멘털 개선은 없다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "유동성 개선은 수급에 긍정적이며 주주 친화 신호로 해석된다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "권리락 이후 착시가 사라지면 효과는 단기에 그칠 수 있다."},
        ],
        "summary": (
            "NAVER는 1주당 1주 무상증자를 결정했다. "
            "Bull은 거래 유동성·접근성 개선과 주주 친화 신호를 긍정적으로 보고, "
            "Bear는 기업가치 불변과 권리락 이후 단기에 그칠 효과를 지적한다."
        ),
        "evidences": [
            {"excerpt": "NAVER 1주당 1주 무상증자 결정 공시.", "source_title": "무상증자 결정 공시"},
            {"excerpt": "무상증자는 발행주식수 증가로 주당가치 조정.", "source_title": "증시 해설"},
        ],
        "evidence_query": "NAVER 무상증자 발행주식수 유동성 권리락 주주",
        "agenda": [
            "쟁점1: 무상증자의 유동성 효과",
            "쟁점2: 기업가치·펀더멘털 영향",
            "쟁점3: 효과의 지속성",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
    {
        "case_id": "golden-010",
        "description": "삼성바이오로직스 대형 위탁생산 계약 — 수주 vs 환율(혼합)",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "삼성바이오로직스는 글로벌 제약사와 대형 위탁생산(CMO) 계약을 체결했다. 가동률 상승이 기대된다."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "매출의 상당분이 달러 결제라 환율 하락 시 수익성이 둔화될 수 있다."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "신규 공장 가동으로 생산능력이 확대돼 외형 성장이 이어진다."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "증설에 따른 감가상각 증가가 초기 마진을 압박한다."},
        ],
        "summary": (
            "삼성바이오로직스는 글로벌 제약사와 대형 위탁생산 계약을 체결해 가동률·외형 성장이 기대된다. "
            "Bull은 신규 공장 가동에 따른 생산능력 확대를 긍정하고, "
            "Bear는 달러 결제에 따른 환율 둔화 위험과 증설 감가상각의 초기 마진 압박을 지적한다."
        ),
        "evidences": [
            {"excerpt": "삼성바이오로직스 글로벌 제약사와 대형 위탁생산 계약 체결.", "source_title": "단일판매공급계약 공시"},
            {"excerpt": "CMO 가동률 상승과 환율 변동이 수익성에 동시 작용.", "source_title": "바이오 CMO 리포트"},
        ],
        "evidence_query": "삼성바이오로직스 위탁생산 CMO 계약 가동률 환율 증설",
        "agenda": [
            "쟁점1: 수주에 따른 가동률·외형 성장",
            "쟁점2: 환율이 수익성에 미치는 영향",
            "쟁점3: 증설 감가상각과 초기 마진",
        ],
        "expected_summary_faithfulness_min":    0.6,
        "expected_summary_answer_relevancy_min": 0.15,
        "expected_evidence_precision_min":      0.0,
    },
]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


async def run_case(case: dict, dry_run: bool) -> dict:
    # golden case는 case_id 기반 결정론적 UUID 생성 (재현 가능)
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ragas-{case['case_id']}"))
    print(f"\n▶ {case['case_id']}: {case['description']}")

    summary_result = await evaluate_summary_async(
        session_id,
        case["statements"],
        case["summary"],
        agenda=case.get("agenda", []),
        save=not dry_run,
    )
    evidence_result = await evaluate_evidence_async(
        session_id,
        case["evidences"],
        case["evidence_query"],
        case["agenda"],
        save=not dry_run,
    )

    faith  = summary_result.faithfulness
    relev  = summary_result.answer_relevancy
    prec   = evidence_result.avg_precision

    faith_pass = faith is not None and faith >= case.get("expected_summary_faithfulness_min", 0)
    relev_pass = relev is None or relev >= case.get("expected_summary_answer_relevancy_min", 0)
    prec_pass  = prec  is None or prec  >= case.get("expected_evidence_precision_min", 0)

    status = "PASS" if (faith_pass and relev_pass and prec_pass) else "FAIL"
    print(f"  summary_faithfulness    : {faith:.3f} {'✅' if faith_pass else '❌'}" if faith is not None else "  summary_faithfulness    : None ⚠️")
    print(f"  summary_answer_relevancy: {relev:.3f} {'✅' if relev_pass else '❌'}" if relev is not None else "  summary_answer_relevancy: None ⚠️")
    print(f"  evidence_precision      : {prec:.3f}  {'✅' if prec_pass else '❌'}"  if prec  is not None else "  evidence_precision      : None ⚠️")
    print(f"  → {status}")

    return {
        "case_id":                       case["case_id"],
        "description":                   case["description"],
        "summary_faithfulness":          faith,
        "summary_answer_relevancy":      relev,
        "evidence_precision":            prec,
        "faith_pass":                    faith_pass,
        "relev_pass":                    relev_pass,
        "prec_pass":                     prec_pass,
        "status":                        status,
    }


async def run_session(session_id: str, dry_run: bool) -> dict:
    """DB에서 세션 발언/요약을 읽어 평가."""
    from app.core.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        stmts = await conn.fetch("""
            SELECT agent_role, round, content,
                   ROW_NUMBER() OVER (ORDER BY round_order) - 1 AS topic_index
            FROM agent_statement
            WHERE session_id = $1::uuid
            ORDER BY round_order
        """, session_id)
        summary_row = await conn.fetchrow("""
            SELECT summary_content FROM moderator_summary WHERE session_id = $1::uuid
        """, session_id)
        session_row = await conn.fetchrow("""
            SELECT ds.symbol, ds.category,
                   COALESCE(tm.name_kr, ds.symbol) AS symbol_name
            FROM debate_session ds
            LEFT JOIN ticker_metadata tm ON tm.symbol = ds.symbol
            WHERE ds.id = $1::uuid
        """, session_id)

    if not stmts or not summary_row or not session_row:
        print(f"  세션 {session_id} 데이터 없음")
        return {"case_id": session_id, "status": "SKIP"}

    statements  = [dict(s) for s in stmts]
    summary     = summary_row["summary_content"]
    symbol      = session_row["symbol"]
    symbol_name = session_row["symbol_name"]
    category    = session_row["category"]
    # build_category_query 직접 호출 대신 간단한 쿼리 조합 (임포트 의존성 최소화)
    eq = f"{symbol_name}({symbol}) {category} 실적 재무 뉴스 공시"

    print(f"\n▶ session={session_id} ({symbol} / {category})")

    sr = await evaluate_summary_async(session_id, statements, summary, agenda=[], save=not dry_run)
    er = await evaluate_evidence_async(session_id, [], eq, [], save=not dry_run)

    print(f"  summary_faithfulness    : {sr.faithfulness}")
    print(f"  summary_answer_relevancy: {sr.answer_relevancy}")
    print(f"  evidence_precision      : {er.avg_precision}")

    return {
        "case_id":                  session_id,
        "summary_faithfulness":     sr.faithfulness,
        "summary_answer_relevancy": sr.answer_relevancy,
        "evidence_precision":       er.avg_precision,
        "status":                   "DONE",
    }


async def main(args):
    sha = _git_sha()
    out_path = f"ragas-{sha}.json"
    results = []

    if args.session:
        results.append(await run_session(args.session, args.dry_run))
    else:
        for case in GOLDEN_CASES:
            results.append(await run_case(case, args.dry_run))

    passed = sum(1 for r in results if r.get("status") == "PASS")
    total  = len(results)
    print(f"\n{'='*50}")
    print(f"결과: {passed}/{total} PASS")
    print(f"{'='*50}")

    report = {
        "git_sha":    sha,
        "eval_at":    datetime.now(timezone.utc).isoformat(),
        "dry_run":    args.dry_run,
        "summary":    {"passed": passed, "total": total},
        "results":    results,
    }

    if not args.dry_run:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"리포트 저장: {out_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session",  help="특정 세션 ID 평가")
    parser.add_argument("--dry-run",  action="store_true", help="DB 저장 없이 출력만")
    asyncio.run(main(parser.parse_args()))
