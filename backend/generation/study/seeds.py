"""CRN seed derivation (spec section 9.4): every condition gets the same seed per (unit, repeat).

The formula lives in ``identity.crn_seed`` (the only place identity values are computed).
"""

from backend.generation.identity import crn_seed

__all__ = ["crn_seed"]
