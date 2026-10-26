"""Build the pinned analyzer image on the apps daemon (or report its status).

The tag is content-addressed over ``backend/analysis/images/analyzer``; a
build is a no-op when the image for the current definition exists. After a
build the catalog's tool versions are refreshed from the image manifest.
"""

from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand

from backend.analysis.engine import sandbox


class Command(BaseCommand):
    help = "Build the analyzer image (llmlab/analyzer:<content hash>) or show its status"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--check", action="store_true", help="Only report the image status (exit 1 if not built)")

    def handle(self, *args, **options) -> None:
        if options["check"]:
            status = sandbox.image_status()
            self.stdout.write(json.dumps(status, indent=2, default=str))
            if not status.get("built"):
                sys.exit(1)
            return
        tag, image_id = sandbox.build_image(log=self.stdout.write)
        manifest = sandbox.image_manifest(image_id)
        tools = manifest.get("tools") if isinstance(manifest, dict) else None
        versions = {slug: sandbox.tool_version(manifest, slug) for slug in tools} if isinstance(tools, dict) else {}
        self.stdout.write(self.style.SUCCESS(f"{tag} ({image_id})"))
        for slug, version in sorted(versions.items()):
            self.stdout.write(f"  {slug}: {version}")
