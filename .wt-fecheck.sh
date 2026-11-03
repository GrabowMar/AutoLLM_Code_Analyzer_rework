#!/usr/bin/env bash
# Usage: .wt-fecheck.sh [check|test <vitest args>|build] — serialized (flock) svelte-check / vitest / vite build
# of THIS worktree's frontend/ (FE_SRC overrides) on the shared on-disk host mirror (the 2 GB frontend
# container OOMs; /tmp is RAM-backed and nearly full).
# A copy of ~/.cache/llmlab-femirror-978f/fecheck.sh with only the rsync source changed: same mirror, same
# node_modules symlink and the same flock, so it serializes with the main checkout's checks. Every run
# re-syncs the mirror, so whatever the previous run left there does not matter.
set -uo pipefail
M=/home/ubuntu/.cache/llmlab-femirror-978f
SRC=${FE_SRC:-$(cd "$(dirname "$0")" && pwd)/frontend}
export PATH=$HOME/.local/node/bin:$PATH
exec 9>$M/fecheck.lock
flock 9
rsync -a --delete --exclude node_modules --exclude build --exclude .svelte-kit "$SRC/" $M/app/
ln -sfn $M/node_modules $M/app/node_modules
cd $M/app
case "${1:-check}" in
  check) npx svelte-kit sync >/dev/null 2>&1; NODE_OPTIONS=--max-old-space-size=2500 npx svelte-check --tsconfig ./tsconfig.json --threshold warning --output machine 2>&1 | grep -E "ERROR|WARNING|COMPLETED" ;;
  test) shift; npx vitest run "$@" 2>&1 | tail -40 ;;
  build) NODE_OPTIONS=--max-old-space-size=4096 npx vite build 2>&1 | tail -15 ;;
esac
