"""Port allocation helpers for container instances."""

from __future__ import annotations

import logging

from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.models import PortAllocation

logger = logging.getLogger(__name__)


def allocate() -> int:
    """Allocate the next free app port and return it."""
    alloc = PortAllocation.allocate()
    return alloc.app_port


def release(container: ContainerInstance) -> None:
    """Free the port allocation for a container."""
    try:
        alloc = container.port_allocation
        alloc.delete()
    except PortAllocation.DoesNotExist:
        pass
    except Exception:
        logger.exception("Failed to release port for container %s", container.name)
