#!/usr/bin/env python3
"""Initialize a fresh checkout: copy .env templates and generate local secrets.

Idempotent — won't overwrite an existing real .env file.
"""

from __future__ import annotations

import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rand() -> str:
    """48 alphanumeric chars — matches bootstrap.sh length/charset."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(48))


def copy_if_missing(src: Path, dest: Path) -> bool:
    if dest.is_file():
        print(f"  ok exists: {dest.relative_to(ROOT)}")
        return False
    shutil.copy2(src, dest)
    print(f"  + created: {dest.relative_to(ROOT)}")
    return True


def set_secret(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    target = f"{key}=changeme"
    if target not in text:
        return
    path.write_text(text.replace(target, f"{key}={value}", 1), encoding="utf-8")


def main() -> int:
    django_example = ROOT / ".envs/.local/.django.example"
    django_dest = ROOT / ".envs/.local/.django"
    postgres_example = ROOT / ".envs/.local/.postgres.example"
    postgres_dest = ROOT / ".envs/.local/.postgres"

    print("-> Local Django env")
    if copy_if_missing(django_example, django_dest):
        set_secret(django_dest, "CELERY_FLOWER_USER", rand())
        set_secret(django_dest, "CELERY_FLOWER_PASSWORD", rand())

    print("-> Local Postgres env")
    postgres_existed = postgres_dest.is_file()
    if copy_if_missing(postgres_example, postgres_dest):
        set_secret(postgres_dest, "POSTGRES_USER", rand())
        set_secret(postgres_dest, "POSTGRES_PASSWORD", rand())

    print()
    print("Bootstrap complete.")
    if postgres_existed:
        print()
        print(
            "Note: .envs/.local/.postgres already existed. If Postgres was initialized"
        )
        print(
            "earlier with different credentials, reset the DB volume before `just up`:"
        )
        print("  docker compose -f docker-compose.local.yml down -v")
        print("  (or: just prune)")
    print()
    print("Next steps:")
    print("  1. (optional) Edit .envs/.local/.django to set OPENROUTER_API_KEY")
    print("  2. just up")
    print("  3. just manage migrate")
    print("  4. just manage createsuperuser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
