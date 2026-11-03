#!/usr/bin/env bash
# Run git in THIS worktree with the repo's pre-commit hooks (inside the dev image).
# usage: .wt-commit.sh <git args...>    e.g.  .wt-commit.sh add -A  ;  .wt-commit.sh commit -F msg.txt
#
# Location-independent: WT is the directory holding this script, and G is the repository's shared git dir
# (`git rev-parse --git-common-dir`; a worktree's own git dir lives under it). Both are mounted at their
# host paths, so the worktree's `.git` file and absolute gitdir links resolve inside the container.
# The venv (for pre-commit) is the one .wt-test.sh would mount.
set -euo pipefail

WT="$(cd "$(dirname "$0")" && pwd)"
G="$(git -C "$WT" rev-parse --path-format=absolute --git-common-dir)"
STATE_DIR=${WT_STATE_DIR:-/home/ubuntu/.cache/llmlab-v3-work}
IMAGE=${WT_IMAGE:-vsc-autollm_code_analyzer_rework-63da51dee73f38262592be7d4da1b4c7ef82e318f7627771c464afe6e025af66-uid}
VENV_MOUNT="$("$WT/.wt-test.sh" --venv-mount)"

exec docker run --rm -i -u 1000:1000 -e HOME=/home/dev-user \
    -v "$WT:$WT" -v "$G:$G" -v "$VENV_MOUNT" -v llmlab_devcontainer_cache:/home/dev-user/.cache \
    -v "$STATE_DIR:$STATE_DIR" \
    -w "$WT" --entrypoint bash "$IMAGE" -lc 'export PATH=/home/dev-user/.venv/bin:$PATH; git config --global --add safe.directory "*"; git -c user.name=GrabowMar -c user.email=44323258+GrabowMar@users.noreply.github.com "$@"' _ "$@"
