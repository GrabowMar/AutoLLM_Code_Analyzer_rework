#!/bin/sh
# Re-lock the per-tool Python requirements (requirements/<tool>.in -> <tool>.txt).
#
# Run inside the pinned base image with network access, e.g.
#   docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh scripts/lock_python.sh
# Every tool gets its own hash-locked requirement set (installed into its own
# venv) so tools cannot constrain each other's dependencies and mypy/pylint do
# not see another tool's packages. CUTOFF bounds every transitive release date:
# the toolchain is "what was published on or before 2026-10-08".
set -eu
CUTOFF="${CUTOFF:-2026-10-09T00:00:00Z}"
UV_VERSION="${UV_VERSION:-0.12.24}"
pip install --quiet --no-cache-dir "uv==${UV_VERSION}"
cd "$(dirname "$0")/../requirements"
for f in *.in; do
  uv pip compile --quiet "$f" -o "${f%.in}.txt" \
    --generate-hashes \
    --exclude-newer "$CUTOFF" \
    --python-version 3.12 \
    --python-platform x86_64-manylinux_2_28 \
    --custom-compile-command "scripts/lock_python.sh"
done
