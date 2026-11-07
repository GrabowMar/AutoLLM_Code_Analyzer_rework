# syntax=docker/dockerfile:1.19
# Stage 1: build the frontend (no lockfile: package.json pins exact versions)
FROM node:24-alpine AS frontend-builder
WORKDIR /frontend
ARG SAMPLE_CACHE_NAMESPACE=sample
COPY frontend/package*.json ./
ARG SAMPLE_DEPENDENCY_REFRESH=0
RUN --mount=type=cache,id=${SAMPLE_CACHE_NAMESPACE}-npm,target=/root/.npm \
    : "$SAMPLE_DEPENDENCY_REFRESH" && npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: Flask serves the API + the built SPA from ./static
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

# Preserve multi-module backend files without copying frontend source into
# the runtime image. Ownership is set while copying, avoiding a full tree walk.
COPY --chown=appuser:appgroup --exclude=frontend . .
COPY --from=frontend-builder --chown=appuser:appgroup /frontend/dist ./static
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "app.py"]
