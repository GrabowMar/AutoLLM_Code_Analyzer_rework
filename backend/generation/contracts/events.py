"""SSE event payload schemas (spec sections 4.7, 6, 9.5).

Channels: ``run:<uuid>`` and ``study:<uuid>`` (they replace ``generation:`` and
``experiment:``). Every payload has a ``type`` discriminator; the realtime
publisher adds ``channel`` and ``published_at``. Payloads never carry other
tenants' model names, hosts or addresses.

* ``run:<id>``: ``status``, ``step``, ``usage``, ``trajectory_step``, ``log``, ``evaluation``.
* ``study:<id>``: ``run``, ``tick``, ``state``, ``post_run`` (ported from the
  experiment channel) plus ``spend``, ``budget_alert``, ``stopped`` and ``paused``.
"""

from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import Field
from pydantic import TypeAdapter

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts.harness import Step

__all__ = [
    "RUN_EVENT_TYPES",
    "STUDY_EVENT_TYPES",
    "BudgetAlertEvent",
    "PausedEvent",
    "RunEvaluationEvent",
    "RunEvent",
    "RunLogEvent",
    "RunStatusEvent",
    "RunStepEvent",
    "RunUsageEvent",
    "SpendEvent",
    "StoppedEvent",
    "StudyEvent",
    "StudyPostRunEvent",
    "StudyRunEvent",
    "StudyStateEvent",
    "StudyTickEvent",
    "TrajectoryStepEvent",
    "parse_run_event",
    "parse_study_event",
    "run_channel",
    "study_channel",
]


def run_channel(run_id: object) -> str:
    return f"run:{run_id}"


def study_channel(study_id: object) -> str:
    return f"study:{study_id}"


# ---------------------------------------------------------------------------
# run:<id>
# ---------------------------------------------------------------------------


class RunStatusEvent(ContractModel):
    type: Literal["status"] = "status"
    run_id: NonEmptyStr
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    phase: str = ""
    outcome: str = ""
    usable: bool | None = None
    attempt: NonNegInt = 0
    ts: str | None = None


class RunStepEvent(ContractModel):
    """Every lifecycle step publishes ``{type: "step", step, status}`` (section 6)."""

    type: Literal["step"] = "step"
    run_id: NonEmptyStr
    step: NonEmptyStr  # resolve, images, workspace, sandbox, harness, collect, record, lock, build, ...
    status: Literal["started", "ok", "failed", "skipped"]
    seconds: NonNegFloat | None = None
    detail: str | None = None


class RunUsageEvent(ContractModel):
    type: Literal["usage"] = "usage"
    run_id: NonEmptyStr
    attempt: NonNegInt
    requests_by_class: dict[str, NonNegInt] = Field(default_factory=dict)
    prompt_tokens: NonNegInt = 0
    completion_tokens: NonNegInt = 0
    reasoning_tokens: NonNegInt = 0
    cached_tokens: NonNegInt = 0
    cost_usd: NonNegFloat = 0.0
    list_cost_usd: NonNegFloat = 0.0
    inflight: NonNegInt = 0
    budget_exhausted: str | None = None


class TrajectoryStepEvent(ContractModel):
    """Throttled to 2/s by the supervisor; ``step`` is ``None`` when the adapter has no ``parse_line``."""

    type: Literal["trajectory_step"] = "trajectory_step"
    run_id: NonEmptyStr
    attempt: NonNegInt
    n_steps: NonNegInt
    step: Step | None = None


class RunLogEvent(ContractModel):
    type: Literal["log"] = "log"
    run_id: NonEmptyStr
    stream: Literal["stdout", "stderr", "system"] = "system"
    level: Literal["debug", "info", "warning", "error"] = "info"
    line: str


class RunEvaluationEvent(ContractModel):
    type: Literal["evaluation"] = "evaluation"
    run_id: NonEmptyStr
    evaluation_id: int | None = None
    status: Literal["pending", "running", "measured", "not_measured", "error"]
    stage: str | None = None
    agent_pass_rate: Fraction | None = None


RunEvent = Annotated[
    RunStatusEvent | RunStepEvent | RunUsageEvent | TrajectoryStepEvent | RunLogEvent | RunEvaluationEvent,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# study:<id>
# ---------------------------------------------------------------------------


class StudyRunEvent(ContractModel):
    """A run of the study changed state (port of the experiment ``job`` event)."""

    type: Literal["run"] = "run"
    run_id: NonEmptyStr
    cell_id: int | None = None
    condition_id: int | None = None
    unit_key: str | None = None
    repeat_index: NonNegInt | None = None
    status: str
    phase: str = ""
    outcome: str = ""
    attempt: NonNegInt = 0
    endpoint_id: int | None = None
    agent_pass_rate: Fraction | None = None
    ts: str | None = None


class StudyTickEvent(ContractModel):
    """Periodic progress summary published by the executor."""

    type: Literal["tick"] = "tick"
    counts: dict[str, NonNegInt] = Field(default_factory=dict)
    cells: dict[str, NonNegInt] = Field(default_factory=dict)  # total, done, failed_final, in_flight
    endpoints: list[dict[str, Any]] = Field(default_factory=list)
    bottleneck: str | None = None


class StudyStateEvent(ContractModel):
    type: Literal["state"] = "state"
    status: Literal["draft", "planned", "piloting", "running", "paused", "completed", "cancelled", "stopped"]
    pause_reason: str = ""


class StudyPostRunEvent(ContractModel):
    type: Literal["post_run"] = "post_run"
    id: int
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)


class SpendEvent(ContractModel):
    type: Literal["spend"] = "spend"
    spent: NonNegFloat
    spent_list: NonNegFloat
    reserved: NonNegFloat
    delta: float
    run_id: str | None = None


class BudgetAlertEvent(ContractModel):
    type: Literal["budget_alert"] = "budget_alert"
    fraction: Fraction


class StoppedEvent(ContractModel):
    type: Literal["stopped"] = "stopped"
    reason: NonEmptyStr


class PausedEvent(ContractModel):
    type: Literal["paused"] = "paused"
    reason: NonEmptyStr  # budget, quota, spend_cap, rule:<code>, manual, infra
    condition_id: int | None = None


StudyEvent = Annotated[
    StudyRunEvent
    | StudyTickEvent
    | StudyStateEvent
    | StudyPostRunEvent
    | SpendEvent
    | BudgetAlertEvent
    | StoppedEvent
    | PausedEvent,
    Field(discriminator="type"),
]

RUN_EVENT_TYPES = ("status", "step", "usage", "trajectory_step", "log", "evaluation")
STUDY_EVENT_TYPES = ("run", "tick", "state", "post_run", "spend", "budget_alert", "stopped", "paused")

_RUN_ADAPTER: TypeAdapter[Any] = TypeAdapter(RunEvent)
_STUDY_ADAPTER: TypeAdapter[Any] = TypeAdapter(StudyEvent)


def parse_run_event(payload: dict[str, Any]) -> Any:
    """Validate a ``run:<id>`` payload (ignores the publisher's ``channel``/``published_at`` keys)."""
    return _RUN_ADAPTER.validate_python(_strip_envelope(payload))


def parse_study_event(payload: dict[str, Any]) -> Any:
    """Validate a ``study:<id>`` payload (ignores the publisher's ``channel``/``published_at`` keys)."""
    return _STUDY_ADAPTER.validate_python(_strip_envelope(payload))


def _strip_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"channel", "published_at"}}
