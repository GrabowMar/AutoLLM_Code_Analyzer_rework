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
RUN mkdir -p data
EXPOSE 8000
CMD ["sh", "-c", "python backend/manage.py migrate --noinput && exec gunicorn --bind 0.0.0.0:8000 --pythonpath backend config.wsgi:application"]
