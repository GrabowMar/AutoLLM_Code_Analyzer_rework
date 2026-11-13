FROM node:24-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* frontend/
RUN cd frontend \
    && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ frontend/
RUN cd frontend && npm run build

FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock* backend/
RUN if [ -f backend/requirements.lock ]; then \
      pip install --no-cache-dir -r backend/requirements.txt -c backend/requirements.lock; \
    else \
      pip install --no-cache-dir -r backend/requirements.txt; \
    fi
COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist
RUN mkdir -p data
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--pythonpath", "backend", "app:app"]
