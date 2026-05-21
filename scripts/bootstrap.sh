#!/usr/bin/env bash
# Thin wrapper — implementation lives in bootstrap.py (cross-platform).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python "$(dirname "${BASH_SOURCE[0]}")/bootstrap.py"
