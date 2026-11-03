"""Every ``TextChoices`` enum of the v3 generation models (spec section 3).

Enums that mirror a contract enum (``Outcome``, ``Termination``, ``ExclusionReason``, run kinds and
protocols) are spelled out here so migrations stay readable; ``tests/wp01_foundation/test_models.py``
asserts that their values equal the contract's.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Library (section 3.3)
# ---------------------------------------------------------------------------


class Visibility(models.TextChoices):
    PRIVATE = "private", _("Private")
    SHARED = "shared", _("Shared")
    SYSTEM = "system", _("System")


class AuthMode(models.TextChoices):
    """Section 3.5: a closed enum; its contract text lives in ``auth-contract-<mode>`` fragments."""

    CONTRACT_BEARER = "contract_bearer", _("Contract (bearer token)")
    COOKIE_SESSION = "cookie_session", _("Cookie session")
    SPEC_DEFINED = "spec_defined", _("Defined by the task spec")


class StarterLevel(models.TextChoices):
    BARE = "bare", _("Bare")
    MINIMAL = "minimal", _("Minimal")
    FULL = "full", _("Full")


class SecurityHint(models.TextChoices):
    NONE = "none", _("None")
    GENERIC = "generic", _("Generic")
    CWE_SPECIFIC = "cwe_specific", _("CWE-specific")


class PromptRole(models.TextChoices):
    BRIEF = "brief", _("Brief")
    SYSTEM = "system", _("System")
    USER = "user", _("User")
    SLOT_SINGLE_SHOT_SYSTEM = "slot_single_shot_system", _("Slot single-shot system")
    SLOT_SINGLE_SHOT_USER = "slot_single_shot_user", _("Slot single-shot user")
    SLOT_AGENT_BRIEF = "slot_agent_brief", _("Slot agent brief")
    FRAGMENT = "fragment", _("Fragment")


class Mode(models.TextChoices):
    """Study mode and PromptSet mode."""

    APP = "app", _("App")
    SLOT = "slot", _("Slot")


class PolicyTier(models.TextChoices):
    FRUGAL = "frugal", _("Frugal")
    STANDARD = "standard", _("Standard")
    GENEROUS = "generous", _("Generous")
    CUSTOM = "custom", _("Custom")


class DetectorEngine(models.TextChoices):
    SEMGREP = "semgrep", _("Semgrep")
    TS_QUERY = "ts_query", _("Tree-sitter query")
    REGEX = "regex", _("Regex")
    PROBE = "probe", _("Probe")
    MANUAL = "manual", _("Manual coding")


class ProseVariant(models.TextChoices):
    MINIMAL = "minimal", _("Minimal")
    STANDARD = "standard", _("Standard")
    EXHAUSTIVE = "exhaustive", _("Exhaustive")


# ---------------------------------------------------------------------------
# Study (section 3.6)
# ---------------------------------------------------------------------------


class StudyKind(models.TextChoices):
    EXPLORATORY = "exploratory", _("Exploratory")
    CONFIRMATORY = "confirmatory", _("Confirmatory")


class StudyStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PLANNED = "planned", _("Planned")
    PILOTING = "piloting", _("Piloting")
    RUNNING = "running", _("Running")
    PAUSED = "paused", _("Paused")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    STOPPED = "stopped", _("Stopped")


class Scheduling(models.TextChoices):
    AUTO = "auto", _("Auto")
    INTERLEAVED = "interleaved", _("Interleaved")
    BLOCKED = "blocked", _("Blocked")


class BudgetBasis(models.TextChoices):
    USD = "usd", _("USD")
    TOKENS = "tokens", _("Tokens")


class CapBasis(models.TextChoices):
    BILLED = "billed", _("Billed")
    LIST = "list", _("List price")


class OnBudgetExhausted(models.TextChoices):
    PAUSE = "pause", _("Pause")
    STOP_BALANCED = "stop_balanced", _("Stop (balanced)")
    STOP = "stop", _("Stop")


class FactorRole(models.TextChoices):
    CROSSED = "crossed", _("Crossed")
    FIXED = "fixed", _("Fixed")
    FRACTION = "fraction", _("Fraction")


class PlanStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    LAUNCHED = "launched", _("Launched")
    SUPERSEDED = "superseded", _("Superseded")


class Stage(models.TextChoices):
    PILOT = "pilot", _("Pilot")
    MAIN = "main", _("Main")


class CellState(models.TextChoices):
    PLANNED = "planned", _("Planned")
    QUEUED = "queued", _("Queued")
    RUNNING = "running", _("Running")
    DONE = "done", _("Done")
    INFRA_PENDING = "infra_pending", _("Infra pending")
    EXCLUDED = "excluded", _("Excluded")


class CellExclusionReason(models.TextChoices):
    CELL_BUDGET = "cell_budget", _("Cell budget")
    STOPPED_BY_RULE = "stopped_by_rule", _("Stopped by rule")
    BUDGET_STOP = "budget_stop", _("Budget stop")
    CANCELLED = "cancelled", _("Cancelled")
    CONFIG_INVALID = "config_invalid", _("Configuration invalid")


class OverrideOwnerKind(models.TextChoices):
    PROMPT = "prompt", _("Prompt template")
    PROMPT_SET = "prompt_set", _("Prompt set")
    STACK = "stack", _("Stack profile")
    STYLE = "style", _("Style")
    GUIDANCE = "guidance", _("Guidance")
    AUTH_FRAGMENT = "auth_fragment", _("Auth fragment")


class ScheduleAction(models.TextChoices):
    TOP_UP = "top_up", _("Top up")
    ADD_REPLICATES = "add_replicates", _("Add replicates")
    CLONE_AND_RUN = "clone_and_run", _("Clone and run")


class PostRunKind(models.TextChoices):
    # The ported v2 post-run: analysis of the study's runs, then a report.
    ANALYSIS = "analysis", _("Analysis and report")
    PATTERNS = "patterns", _("Pattern analysis")


class PostRunTrigger(models.TextChoices):
    COMPLETED = "completed", _("Completed")
    MANUAL = "manual", _("Manual")
    SCHEDULE = "schedule", _("Schedule")


class WorkStatus(models.TextChoices):
    """Lifecycle of leased background work (post-runs, pattern runs)."""

    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


# ---------------------------------------------------------------------------
# Runs (section 3.7)
# ---------------------------------------------------------------------------


class RunKind(models.TextChoices):
    APP_AGENTIC = "app_agentic", _("App (agentic)")
    SLOT_SINGLE_SHOT = "slot_single_shot", _("Slot (single shot)")
    SLOT_AGENT = "slot_agent", _("Slot (agent)")
    REFERENCE = "reference", _("Reference")


class Protocol(models.TextChoices):
    APP_1 = "app@1", "app@1"
    SLOT_1 = "slot@1", "slot@1"
    REF_1 = "ref@1", "ref@1"


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class RunPhase(models.TextChoices):
    # PENDING: "". RUNNING: running | eval_queued | evaluating.
    NONE = "", _("None")
    RUNNING = "running", _("Running")
    EVAL_QUEUED = "eval_queued", _("Waiting for evaluation")
    EVALUATING = "evaluating", _("Evaluating")


class Lane(models.TextChoices):
    SANDBOX = "sandbox", _("Sandbox")
    LLM = "llm", _("LLM")


class Outcome(models.TextChoices):
    """Section 6.12 (``contracts.outcome.Outcome``)."""

    OUTPUT_INVALID = "output_invalid", _("Output invalid")
    BUILD_FAIL = "build_fail", _("Build failed")
    START_FAIL = "start_fail", _("Start failed")
    TESTED = "tested", _("Tested")
    INFRA_ERROR = "infra_error", _("Infrastructure error")
    EXCLUDED = "excluded", _("Excluded")


class Termination(models.TextChoices):
    """Section 5.1 (``contracts.outcome.Termination``)."""

    COMPLETED = "completed", _("Completed")
    WALL_CLOCK = "wall_clock", _("Wall clock")
    IDLE = "idle_timeout", _("Idle timeout")
    BUDGET = "budget_exhausted", _("Budget exhausted")
    HARNESS_ERROR = "harness_error", _("Harness error")
    HARNESS_CRASH = "harness_crash", _("Harness crash")
    LLM_ERROR = "llm_error", _("LLM error")
    INFRA = "infra_interrupted", _("Infrastructure interrupted")
    CANCELLED = "cancelled", _("Cancelled")
    MAX_TURNS = "max_turns", _("Max turns")
    OOM = "oom_killed", _("Out of memory")


class ExclusionReason(models.TextChoices):
    """``outcome_reason`` of an ``excluded`` run (``contracts.outcome.ExclusionReason``)."""

    CELL_BUDGET = "cell_budget", _("Cell budget")
    BUDGET_STOP = "budget_stop", _("Budget stop")
    CANCELLED = "cancelled", _("Cancelled")
    STOPPED_BY_RULE = "stopped_by_rule", _("Stopped by rule")
    HARNESS_NOT_ENABLED = "harness_not_enabled", _("Harness not enabled")
    CONFIG_INVALID = "config_invalid", _("Configuration invalid")
    RESOURCE_LIMIT = "resource_limit", _("Resource limit")


class ReferenceOf(models.TextChoices):
    ORACLE = "oracle", _("Oracle")
    STARTER_BASELINE = "starter_baseline", _("Starter baseline")
    SLOT_ORIGINAL = "slot_original", _("Slot original")
    SLOT_HOLED = "slot_holed", _("Slot holed")
    PROBE_MUTANT = "probe_mutant", _("Probe mutant")
    CONFORMANCE = "conformance", _("Conformance")


class LockSource(models.TextChoices):
    MODEL = "model", _("Model")
    PLATFORM = "platform", _("Platform")


class FileOrigin(models.TextChoices):
    STARTER = "starter", _("Starter")
    AUTHORED = "authored", _("Authored")
    MODIFIED = "modified", _("Modified")
    DELETED = "deleted", _("Deleted")
    ARTIFACT = "artifact", _("Artifact")
    SLOT = "slot", _("Slot")


class FileExclusion(models.TextChoices):
    NONE = "", _("Included")
    NON_SOURCE = "non_source", _("Not source")
    BUILD_OUTPUT = "build_output", _("Build output")
    VENDORED = "vendored", _("Vendored")
    BINARY = "binary", _("Binary")
    TOO_LARGE = "too_large", _("Too large")
    LOCKFILE = "lockfile", _("Lockfile")
    DATA = "data", _("Data")


class EvalLevel(models.TextChoices):
    """Section 4.8."""

    NONE = "none", _("None")
    STATIC = "static", _("Static")
    BUILD = "build", _("Build")
    FUNCTIONAL = "functional", _("Functional")
    SECURITY = "security", _("Security")
    FULL = "full", _("Full")


class EvalMode(models.TextChoices):
    FULL = "full", _("Full")
    OVERLAY = "overlay", _("Overlay")
    STATIC_ONLY = "static_only", _("Static only")


class EvaluationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    MEASURED = "measured", _("Measured")
    NOT_MEASURED = "not_measured", _("Not measured")
    ERROR = "error", _("Error")


# ---------------------------------------------------------------------------
# Slot and patterns (section 3.9)
# ---------------------------------------------------------------------------


class SlotKind(models.TextChoices):
    FILE = "file", _("File")
    REGION = "region", _("Region")
    SYMBOL = "symbol", _("Symbol")
    ENDPOINT = "endpoint", _("Endpoint handler")
    COMPONENT = "component", _("UI component")


class HoleMode(models.TextChoices):
    STUB = "stub", _("Stub")
    REMOVE = "remove", _("Remove")
    REWRITE = "rewrite", _("Rewrite")


class EditPolicy(models.TextChoices):
    REVERT = "revert", _("Revert out-of-slot edits")
    FAIL = "fail", _("Fail on out-of-slot edits")


class LinkedChecksSource(models.TextChoices):
    AUTO = "auto", _("Automatic")
    EDITED = "edited", _("Edited")


class ParseStatus(models.TextChoices):
    OK = "ok", _("OK")
    INVALID_JSON = "invalid_json", _("Invalid JSON")
    SCHEMA_VIOLATION = "schema_violation", _("Schema violation")
    EMPTY = "empty", _("Empty")
    REFUSAL = "refusal", _("Refusal")
    TRUNCATED = "truncated", _("Truncated")


class PatternRunLabel(models.TextChoices):
    PLANNED = "planned", _("Planned")
    EXPLORATORY = "exploratory", _("Exploratory")


class HitScope(models.TextChoices):
    SPAN = "span", _("Span")
    FILE = "file", _("File")


class HitStatus(models.TextChoices):
    PRESENT = "present", _("Present")
    ABSENT = "absent", _("Absent")
    ERROR = "error", _("Error")
    NOT_APPLICABLE = "not_applicable", _("Not applicable")


class HitErrorReason(models.TextChoices):
    NONE = "", _("None")
    PARSE_ERROR = "parse_error", _("Parse error")
    TOOL_ERROR = "tool_error", _("Tool error")
    FILE_UNPARSED = "file_unparsed", _("File unparsed")
    TIMEOUT = "timeout", _("Timeout")


class ClusterMethod(models.TextChoices):
    EXACT = "exact", _("Exact")
    MINHASH = "minhash", _("MinHash")


class LabelValue(models.TextChoices):
    PRESENT = "present", _("Present")
    ABSENT = "absent", _("Absent")
    UNSURE = "unsure", _("Unsure")


class AuditStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETE = "complete", _("Complete")


# ---------------------------------------------------------------------------
# Spend (section 3.10)
# ---------------------------------------------------------------------------


class LedgerKind(models.TextChoices):
    RESERVE = "reserve", _("Reserve")
    RELEASE = "release", _("Release")
    USAGE = "usage", _("Usage")
    ADJUST = "adjust", _("Adjust")


class LedgerSource(models.TextChoices):
    SCHEDULER = "scheduler", _("Scheduler")
    GATEWAY_LIVE = "gateway_live", _("Gateway (live)")
    GATEWAY_FINAL = "gateway_final", _("Gateway (final)")
    PROXY = "proxy", _("Local proxy")
    BATCH = "batch", _("Batch")
    OPENROUTER_GENERATION = "openrouter_generation", _("OpenRouter generation stats")
    PRICE_TABLE = "price_table", _("Price table")
    MANUAL = "manual", _("Manual")


class CapScope(models.TextChoices):
    USER = "user", _("User")
    ENDPOINT = "endpoint", _("Endpoint")
    MODEL = "model", _("Model")
    GLOBAL = "global", _("Global")


class CapPeriod(models.TextChoices):
    FIVE_HOURS = "5h", _("5 hours")
    DAY = "day", _("Day")
    WEEK = "week", _("Week")
    MONTH = "month", _("Month")


class CapUnit(models.TextChoices):
    USD = "usd", _("USD")
    LIST_USD = "list_usd", _("List-price USD")
    TOKENS = "tokens", _("Tokens")


class CapAction(models.TextChoices):
    BLOCK = "block", _("Block")
    WARN = "warn", _("Warn")


class CapOnHit(models.TextChoices):
    PAUSE = "pause", _("Pause")
