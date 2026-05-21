"""Re-export shim for backwards-compatible imports.

The implementation now lives in :mod:`llm_lab.generation.services.orchestrator`.
"""

from llm_lab.generation.services.orchestrator import GenerationService

__all__ = ["GenerationService"]
