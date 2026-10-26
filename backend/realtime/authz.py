"""Per-channel authorization for the SSE stream (spec §F.6).

Every requested channel must match one strict pattern and pass its owner
check; anything else (unknown prefix, malformed id, someone else's object)
is refused. Non-owners of an endpoint are mapped to its redacted
``endpoint:<id>:public`` channel transparently.

| channel                    | allowed for                                       |
|----------------------------|---------------------------------------------------|
| ``generation:<uuid>``      | the job's creator or staff                        |
| ``experiment:<uuid>``      | the experiment's creator or staff                 |
| ``endpoint:<int>``         | ``can_use_endpoint``; non-owners get ``:public``  |
| ``endpoint:<int>:public``  | ``can_use_endpoint``                              |
| ``analysis:<uuid|int>``    | the analysis run's creator or staff               |
| ``runtime:<uuid|int>``     | the container's creator, its job's owner, or staff|
| ``capacity``               | any authenticated user (aggregate payloads only)  |
"""

from __future__ import annotations

import re

from asgiref.sync import sync_to_async

MAX_CHANNELS = 20

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(rf"^{_UUID}$")
_PATTERNS: dict[str, re.Pattern[str]] = {
    "generation": re.compile(rf"^generation:({_UUID})$"),
    "experiment": re.compile(rf"^experiment:({_UUID})$"),
    "endpoint": re.compile(r"^endpoint:([1-9][0-9]{0,18})(:public)?$"),
    "analysis": re.compile(rf"^analysis:({_UUID}|[1-9][0-9]{{0,18}})$"),
    "runtime": re.compile(rf"^runtime:({_UUID}|[1-9][0-9]{{0,18}})$"),
}
AGGREGATE_CHANNELS = frozenset({"capacity"})


def _owned(model, object_id: str, user, owner_field: str = "created_by") -> bool:
    if getattr(user, "is_staff", False):
        return model.objects.filter(pk=object_id).exists()
    return model.objects.filter(pk=object_id, **{owner_field: user}).exists()


def _owner_model(prefix: str):
    """The model whose ``created_by`` owns a ``<prefix>:<id>`` channel (all of them have UUID primary keys)."""
    if prefix == "generation":
        from backend.generation.models import GenerationJob

        return GenerationJob
    if prefix == "experiment":
        from backend.generation.models import Experiment

        return Experiment
    if prefix == "analysis":
        from backend.analysis.models import AnalysisRun

        return AnalysisRun
    from backend.runtime.models import ContainerInstance

    return ContainerInstance


def _may_see(prefix: str, object_id: str, user) -> bool:
    """Whether *user* may watch the ``<prefix>:<object_id>`` object."""
    if prefix == "runtime":
        # Same visibility as the runtime API: creator, generation-job owner, or staff.
        from backend.runtime.services.container_service import visible_containers

        return visible_containers(user).filter(pk=object_id).exists()
    return _owned(_owner_model(prefix), object_id, user)


def _endpoint_channel(user, endpoint_id: str, *, public: bool) -> str | None:
    from backend.inference.access import can_use_endpoint
    from backend.inference.access import is_endpoint_owner
    from backend.inference.models import LLMEndpoint
    from backend.realtime.events import endpoint_channel

    endpoint = LLMEndpoint.objects.filter(pk=int(endpoint_id)).first()
    if endpoint is None or not can_use_endpoint(user, endpoint):
        return None
    return endpoint_channel(endpoint.pk, public=public or not is_endpoint_owner(user, endpoint))


def resolve_channel(user, channel: str) -> str | None:
    """The channel *user* receives for *channel* (``endpoint:<id>`` may become ``:public``), or None if refused.

    Synchronous (it queries the database); the async view calls it through
    ``sync_to_async``.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if channel in AGGREGATE_CHANNELS:
        return channel
    prefix = channel.split(":", 1)[0]
    pattern = _PATTERNS.get(prefix)
    match = pattern.match(channel) if pattern is not None else None
    if match is None:
        return None
    object_id = match.group(1)
    if prefix == "endpoint":
        return _endpoint_channel(user, object_id, public=bool(match.group(2)))
    # Owner-checked objects; their primary keys are UUIDs, so integer ids never match.
    if not _UUID_RE.match(object_id):
        return None
    return channel if _may_see(prefix, object_id, user) else None


def authorize_channels(user, channels: list[str]) -> list[str] | None:
    """Every channel resolved for *user*, deduplicated in order; None when any one is refused."""
    resolved: list[str] = []
    for channel in channels:
        target = resolve_channel(user, channel)
        if target is None:
            return None
        if target not in resolved:
            resolved.append(target)
    return resolved


async def can_subscribe(user, channel: str) -> bool:
    """Whether *user* may subscribe to *channel* (possibly through its ``:public`` variant)."""
    return await sync_to_async(resolve_channel)(user, channel) is not None
