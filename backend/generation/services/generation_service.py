"""Re-export shim for backwards-compatible imports.

The implementation now lives in :mod:`backend.generation.services.orchestrator`.
"""

from backend.generation.services.orchestrator import GenerationService

__all__ = ["GenerationService"]
