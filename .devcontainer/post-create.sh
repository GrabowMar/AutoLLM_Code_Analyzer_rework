#!/usr/bin/env bash
set -euo pipefail

git config --global --add safe.directory /app

grep -q 'bashrc.override.sh' ~/.bashrc 2>/dev/null \
  || echo 'source /app/.devcontainer/bashrc.override.sh' >> ~/.bashrc

# Python deps (incl. dev group: pytest, ruff, mypy, pre-commit, ...) into
# dev-user's venv at $UV_PROJECT_ENVIRONMENT.
uv sync --frozen

# Frontend deps for editor tooling, svelte-check and builds.
(cd frontend && npm ci)

pre-commit install --install-hooks || echo "pre-commit hook install failed; retry with: pre-commit install --install-hooks"
