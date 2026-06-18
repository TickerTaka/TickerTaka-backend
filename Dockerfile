# syntax=docker/dockerfile:1
# ── build stage: 휠 컴파일용 도구로 의존성 설치(최종 이미지엔 안 남김) ──
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential 은 일부 휠 컴파일에만 필요 → builder 에만 둔다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 격리된 venv 에 설치 → runtime 스테이지로 통째 복사(빌드 캐시·도구 분리)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ── runtime stage: 실행에 필요한 것만 ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# 런타임 의존: healthcheck용 curl + torch/sklearn 의 OpenMP 런타임(libgomp1).
# build-essential 은 제외 → 최종 이미지에서 빌드 도구 제거.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# builder 에서 설치한 venv(= 모든 파이썬 의존성)만 가져온다.
COPY --from=builder /opt/venv /opt/venv

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
