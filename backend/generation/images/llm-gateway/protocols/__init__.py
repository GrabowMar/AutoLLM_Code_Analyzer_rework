"""Front protocols of the LLM gateway (spec 5.4, 6.6).

``FRONTS`` maps a ``front_protocol`` to its module. Every module exposes ``NAME``, ``UPSTREAM_PATH``,
``PING`` (the keepalive written during upstream silence), ``route(method, path)``,
``apply_policy_native(body, policy) -> (body, policy_diff)``, ``prompt_text``, ``system_prompt``,
``requested_reasoning``, ``error(kind, message, status)``, ``context_error(est, cap)``,
``stream_error(status, payload)``, ``models_payload(names)``, ``summarize(payload)``, a
``StreamAccumulator`` and the translator seams ``to_chat`` / ``from_chat`` / ``stream_from_chat``.
``budget_rules`` is shared with the executor's local proxy and imports nothing from this package.
"""
