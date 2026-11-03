#!/usr/bin/env bash
# Run a command (default: pytest -q -x) against THIS worktree's code in an isolated container + test DB.
#
#   .wt-test.sh [DBSUFFIX] [--] [cmd...]   e.g. .wt-test.sh wp03 -- pytest -q backend/generation/tests/wp03_library
#                                               .wt-test.sh wp03 -- python manage.py makemigrations --check
#   .wt-test.sh --init-venv [--fresh]      (re)build the worktree venv volume from this worktree's uv.lock
#   .wt-test.sh --venv-mount               print the venv `-v` spec a test container would use
#
# Location-independent: WT is the directory holding this script, so a sub-worktree tests its own code.
# Test DB: llmlab_wt_<DBSUFFIX> on the shared postgres (one suffix per role, so runs are parallel-safe).
# Env file: $WT/.wt/wt.env (gitignored, mode 600) when present, else $STATE_DIR/wt.env.
# Venv: the volume llmlab_wt_samplegen_venv, mounted read-write, once `--init-venv` has built AND verified
# it (the marker $STATE_DIR/llmlab_wt_samplegen_venv.ready holds the sha256 of the uv.lock it was synced
# from). Until then, and while it is rebuilt, the devcontainer venv is mounted read-only (the old behaviour).
# WT_VENV=devcontainer forces the old venv; WT_VENV=worktree refuses to run without the new one.
set -euo pipefail

WT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR=${WT_STATE_DIR:-/home/ubuntu/.cache/llmlab-v3-work}
IMAGE=${WT_IMAGE:-vsc-autollm_code_analyzer_rework-63da51dee73f38262592be7d4da1b4c7ef82e318f7627771c464afe6e025af66-uid}
NETWORK=${WT_NETWORK:-llmlab_default}
VENV=/home/dev-user/.venv
VENV_VOL=llmlab_wt_samplegen_venv
DEV_VENV_VOL=llmlab_devcontainer_venv
READY="$STATE_DIR/$VENV_VOL.ready"
# Imported after every sync: the base stack plus the v3 dependencies (pyproject.toml).
VERIFY_IMPORTS="django pytest pytest_django factory tree_sitter tree_sitter_language_pack zstandard"

die() {
    echo ".wt-test.sh: $*" >&2
    exit 2
}

lock_sha() { sha256sum "$WT/uv.lock" | cut -d' ' -f1; }

venv_mount() {
    local mode=${WT_VENV:-auto}
    if [ "$mode" != devcontainer ] && [ -f "$READY" ] && docker volume inspect "$VENV_VOL" >/dev/null 2>&1; then
        if [ "$(cat "$READY")" != "$(lock_sha)" ]; then
            echo ".wt-test.sh: warning: $VENV_VOL was synced from a different uv.lock; run $0 --init-venv" >&2
        fi
        echo "$VENV_VOL:$VENV"
    elif [ "$mode" = worktree ]; then
        die "WT_VENV=worktree but $VENV_VOL is not built and verified; run $0 --init-venv"
    else
        echo "$DEV_VENV_VOL:$VENV:ro"
    fi
}

init_venv() {
    local fresh=${1:-}
    mkdir -p "$STATE_DIR"
    exec 8>"$STATE_DIR/$VENV_VOL.lock"
    flock -n 8 || die "another --init-venv is already running"
    if [ "$fresh" = --fresh ] && docker volume inspect "$VENV_VOL" >/dev/null 2>&1; then
        # Refused by docker while a test container mounts it; the verified venv then stays in use.
        docker volume rm "$VENV_VOL" >/dev/null || die "cannot remove $VENV_VOL (still mounted by a test container?)"
    fi
    # New test runs fall back to the devcontainer venv until this build is verified.
    rm -f "$READY"
    if ! docker volume inspect "$VENV_VOL" >/dev/null 2>&1; then
        echo ".wt-test.sh: creating $VENV_VOL as a copy of $DEV_VENV_VOL" >&2
        docker volume create "$VENV_VOL" >/dev/null
        # Mounted at the image's dev-user-owned $VENV, so the new volume inherits that ownership.
        if ! docker run --rm -u 1000:1000 -v "$DEV_VENV_VOL:/from:ro" -v "$VENV_VOL:$VENV" \
            --entrypoint bash "$IMAGE" -c "cp -a /from/. $VENV/"; then
            docker volume rm "$VENV_VOL" >/dev/null 2>&1 || true
            die "copying $DEV_VENV_VOL failed"
        fi
    fi
    local sha
    sha=$(lock_sha)
    echo ".wt-test.sh: uv sync --frozen (uv.lock $sha) into $VENV_VOL" >&2
    # The worktree is mounted read-only: --frozen installs exactly what uv.lock pins and never rewrites it.
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$WT:/app:ro" -v "$VENV_VOL:$VENV" -w /app \
        -e UV_PROJECT_ENVIRONMENT="$VENV" -e UV_CACHE_DIR=/tmp/uv-cache -e UV_LINK_MODE=copy \
        -e UV_PYTHON_DOWNLOADS=0 -e UV_COMPILE_BYTECODE=1 \
        --entrypoint bash "$IMAGE" -c "
            set -e
            uv sync --frozen --group dev
            $VENV/bin/python - $VERIFY_IMPORTS <<'EOF'
import importlib
import sys

for name in sys.argv[1:]:
    importlib.import_module(name)
import tree_sitter_language_pack as pack

for lang in ('python', 'javascript', 'typescript', 'tsx', 'go', 'java', 'html', 'css', 'vue', 'svelte'):
    pack.get_parser(lang)  # the grammars ship inside the wheel: no download, no network
print('verified imports:', ', '.join(sys.argv[1:]), '+ tree-sitter grammars')
EOF
        "
    echo "$sha" >"$READY"
    echo ".wt-test.sh: $VENV_VOL is ready; test runs now mount it read-write" >&2
}

case "${1:-}" in
    --init-venv)
        init_venv "${2:-}"
        exit 0
        ;;
    --venv-mount)
        venv_mount
        exit 0
        ;;
esac

SUF=${1:-a}
if [ $# -gt 0 ]; then shift; fi
if [ "${1:-}" = "--" ]; then shift; fi
[[ "$SUF" =~ ^[a-z0-9_]+$ ]] || die "DBSUFFIX must match [a-z0-9_]+ (got '$SUF'); usage: $0 [DBSUFFIX] -- cmd..."
if [ $# -eq 0 ]; then set -- pytest -q -x; fi

ENV_FILE="$WT/.wt/wt.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$STATE_DIR/wt.env"
[ -f "$ENV_FILE" ] || die "no env file ($WT/.wt/wt.env or $STATE_DIR/wt.env)"
MOUNT=$(venv_mount)

exec docker run --rm -i --network "$NETWORK" --env-file "$ENV_FILE" -e POSTGRES_DB="llmlab_wt_$SUF" \
    -e DJANGO_SETTINGS_MODULE=config.settings.test \
    -v "$WT:/app" -v "$MOUNT" \
    -e VIRTUAL_ENV="$VENV" -e PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin" \
    -w /app -u "$(id -u):$(id -g)" -e HOME=/tmp "$IMAGE" /entrypoint "$@"
