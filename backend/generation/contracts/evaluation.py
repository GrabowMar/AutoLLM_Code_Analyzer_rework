"""Evaluation contracts (spec sections 6.10, 8.1, 8.3, 8.7).

The tester (``images/eval-tester``) reads an ``AcceptancePlan`` and writes an
``EvalResult``; the adherence checker writes an ``AdherenceRecord``; the lock
step writes a ``ResolvedLock``. ``CheckSpec``/``ProbeSpec`` live in
``contracts.library`` (they are part of ``TaskSpecContent``) and are
re-exported here.

Validity rules carried by these shapes: a tester-side error makes the whole
result ``measured=false`` (never pass rate 0); skipped probes carry a reason
and never count as ``not_exploited``; nothing here holds a computed severity.
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import Field

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import RelPath
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.library import Actor
from backend.generation.contracts.library import AuthFlow
from backend.generation.contracts.library import CheckCategory
from backend.generation.contracts.library import CheckSpec
from backend.generation.contracts.library import CompiledRequest
from backend.generation.contracts.library import ProbeKind
from backend.generation.contracts.library import ProbeSpec

__all__ = [
    "AcceptancePlan",
    "Actor",
    "AdherenceDetected",
    "AdherenceRecord",
    "AuthDriverConfig",
    "AuthMode",
    "CategoryCount",
    "CheckResult",
    "CheckSpec",
    "CompiledCheck",
    "CompiledProbe",
    "CompiledRequest",
    "Detected",
    "Deviation",
    "EvalMetrics",
    "EvalResult",
    "EvalStages",
    "LockedPackage",
    "PackageRecord",
    "ProbeKind",
    "ProbeResult",
    "ProbeSpec",
    "ResolvedLock",
    "StageRecord",
    "StyleMetrics",
]

AuthMode = Literal["contract_bearer", "cookie_session", "spec_defined"]


# ---------------------------------------------------------------------------
# Tester input (8.1)
# ---------------------------------------------------------------------------


class AuthDriverConfig(ContractModel):
    """How the tester authenticates each actor (section 8.4)."""

    mode: AuthMode
    register_path: str | None = "/api/auth/register"
    login_path: str = "/api/auth/login"
    logout_path: str | None = "/api/auth/logout"
    credential: Literal["bearer", "cookie"] = "bearer"
    token_json_path: str | None = "token"  # noqa: S105 - a JSON path, not a secret
    csrf: Literal["none", "double_submit_cookie", "header_from_endpoint"] = "none"
    csrf_cookie: str = "csrf_token"
    csrf_header: str = "X-CSRF-Token"
    admin_username: str = "admin"
    auth_flow: AuthFlow | None = None  # spec_defined only


class CompiledCheck(CheckSpec):
    source: Literal["compiler", "acceptance"] = "compiler"


class CompiledProbe(ProbeSpec):
    requires_checks: list[str] = Field(default_factory=list)  # resolved: never None in a compiled plan
    positive_control: CompiledRequest | None = None


class AcceptancePlan(ContractModel):
    """``/in/plan.json`` for the eval-tester, compiled by ``evaluation/acceptance.py``."""

    compiler_version: PosInt
    base_url: NonEmptyStr
    auth: AuthDriverConfig
    actors: list[Actor]
    checks: list[CompiledCheck]
    probes: list[CompiledProbe] = Field(default_factory=list)
    only: list[str] | None = None


# ---------------------------------------------------------------------------
# Tester output (8.1)
# ---------------------------------------------------------------------------


class CheckResult(ContractModel):
    id: NonEmptyStr
    category: CheckCategory
    endpoint: str | None = None
    status: Literal["pass", "fail", "error", "skipped"]
    expected: Any = None
    observed: Any = None
    ms: NonNegInt | None = None
    note: str | None = None
    skipped_due_to: list[str] = Field(default_factory=list)  # failed depends_on checks (status skipped)


class ProbeResult(ContractModel):
    id: NonEmptyStr
    cwe: NonEmptyStr
    kind: ProbeKind
    endpoint: str | None = None
    status: Literal["exploited", "not_exploited", "error", "skipped"]
    evidence: str | None = None
    skip_reason: Literal["precondition_failed", "positive_control_failed", "disabled", "level"] | None = None


class CategoryCount(ContractModel):
    n: NonNegInt
    passed: NonNegInt


class EvalMetrics(ContractModel):
    infra_ok: bool
    agent_checks: NonNegInt
    agent_passed: NonNegInt
    agent_pass_rate: Fraction | None = None
    by_category: dict[str, CategoryCount] = Field(default_factory=dict)
    baseline_pass_rate: Fraction | None = None
    over_baseline: float | None = None
    probes_tested: NonNegInt = 0
    probes_exploited: NonNegInt = 0
    probes_skipped: NonNegInt = 0
    # all functional pass AND no exploited probe among tested probes (None if probes off or none tested)
    sec_pass: bool | None = None
    slot_pass_rate: Fraction | None = None
    regression_pass_rate: Fraction | None = None
    common_pass_rate: Fraction | None = None
    stated_pass_rate: Fraction | None = None
    unstated_pass_rate: Fraction | None = None


class EvalResult(ContractModel):
    """``/out/result.json`` and ``RunEvaluation.result``."""

    tester_version: PosInt
    compiler_version: PosInt
    auth_mode: AuthMode
    checks: list[CheckResult] = Field(default_factory=list)
    probes: list[ProbeResult] = Field(default_factory=list)
    metrics: EvalMetrics
    measured: bool
    not_measured_reason: str | None = None
    flaky_checks: list[str] = Field(default_factory=list)  # excluded from every pass rate, listed here


# ---------------------------------------------------------------------------
# RunEvaluation.stages
# ---------------------------------------------------------------------------


class StageRecord(ContractModel):
    status: Literal["pending", "running", "ok", "fail", "error", "skipped", "not_measured"]
    seconds: NonNegFloat | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class EvalStages(ContractModel):
    """``RunEvaluation.stages``: one record per stage; ``None`` = the stage was not planned at this level."""

    lock: StageRecord | None = None
    build: StageRecord | None = None
    start: StageRecord | None = None
    tester: StageRecord | None = None
    adherence: StageRecord | None = None
    probes: StageRecord | None = None


# ---------------------------------------------------------------------------
# Adherence checker (8.7)
# ---------------------------------------------------------------------------


class Detected(ContractModel):
    value: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]


class Deviation(ContractModel):
    dimension: Literal["backend", "frontend", "database", "styling", "auth", "structure"]
    expected: str
    observed: str
    evidence: list[str] = Field(default_factory=list)


class PackageRecord(ContractModel):
    ecosystem: Literal["pypi", "npm", "go", "maven"]
    name: NonEmptyStr
    version: str | None = None
    pinned: bool
    declared_in: NonEmptyStr
    exists: bool | None = None  # registry lookup done by suite.py (network), not the checker


class AdherenceDetected(ContractModel):
    backend: Detected
    frontend: Detected
    database: Detected
    styling: Detected
    auth: Detected


class StyleMetrics(ContractModel):
    tailwind_class_share: Fraction | None = None
    css_files: NonNegInt = 0
    css_modules: NonNegInt = 0
    component_library_imports: dict[str, NonNegInt] = Field(default_factory=dict)


class AdherenceRecord(ContractModel):
    """``RunEvaluation.adherence``. Adherence never filters: it is a column and a note."""

    checker_version: PosInt
    status: Literal["compliant", "partial", "violated", "not_measured"]
    detected: AdherenceDetected | None = None  # None only when status=not_measured
    deviations: list[Deviation] = Field(default_factory=list)
    packages: list[PackageRecord] = Field(default_factory=list)
    hallucinated: list[str] = Field(default_factory=list)
    unpinned: list[str] = Field(default_factory=list)
    style_metrics: StyleMetrics = Field(default_factory=StyleMetrics)
    sbom_blob: Sha256 | None = None


# ---------------------------------------------------------------------------
# Platform lock (6.10)
# ---------------------------------------------------------------------------


class LockedPackage(ContractModel):
    name: NonEmptyStr
    version: NonEmptyStr
    hash: str | None = None


class ResolvedLock(ContractModel):
    """``GenerationRun.resolved_lock_blob`` payload; every build and re-evaluation installs under it."""

    ecosystems: dict[Literal["pip", "npm", "go", "maven"], list[LockedPackage]] = Field(default_factory=dict)
    files: dict[RelPath, Sha256] = Field(default_factory=dict)  # lock file path -> blob sha
    resolved_at: NonEmptyStr
