#!/bin/sh
# Re-lock the node tools (npm/package.json -> npm/package-lock.json).
#
# Run inside the pinned node image with network access, e.g.
#   docker run --rm -v "$PWD":/w -w /w node:22-bookworm-slim sh scripts/lock_node.sh
# --before bounds every transitive release date, like CUTOFF for the Python locks.
set -eu
BEFORE="${BEFORE:-2026-10-09T00:00:00Z}"
cd "$(dirname "$0")/../npm"
rm -rf node_modules package-lock.json
npm install --before="$BEFORE" --package-lock-only --ignore-scripts --no-audit --no-fund
