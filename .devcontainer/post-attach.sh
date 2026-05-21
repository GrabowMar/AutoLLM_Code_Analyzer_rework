#!/usr/bin/env bash
set -euo pipefail

git config --global --add safe.directory /app 2>/dev/null || true

# Start Django (migrate + uvicorn) without blocking attach or killing the container on failure.
if [[ ! -f /tmp/django-start.pid ]] || ! kill -0 "$(cat /tmp/django-start.pid)" 2>/dev/null; then
  nohup /start >/tmp/django-start.log 2>&1 &
  echo $! >/tmp/django-start.pid
  echo "Django starting in background — logs: /tmp/django-start.log"
fi
