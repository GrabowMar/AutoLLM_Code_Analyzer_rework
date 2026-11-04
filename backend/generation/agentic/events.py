"""``run:<id>`` SSE events of the generation stage (spec sections 6 and 9.5; payloads in ``contracts.events``).

* :class:`StepRecorder`: every lifecycle step publishes ``{type: "step", step, status}`` and is kept with its
  timing in ``RunResult.steps`` (``resolve``, ``images``, ``workspace``, ``gateway``, ``sandbox``, ``record``,
  ``collect``);
* :class:`LiveUsage`: the supervisor's ``state.json`` polls become ``usage`` events (and the ledger's live rows
  through ``budget.live.record_live`` once WP-11 provides it);
* :class:`TrajectoryStream`: harness stdout lines become ``trajectory_step`` events, throttled to 2 per second,
  parsed by the adapter's optional ``parse_line`` (otherwise a step count only).

Publishing never raises: the realtime publisher validates and logs malformed events.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.events import RunLogEvent
from backend.generation.contracts.events import RunStepEvent
from backend.generation.contracts.events import RunUsageEvent
from backend.generation.contracts.events import TrajectoryStepEvent

if TYPE_CHECKING:
    from backend.generation.contracts.harness import Step

logger = logging.getLogger(__name__)

__all__ = ["LiveUsage", "StepRecorder", "TrajectoryStream", "publish"]

TRAJECTORY_MIN_INTERVAL_S: Final = 0.5  # 2 events per second


def publish(run_id: Any, payload: dict[str, Any]) -> None:
    """Publish one ``run:<id>`` event (best effort)."""
    try:
        from backend.realtime.events import publish_run_event

        publish_run_event(run_id, payload)
    except Exception:  # noqa: BLE001 - events never break a run
        logger.debug("publishing a run event of %s failed", run_id, exc_info=True)


class StepRecorder:
    """Times the lifecycle steps of one attempt and publishes them."""

    def __init__(self, run_id: Any, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.run_id = str(run_id)
        self.clock = clock
        self.steps: list[dict[str, Any]] = []
        self._open: dict[str, float] = {}

    def start(self, step: str) -> None:
        self._open[step] = self.clock()
        publish(self.run_id, RunStepEvent(run_id=self.run_id, step=step, status="started").model_dump(mode="json"))

    def end(self, step: str, status: str = "ok", detail: str | None = None) -> None:
        started = self._open.pop(step, None)
        seconds = round(self.clock() - started, 3) if started is not None else None
        record: dict[str, Any] = {"step": step, "status": status, "seconds": seconds}
        if detail:
            record["detail"] = detail[:500]
        self.steps.append(record)
        event = RunStepEvent(run_id=self.run_id, step=step, status=status, seconds=seconds, detail=detail)  # type: ignore[arg-type]
        publish(self.run_id, event.model_dump(mode="json"))

    def skip(self, step: str, detail: str | None = None) -> None:
        self._open[step] = self.clock()
        self.end(step, "skipped", detail)

    def fail_open(self, detail: str | None = None) -> None:
        """Mark every still-open step failed (an exception left them)."""
        for step in list(self._open):
            self.end(step, "failed", detail)

    def log(self, line: str, *, level: str = "info") -> None:
        event = RunLogEvent(run_id=self.run_id, stream="system", level=level, line=line[:2000])  # type: ignore[arg-type]
        publish(self.run_id, event.model_dump(mode="json"))


class LiveUsage:
    """Turns the gateway's ``state.json`` polls into ``usage`` events and live ledger rows."""

    def __init__(self, run: Any, attempt: int) -> None:
        self.run = run
        self.attempt = attempt
        self.seq = 0

    def __call__(self, state: dict[str, Any]) -> None:
        self.seq += 1
        requests = state.get("requests_by_class") or {}
        event = RunUsageEvent(
            run_id=str(self.run.pk),
            attempt=self.attempt,
            requests_by_class={str(k): int(v) for k, v in requests.items() if isinstance(v, int)},
            prompt_tokens=int(state.get("prompt_tokens") or 0),
            completion_tokens=int(state.get("completion_tokens") or 0),
            reasoning_tokens=int(state.get("reasoning_tokens") or 0),
            cached_tokens=int(state.get("cached_tokens") or 0),
            cost_usd=float(state.get("cost") or 0.0),
            list_cost_usd=float(state.get("list_cost") or 0.0),
            inflight=int(state.get("inflight") or 0),
            budget_exhausted=state.get("budget_exhausted") or None,
        )
        publish(self.run.pk, event.model_dump(mode="json"))
        try:
            from backend.generation.budget import live

            record = getattr(live, "record_live", None)
            if record is not None:
                record(self.run, attempt=self.attempt, state=state, seq=self.seq)
        except NotImplementedError:
            pass
        except Exception:
            logger.exception("live spend of run %s failed", self.run.pk)


class TrajectoryStream:
    """Harness stdout -> throttled ``trajectory_step`` events (section 9.5)."""

    def __init__(
        self,
        run_id: Any,
        attempt: int,
        parse_line: Callable[[str], Any] | None,  # -> Step | list[Step] | None
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_id = str(run_id)
        self.attempt = attempt
        self.parse_line = parse_line
        self.clock = clock
        self.n_steps = 0
        self._last = -1e9
        self.published = 0

    def __call__(self, line: str) -> None:
        step: Step | None = None
        if self.parse_line is not None:
            try:
                parsed = self.parse_line(line)
            except Exception:  # noqa: BLE001 - a parser bug must not stop the stream
                parsed = None
            steps = list(parsed) if isinstance(parsed, (list, tuple)) else ([parsed] if parsed is not None else [])
            if not steps:
                return
            self.n_steps += len(steps) - 1
            step = steps[-1]
        elif not line.strip():
            return
        self.n_steps += 1
        now = self.clock()
        if now - self._last < TRAJECTORY_MIN_INTERVAL_S:
            return
        self._last = now
        self.published += 1
        event = TrajectoryStepEvent(run_id=self.run_id, attempt=self.attempt, n_steps=self.n_steps, step=step)
        publish(self.run_id, event.model_dump(mode="json"))
