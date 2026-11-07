# syntax=docker/dockerfile:1.19
# Python backend only (no frontend build)
FROM python:3.13-slim
WORKDIR /app
ARG SAMPLE_CACHE_NAMESPACE=sample

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup appuser \
    && mkdir -p /app/data \
    && chown appuser:appgroup /app /app/data

COPY requirements.txt ./
ARG SAMPLE_DEPENDENCY_REFRESH=0
RUN --mount=type=cache,id=${SAMPLE_CACHE_NAMESPACE}-pip,target=/root/.cache/pip \
    : "$SAMPLE_DEPENDENCY_REFRESH" && pip install -r requirements.txt

# Preserve the whole multi-module backend. Set ownership while copying,
# avoiding a recursive ownership walk after every source change.
COPY --chown=appuser:appgroup . .
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "app.py"]
