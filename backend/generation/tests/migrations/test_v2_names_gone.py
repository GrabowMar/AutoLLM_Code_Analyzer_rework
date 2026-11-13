"""The clean break (spec section 2.2): the factory shim exports none of the removed v2 factory names.

It lives under ``tests/migrations/`` with the wipe guard tests because it names the v2 factories on
purpose, and the gate grep for v2 names excludes ``/migrations/`` paths.
"""

from __future__ import annotations

from backend.generation.tests import factories as shim

V2_FACTORY_NAMES = {
    "GenerationJobFactory",
    "ExperimentFactory",
    "ExperimentConditionFactory",
    "GenerationProfileFactory",
    "AppRequirementTemplateFactory",
    "ContentBlockFactory",
}


def test_tests_factories_shim_exports_no_v2_names():
    assert V2_FACTORY_NAMES.isdisjoint(dir(shim))
    assert V2_FACTORY_NAMES.isdisjoint(shim.__all__)
