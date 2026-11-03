"""Test support for the v3 generation app (spec section 12.2, owned by WP-01).

* ``factories``: factory_boy factories for every model in ``backend.generation.models``;
* ``content``: valid stored-JSON payloads (library content, designs, manifests, results);
* ``fake_harness``: ``FakeHarness``, a harness adapter that writes fixture files and canned calls;
* ``fake_gateway``: ``FakeGateway``, an in-process HTTP server with gateway budget semantics and
  ``state.json``;
* ``fake_sandbox``: ``FakeSandbox``, the sandbox entry script against a temporary directory (no docker);
* ``fake_eval``: ``FakeEvaluator``, canned ``EvalResult`` rows instead of the eval pipeline;
* ``golden_library``: a minimal frozen library fixture and a parent app tree.

Other WPs may add ``fake_<area>.py`` modules here for their own area only.
"""
