"""Informational static checks of a collected sample (spec section 6.8): they go to ``result.static`` and
never gate the outcome.

* ``python``: the AST compile check (``collector.python_static_check``, in-process);
* ``frontend``: esbuild over the model-scope JS/JSX/TS/TSX files in the ``frontend-validator`` image
  (``images/frontend-validator``), run on the apps daemon with no network and all capabilities dropped.

A check that could not run reports ``{"status": "not_measured", "reason": ...}``, never a clean result.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.conf import settings

from backend.generation.agentic.workspace import SeedFile
from backend.generation.agentic.workspace import make_tar

if TYPE_CHECKING:
    from backend.generation.agentic.collector import FileRow

logger = logging.getLogger(__name__)

__all__ = ["FRONTEND_SUFFIXES", "frontend_files", "frontend_static_check", "parse_report"]

FRONTEND_SUFFIXES: Final = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_TIMEOUT_S: Final = 120


def frontend_files(rows: list[FileRow]) -> dict[str, bytes]:
    """The model-scope frontend sources the validator parses."""
    return {
        row.path: row.data
        for row in rows
        if row.data is not None
        and row.origin in {"authored", "modified", "slot"}
        and not row.excluded
        and row.path.endswith(FRONTEND_SUFFIXES)
    }


def parse_report(output: bytes | str) -> dict[str, Any]:
    """The validator's JSON report as a ``result.static.frontend`` entry."""
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    line = next((ln for ln in reversed(text.strip().splitlines()) if ln.startswith("{")), "")
    try:
        report = json.loads(line)
    except ValueError:
        return {"status": "not_measured", "reason": "unreadable validator output"}
    errors = list(report.get("errors") or [])
    return {
        "status": "errors" if errors else "ok",
        "files": int(report.get("files") or 0),
        "errors": errors[:50],
        "validator": report.get("validator"),
    }


def frontend_static_check(rows: list[FileRow], *, client: Any = None) -> dict[str, Any]:
    """Run the frontend validator over the sample's frontend files (``not_measured`` when it cannot run)."""
    files = frontend_files(rows)
    if not files:
        return {"status": "not_applicable", "files": 0}
    if not getattr(settings, "GENERATION_FRONTEND_VALIDATOR", True):
        return {"status": "not_measured", "reason": "disabled", "files": len(files)}
    from backend.generation.agentic.images import FRONTEND_VALIDATOR_IMAGE
    from backend.generation.agentic.images import ImageBuildError
    from backend.generation.agentic.images import ensure_image

    container = None
    try:
        if client is None:
            from backend.runtime.services import docker_manager

            client = docker_manager.new_client()
        tag, _ = ensure_image(client, FRONTEND_VALIDATOR_IMAGE)
        container = client.containers.create(
            tag,
            network_mode="none",
            mem_limit="512m",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        container.put_archive("/work", make_tar({path: SeedFile(data) for path, data in files.items()}))
        container.start()
        result = container.wait(timeout=_TIMEOUT_S)
        output = container.logs(stdout=True, stderr=False)
        if int((result or {}).get("StatusCode", 1)) != 0:
            return {"status": "not_measured", "reason": "validator exited with an error", "files": len(files)}
        return parse_report(output)
    except ImageBuildError as exc:
        return {"status": "not_measured", "reason": f"validator image: {exc}"[:300], "files": len(files)}
    except Exception as exc:  # noqa: BLE001 - informational: never fail a run over a static check
        logger.warning("frontend validator failed: %s", exc)
        return {"status": "not_measured", "reason": f"validator unavailable: {exc}"[:300], "files": len(files)}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001
                logger.debug("removing the validator container failed", exc_info=True)
