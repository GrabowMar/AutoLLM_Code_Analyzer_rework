# Per-run LLM gateway sidecar (see gateway.py). Stdlib only; protocols/ holds the front protocols and
# budget_rules.py, the budget rules shared with the executor's local proxy.
FROM python:3.12.12-slim-bookworm

COPY gateway.py /opt/gateway/gateway.py
COPY protocols/ /opt/gateway/protocols/

RUN useradd --system --uid 10001 gateway && mkdir -p /logs && chown gateway /logs
USER gateway

EXPOSE 8080
CMD ["python", "-u", "-B", "/opt/gateway/gateway.py"]
