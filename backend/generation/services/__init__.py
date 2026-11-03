"""Shared generation infrastructure retained from v2 (spec section 2.1).

Only provider plumbing lives here: the OpenRouter client and generation-stats fetcher, per-call telemetry,
LLM parameter helpers, sampling-preset layering and the code profile. Run content is read through
:mod:`backend.generation.runs.view`, never from these modules.
"""
