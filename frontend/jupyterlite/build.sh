#!/usr/bin/env bash
# Build the self-hosted JupyterLite site the report pages embed (Notebook tab) into
# frontend/static/jupyterlite (gitignored, ~20 MB; Pyodide itself loads from the jsdelivr CDN).
# Needs Python 3.11+ with uv or pip. Config lives next to this script (jupyter-lite.json:
# exposeAppInBrowser, so the report page can write the generated notebook and report.json
# through window.jupyterapp).
# Usage: frontend/jupyterlite/build.sh [venv dir]
set -euo pipefail
LITE=$(cd "$(dirname "$0")" && pwd)
OUT=$LITE/../static/jupyterlite
VENV=${1:-${TMPDIR:-/tmp}/jupyterlite-venv}
JUPYTERLITE_CORE=${JUPYTERLITE_CORE:-0.8.7}
PYODIDE_KERNEL=${PYODIDE_KERNEL:-0.8.6}
if [ ! -x "$VENV/bin/jupyter" ]; then
  if command -v uv >/dev/null; then
    uv venv -q "$VENV" && VIRTUAL_ENV=$VENV uv pip install -q "jupyterlite-core==$JUPYTERLITE_CORE" "jupyterlite-pyodide-kernel==$PYODIDE_KERNEL"
  else
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q "jupyterlite-core==$JUPYTERLITE_CORE" "jupyterlite-pyodide-kernel==$PYODIDE_KERNEL"
  fi
fi
cd "$LITE"
rm -rf "$OUT"
"$VENV/bin/jupyter" lite build --apps lab --no-sourcemaps --output-dir "$OUT" --lite-dir "$LITE"
# The build's own dev files are not served.
rm -f "$OUT"/rspack.config*.js "$OUT"/package.json
echo "JupyterLite built: $(du -sh "$OUT" | cut -f1)"
