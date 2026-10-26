"""/api/integrations/ — third-party integrations.

Each integration owns a sub-router module; this one only mounts them.
"""

from __future__ import annotations

from ninja import Router

router = Router(tags=["integrations"])

from backend.integrations.api import github  # noqa: E402
from backend.integrations.api import mcp  # noqa: E402
from backend.integrations.api import providers  # noqa: E402
from backend.integrations.api import webhooks  # noqa: E402

router.add_router("/providers/", providers.router)
router.add_router("/webhooks/", webhooks.router)
router.add_router("/github/", github.router)
router.add_router("/mcp/", mcp.router)
