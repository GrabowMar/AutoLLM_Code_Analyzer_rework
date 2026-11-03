"""``manage.py build_starters``: lock, check and seed the starters of ``data/starters/compose.yaml``.

    build_starters --lock [--all] [--docker-host URL]   (re)generate stale lockfiles inside the pinned base images
    build_starters --check                              compose every phase-A starter in memory and apply the rules
    build_starters [--with-stacks] [--stack S ...]      store new Starter versions (idempotent; --dry-run)

Locks run in containers on ``settings.STARTERS_DOCKER_HOST`` (default: the apps daemon, apps-dind), with files
copied in and out through the Docker API. ``--check`` needs neither Docker nor the database; it exits 1 when a
starter cannot be composed (stale or missing lock, missing fragment) or fails the starter rules.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.build import locks
from backend.generation.build import seeding
from backend.generation.build import starters


class Command(BaseCommand):
    help = "Lock, check and seed the composed starters (spec section 11.4)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--lock", action="store_true", help="(re)generate lockfiles in the base images")
        parser.add_argument("--all", action="store_true", help="with --lock: regenerate fresh locks too")
        parser.add_argument("--docker-host", default="", help="with --lock: Docker daemon URL (overrides settings)")
        parser.add_argument("--check", action="store_true", help="compose in memory and apply the starter rules")
        parser.add_argument("--with-stacks", action="store_true", help="also seed stack profiles and fragments")
        parser.add_argument("--stack", action="append", default=None, help="limit to these stack slugs")
        parser.add_argument("--dry-run", action="store_true", help="report what seeding would create")
        parser.add_argument("--json", action="store_true", help="print the report as JSON")

    def handle(self, *args: Any, **options: Any) -> None:
        stacks = options["stack"]
        if options["lock"]:
            report: list[dict[str, Any]] = self._lock(stacks, rebuild=options["all"], host=options["docker_host"])
        elif options["check"]:
            report = self._check(stacks)
        else:
            if options["with_stacks"]:
                records = seeding.seed_stack_library(stacks=stacks, dry_run=options["dry_run"])
            else:
                records = starters.seed_starters(stacks=stacks, dry_run=options["dry_run"])
            report = [record.__dict__ for record in records]
        self._print(report, as_json=options["json"])
        failed = [row for row in report if row.get("status") == "error"]
        if failed:
            msg = f"{len(failed)} starter(s) failed: " + ", ".join(
                str(row.get("slug") or row.get("key")) for row in failed
            )
            raise CommandError(msg)

    def _entries(self, stacks: list[str] | None) -> list[starters.ComposeEntry]:
        return [e for e in starters.compose_plan() if stacks is None or e.stack in stacks]

    def _lock(self, stacks: list[str] | None, *, rebuild: bool, host: str) -> list[dict[str, Any]]:
        fragments: set[str] = set()
        for entry in self._entries(stacks):
            if entry.phase != "A" or entry.level == "bare":
                continue
            content = starters.load_stack_yaml(entry.stack)["content"]
            for fragment in starters.fragments_for(content, entry.level):
                if fragment:
                    fragments.add(fragment)
        client = None
        if host:
            import docker

            client = docker.DockerClient(base_url=host)
        runner = locks.DockerLockRunner(client)
        return locks.generate_locks(sorted(fragments), runner, only_stale=not rebuild)

    def _check(self, stacks: list[str] | None) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        for entry in self._entries(stacks):
            slug = starters.starter_slug(entry.stack, entry.level)
            if entry.phase != "A":
                report.append({"slug": slug, "status": "skipped", "reason": f"phase {entry.phase}"})
                continue
            try:
                stack = starters.load_stack_yaml(entry.stack)
                composed = starters.compose_tree(
                    entry.stack,
                    stack["content"],
                    entry.level,
                    stack_ref=f"{entry.stack}@0#{'0' * 12}",
                )
            except (starters.StarterCompositionError, locks.LockError, ValueError) as exc:
                report.append({"slug": slug, "status": "error", "reason": str(exc)})
                continue
            row = {"slug": slug, "status": "ok" if composed.passed else "error", "tree_hash": composed.tree_hash}
            if not composed.passed:
                row["reason"] = "; ".join(issue.message for issue in composed.issues)
            report.append(row)
        return report

    def _print(self, report: list[dict[str, Any]], *, as_json: bool) -> None:
        if as_json:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
            return
        for row in report:
            name = row.get("slug") or row.get("key")
            extra = row.get("reason") or row.get("tree_hash") or row.get("lock") or ""
            version = f" v{row['version']}" if row.get("version") else ""
            self.stdout.write(f"{row.get('status', '?'):9} {name}{version} {extra}".rstrip())
