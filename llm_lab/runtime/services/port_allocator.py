"""Port allocation helpers for container instances."""

from __future__ import annotations

import logging

from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.models import PortAllocation

logger = logging.getLogger(__name__)


def allocate_pair() -> tuple[int, int]:
    """Allocate next free (backend_port, frontend_port) pair."""
    alloc = PortAllocation.allocate()
    return alloc.backend_port, alloc.frontend_port


def release(container: ContainerInstance) -> None:
    """Free the port allocation for a container."""
    try:
        alloc = container.port_allocation
        alloc.delete()
    except PortAllocation.DoesNotExist:
        pass
    except Exception:
        logger.exception("Failed to release ports for container %s", container.name)
