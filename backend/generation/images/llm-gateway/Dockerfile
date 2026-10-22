# Per-job LLM gateway sidecar for agent-mode generation (see gateway.py).
FROM python:3.12.12-slim-bookworm

COPY gateway.py /opt/gateway/gateway.py

RUN useradd --system --uid 10001 gateway && mkdir -p /logs && chown gateway /logs
USER gateway

EXPOSE 8080
CMD ["python", "-u", "/opt/gateway/gateway.py"]
