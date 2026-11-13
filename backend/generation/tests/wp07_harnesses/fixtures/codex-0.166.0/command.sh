#!/bin/sh
cd /workspace || exit 70
exec codex exec --json --skip-git-repo-check --ephemeral --ignore-rules --strict-config --color never --sandbox danger-full-access -
