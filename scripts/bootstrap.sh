#!/usr/bin/env bash
# Initialize a fresh checkout: copies .env templates into real files and
# generates strong random values for Postgres + Celery Flower secrets.
#
# Idempotent — won't overwrite an existing real .env file. Just prints a
# notice and moves on.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
rand() {
    # 48 alphanumeric chars from /dev/urandom — independent of openssl version.
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48
}

copy_if_missing() {
    local src="$1"
    local dest="$2"
    if [[ -f "$dest" ]]; then
        echo "  ✓ exists: $dest"
        return 1
    fi
    cp "$src" "$dest"
    echo "  + created: $dest"
    return 0
}

# Replace KEY=changeme with KEY=<random> in a file.
set_secret() {
    local file="$1"
    local key="$2"
    local value="$3"
    if grep -qE "^${key}=changeme$" "$file"; then
        # Use a delimiter unlikely to appear in random hex/alphanumerics.
        sed -i.bak -E "s|^${key}=changeme$|${key}=${value}|" "$file"
        rm -f "${file}.bak"
    fi
}

# ----------------------------------------------------------------------------
# .envs/.local/.django
# ----------------------------------------------------------------------------
echo "→ Local Django env"
if copy_if_missing ".envs/.local/.django.example" ".envs/.local/.django"; then
    set_secret ".envs/.local/.django" "CELERY_FLOWER_USER" "$(rand)"
    set_secret ".envs/.local/.django" "CELERY_FLOWER_PASSWORD" "$(rand)"
fi

# ----------------------------------------------------------------------------
# .envs/.local/.postgres
# ----------------------------------------------------------------------------
echo "→ Local Postgres env"
if copy_if_missing ".envs/.local/.postgres.example" ".envs/.local/.postgres"; then
    set_secret ".envs/.local/.postgres" "POSTGRES_USER" "$(rand)"
    set_secret ".envs/.local/.postgres" "POSTGRES_PASSWORD" "$(rand)"
fi

echo
echo "Bootstrap complete."
echo
echo "Next steps:"
echo "  1. (optional) Edit .envs/.local/.django to set OPENROUTER_API_KEY"
echo "  2. just up"
echo "  3. just manage migrate"
echo "  4. just manage createsuperuser"
