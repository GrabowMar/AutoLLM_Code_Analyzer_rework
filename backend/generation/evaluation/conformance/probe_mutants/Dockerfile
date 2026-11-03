# Probe mutant reference app (spec 8.3): never deploy. LLMLAB_VULN / LLMLAB_MUTANT select the variant.
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
WORKDIR /app
COPY app.py /app/app.py
ENV PORT=8000 DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "/app/app.py"]
