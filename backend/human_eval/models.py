"""Human evaluation: researcher-defined manual testing tasks graded by human participants.

A researcher builds an :class:`EvalTask` over a pool of generated apps
(:class:`EvalTaskApp`, one per ``GenerationJob``) and a *design* — the ordered
evaluation blocks (rubric ratings, requirement checklist, security testing,
usability scenarios, questionnaires, bug log, A/B preference) validated by
``services/design.py``. Opening the task freezes the design (``design_hash``)
and derives the :class:`EvalItem` units: one app (single mode) or one pair of
apps (pairwise mode).

Participants (:class:`Participant`) join through an invite link, a
pre-generated access code, or a platform account (optionally a restricted
evaluator account, :class:`EvaluatorProfile`). The allocation engine hands each
participant :class:`Assignment` rows (participant x item); answers are stored per
block in :class:`Response`, plus free-form :class:`Observation` reports (bugs,
vulnerabilities, notes) with screenshot :class:`Attachment` files, TP/FP
verdicts on automated analysis findings (:class:`FindingVerdict`) and every
request sent through the HTTP console (:class:`HttpExchange`).

Research-data rules: rows keep snapshots of what the participant saw
(``EvalTaskApp.snapshot``, ``Assignment.context``, ``FindingVerdict.finding_snapshot``)
so analysis and exports survive later edits or deletion of jobs and findings.
Severity labels on observations are the *tester's own* rating; automated
findings are only ever shown with the tool's own ``tool_severity`` label.
"""

from __future__ import annotations

import secrets
import uuid

from django.conf import settings as django_settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


def _new_seed() -> int:
    return secrets.randbelow(2**31 - 1) + 1


def _new_invite_token() -> str:
    return secrets.token_urlsafe(18)


def _attachment_path(instance: Attachment, filename: str) -> str:
    # Random, unguessable names: MEDIA_ROOT is served without auth when DEBUG is on.
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[1].lower()[:8]
    return f"human_eval/{uuid.uuid4().hex}{ext}"


class EvalTask(models.Model):
    """A manual testing task (study) over a pool of generated apps."""

    class Mode(models.TextChoices):
        SINGLE = "single", _("Single app")
        PAIRWISE = "pairwise", _("Pairwise (A/B)")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        OPEN = "open", _("Open")
        PAUSED = "paused", _("Paused")
        CLOSED = "closed", _("Closed")
        ARCHIVED = "archived", _("Archived")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(
        _("description"),
        blank=True,
        default="",
        help_text="Short public summary shown on the join page",
    )
    instructions = models.TextField(
        _("instructions"),
        blank=True,
        default="",
        help_text="Markdown shown to participants on the task page and in the workbench",
    )
    mode = models.CharField(_("mode"), max_length=20, choices=Mode.choices, default=Mode.SINGLE)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.DRAFT)
    design = models.JSONField(
        _("design"),
        default=dict,
        blank=True,
        help_text="Evaluation blocks, consent and questionnaires (services/design.py); frozen at first open",
    )
    settings = models.JSONField(
        _("settings"),
        default=dict,
        blank=True,
        help_text="Blinding, allocation, pairing, access, hosting and quality settings (services/design.py)",
    )
    design_hash = models.CharField(_("design hash"), max_length=64, blank=True, default="")
    frozen_at = models.DateTimeField(_("design frozen at"), null=True, blank=True)
    seed = models.PositiveIntegerField(
        _("seed"),
        default=_new_seed,
        help_text="Seeds allocation order, pair sides and finding samples (reproducible)",
    )
    experiment = models.ForeignKey(
        "generation.Experiment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="human_eval_tasks",
    )
    opens_at = models.DateTimeField(_("opens at"), null=True, blank=True)
    closes_at = models.DateTimeField(_("closes at"), null=True, blank=True)
    opened_at = models.DateTimeField(_("first opened at"), null=True, blank=True)
    closed_at = models.DateTimeField(_("closed at"), null=True, blank=True)
    first_closed_at = models.DateTimeField(
        _("first closed at"),
        null=True,
        blank=True,
        help_text="Never overwritten: data collected after a reopen is not confirmatory",
    )
    protocol_log = models.JSONField(
        _("protocol log"),
        default=list,
        blank=True,
        help_text="Audit trail of post-open changes [{at, by, field, old_sha256, new_sha256}]",
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="human_eval_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Human evaluation task")
        verbose_name_plural = _("Human evaluation tasks")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_by", "status"], name="heval_task_owner_status")]

    def __str__(self) -> str:
        return self.title

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class EvalTaskApp(models.Model):
    """One generated app in a task's pool, with its blinded label and hosting state."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(EvalTask, on_delete=models.CASCADE, related_name="apps")
    job = models.ForeignKey(
        "generation.GenerationJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="human_eval_apps",
    )
    label = models.CharField(_("blinded label"), max_length=40, help_text='Shown to participants, e.g. "App 07"')
    group_key = models.CharField(
        _("group key"),
        max_length=200,
        blank=True,
        default="",
        help_text="App spec slug; pairs are only formed within one group",
    )
    position = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text="Inactive apps get no new assignments (existing responses are kept)",
    )
    snapshot = models.JSONField(
        _("snapshot"),
        default=dict,
        blank=True,
        help_text="De-blinding key frozen when the app was added (model, condition, spec, conformance)",
    )
    # Hosting (services/hosting.py). The container is resolved again at view
    # time: a rebuild creates a new llm-<hex> container.
    container = models.ForeignKey(
        "runtime.ContainerInstance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="human_eval_apps",
    )
    started_by_task = models.BooleanField(
        _("started by task"),
        default=False,
        help_text="Only containers the task started are stopped by the idle reaper",
    )
    pinned = models.BooleanField(
        _("pinned"),
        default=False,
        help_text="Pre-started by the researcher: never idle-stopped or evicted",
    )
    last_activity_at = models.DateTimeField(_("last activity"), null=True, blank=True)
    start_dispatched_at = models.DateTimeField(
        _("start dispatched at"),
        null=True,
        blank=True,
        help_text="In-flight marker set under row lock when a build/start/reset is dispatched; cleared after",
    )
    hosting_requested_at = models.DateTimeField(
        _("hosting requested at"),
        null=True,
        blank=True,
        help_text="Set while a start is queued for a free app/build slot (FIFO); cleared once started",
    )
    hosting_error = models.TextField(_("hosting error"), blank=True, default="")
    finding_sample = models.JSONField(
        _("finding sample"),
        default=list,
        blank=True,
        help_text="Finding ids (str) drawn once for TP/FP review, identical for every rater of this app",
    )
    finding_sample_at = models.DateTimeField(_("finding sample drawn at"), null=True, blank=True)
    finding_frame = models.JSONField(
        _("finding sampling frame"),
        default=dict,
        blank=True,
        help_text="{tool_slug: {population, sampled}} for inverse-probability weighting of precision",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Task app")
        verbose_name_plural = _("Task apps")
        ordering = ["task", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "job"],
                condition=Q(job__isnull=False),
                name="human_eval_taskapp_task_job_uniq",
            ),
            models.UniqueConstraint(fields=["task", "label"], name="human_eval_taskapp_task_label_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.task_id})"


class EvalItem(models.Model):
    """The unit a participant evaluates: one app, or an (unordered) pair of apps."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(EvalTask, on_delete=models.CASCADE, related_name="items")
    app_a = models.ForeignKey(EvalTaskApp, on_delete=models.CASCADE, related_name="items_as_a")
    app_b = models.ForeignKey(
        EvalTaskApp,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="items_as_b",
    )
    group_key = models.CharField(_("group key"), max_length=200, blank=True, default="")
    position = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Evaluation item")
        verbose_name_plural = _("Evaluation items")
        ordering = ["task", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "app_a"],
                condition=Q(app_b__isnull=True),
                name="human_eval_item_single_uniq",
            ),
            models.UniqueConstraint(
                fields=["task", "app_a", "app_b"],
                condition=Q(app_b__isnull=False),
                name="human_eval_item_pair_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"item {str(self.id)[:8]}"


class EvalInvite(models.Model):
    """A shareable join link (``/eval/join/<token>``); each visitor becomes a new participant."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(EvalTask, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(_("token"), max_length=64, unique=True, default=_new_invite_token)
    label = models.CharField(_("label"), max_length=120, blank=True, default="")
    max_uses = models.PositiveIntegerField(_("max uses"), null=True, blank=True)
    uses = models.PositiveIntegerField(_("uses"), default=0)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Invite link")
        verbose_name_plural = _("Invite links")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.label or f"invite {self.token[:6]}"


class Participant(models.Model):
    """One rater in one task (pseudonymous label ``P001``)."""

    class Kind(models.TextChoices):
        INVITE = "invite", _("Invite link")
        CODE = "code", _("Access code")
        ACCOUNT = "account", _("Account")
        PREVIEW = "preview", _("Researcher preview")

    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(EvalTask, on_delete=models.CASCADE, related_name="participants")
    number = models.PositiveIntegerField(_("number"), help_text="Sequential per task; label P001")
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.INVITED)
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="human_eval_participations",
    )
    invite = models.ForeignKey(
        EvalInvite,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="participants",
    )
    # Secrets are stored as sha256 only (tokens/services.py pattern).
    access_code_hash = models.CharField(_("access code hash"), max_length=64, unique=True, null=True, blank=True)
    access_code_hint = models.CharField(_("access code hint"), max_length=8, blank=True, default="")
    session_token_hash = models.CharField(_("session token hash"), max_length=64, unique=True, null=True, blank=True)
    consent_at = models.DateTimeField(_("consent given at"), null=True, blank=True)
    pre_answers = models.JSONField(_("pre-questionnaire answers"), default=dict, blank=True)
    post_answers = models.JSONField(_("post-questionnaire answers"), default=dict, blank=True)
    completion_code = models.CharField(_("completion code"), max_length=16, blank=True, default="")
    joined_at = models.DateTimeField(_("joined at"), null=True, blank=True)
    last_seen_at = models.DateTimeField(_("last seen at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    withdrawn_at = models.DateTimeField(_("withdrawn at"), null=True, blank=True)
    device_hash = models.CharField(
        _("device hash"),
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="sha256 of a long-lived random device cookie; flags possible duplicate joins",
    )
    excluded = models.BooleanField(_("excluded from analysis"), default=False)
    exclusion_reason = models.TextField(_("exclusion reason"), blank=True, default="")
    excluded_at = models.DateTimeField(_("excluded at"), null=True, blank=True)
    excluded_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    note = models.TextField(_("researcher note"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Participant")
        verbose_name_plural = _("Participants")
        ordering = ["task", "number"]
        constraints = [
            models.UniqueConstraint(fields=["task", "number"], name="human_eval_participant_number_uniq"),
            models.UniqueConstraint(
                fields=["task", "user"],
                condition=Q(user__isnull=False),
                name="human_eval_participant_task_user_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        return f"P{self.number:03d}"


class Assignment(models.Model):
    """One participant evaluating one item."""

    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        IN_PROGRESS = "in_progress", _("In progress")
        SUBMITTED = "submitted", _("Submitted")
        SKIPPED = "skipped", _("Skipped")
        EXPIRED = "expired", _("Expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(EvalTask, on_delete=models.CASCADE, related_name="assignments")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="assignments")
    item = models.ForeignKey(EvalItem, on_delete=models.CASCADE, related_name="assignments")
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    position = models.PositiveIntegerField(_("position"), help_text="1-based order within the participant's work")
    swapped = models.BooleanField(
        _("sides swapped"),
        default=False,
        help_text="Pairwise: True when item.app_b is shown as A (randomized, seeded)",
    )
    context = models.JSONField(
        _("context"),
        default=dict,
        blank=True,
        help_text="Frozen at first open: requirement lists, finding ids, instrument questions per block",
    )
    skip_reason = models.CharField(_("skip reason"), max_length=40, blank=True, default="")
    skip_note = models.TextField(_("skip note"), blank=True, default="")
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    last_activity_at = models.DateTimeField(_("last activity"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    active_seconds = models.PositiveIntegerField(
        _("active seconds"),
        default=0,
        help_text="Visible-tab time from heartbeats (capped per beat)",
    )
    wall_seconds = models.PositiveIntegerField(
        _("wall seconds"),
        default=0,
        help_text="Workbench-open time from heartbeats (visible or not), gaps over 5 minutes removed",
    )
    app_views = models.JSONField(
        _("app views"),
        default=dict,
        blank=True,
        help_text="{slot: {viewed_seconds, first_running_at}} from heartbeats",
    )
    excluded = models.BooleanField(_("excluded from analysis"), default=False)
    exclusion_reason = models.TextField(_("exclusion reason"), blank=True, default="")
    excluded_at = models.DateTimeField(_("excluded at"), null=True, blank=True)
    excluded_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Assignment")
        verbose_name_plural = _("Assignments")
        ordering = ["participant", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "item"],
                name="human_eval_assignment_participant_item_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["task", "status"], name="heval_assign_task_status"),
            models.Index(fields=["item", "status"], name="heval_assign_item_status"),
        ]

    def __str__(self) -> str:
        return f"assignment {str(self.id)[:8]} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in {self.Status.ASSIGNED, self.Status.IN_PROGRESS}


class Response(models.Model):
    """Answers to one design block within one assignment (autosaved drafts, locked at submit)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="responses")
    block_id = models.CharField(_("block id"), max_length=40)
    answers = models.JSONField(_("answers"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Response")
        verbose_name_plural = _("Responses")
        constraints = [
            models.UniqueConstraint(fields=["assignment", "block_id"], name="human_eval_response_block_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.block_id} @ {self.assignment_id}"


class Observation(models.Model):
    """A bug, vulnerability or note a participant logged against one app."""

    class Kind(models.TextChoices):
        BUG = "bug", _("Bug")
        VULNERABILITY = "vulnerability", _("Vulnerability")
        NOTE = "note", _("Note")

    class Severity(models.TextChoices):
        # The tester's own judgement, labelled as such everywhere.
        CRITICAL = "critical", _("Critical")
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")
        INFO = "info", _("Info")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="observations")
    task_app = models.ForeignKey(EvalTaskApp, on_delete=models.CASCADE, related_name="observations")
    block_id = models.CharField(_("block id"), max_length=40, blank=True, default="")
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    steps = models.TextField(_("steps to reproduce"), blank=True, default="")
    location = models.CharField(
        _("location"),
        max_length=500,
        blank=True,
        default="",
        help_text="Page URL or endpoint",
    )
    category = models.CharField(_("category"), max_length=60, blank=True, default="")
    severity = models.CharField(_("tester severity"), max_length=20, choices=Severity.choices, blank=True, default="")
    cwe = models.CharField(_("CWE"), max_length=20, blank=True, default="")
    checklist_item_id = models.CharField(_("checklist item"), max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Observation")
        verbose_name_plural = _("Observations")
        ordering = ["created_at"]
        indexes = [models.Index(fields=["task_app", "kind"], name="heval_obs_app_kind")]

    def __str__(self) -> str:
        return self.title


class Attachment(models.Model):
    """A screenshot attached to an observation; served only through the API."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(_("file"), upload_to=_attachment_path, max_length=255)
    content_type = models.CharField(_("content type"), max_length=60)
    size = models.PositiveIntegerField(_("size (bytes)"))
    original_name = models.CharField(_("original name"), max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Attachment")
        verbose_name_plural = _("Attachments")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.original_name or str(self.id)


class FindingVerdict(models.Model):
    """A participant's TP/FP/unsure judgement on one automated analysis finding."""

    class Verdict(models.TextChoices):
        TP = "tp", _("True positive")
        FP = "fp", _("False positive")
        UNSURE = "unsure", _("Unsure")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="finding_verdicts")
    task_app = models.ForeignKey(EvalTaskApp, on_delete=models.CASCADE, related_name="finding_verdicts")
    finding = models.ForeignKey(
        "analysis.Finding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="human_eval_verdicts",
    )
    finding_key = models.CharField(_("finding key"), max_length=64, help_text="str(finding id), kept if it is deleted")
    finding_snapshot = models.JSONField(
        _("finding snapshot"),
        default=dict,
        blank=True,
        help_text="tool, rule_id, tool_severity, category, title, file_path, line as shown to the participant",
    )
    verdict = models.CharField(_("verdict"), max_length=10, choices=Verdict.choices)
    note = models.TextField(_("note"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Finding verdict")
        verbose_name_plural = _("Finding verdicts")
        constraints = [
            models.UniqueConstraint(fields=["assignment", "finding_key"], name="human_eval_verdict_finding_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.verdict} {self.finding_key[:8]}"


class HttpExchange(models.Model):
    """One request a participant sent to an app through the HTTP console (and its response)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="http_exchanges")
    task_app = models.ForeignKey(EvalTaskApp, on_delete=models.CASCADE, related_name="http_exchanges")
    method = models.CharField(_("method"), max_length=10)
    path = models.TextField(_("path"))
    request_headers = models.JSONField(_("request headers"), default=dict, blank=True)
    request_body = models.TextField(_("request body (excerpt)"), blank=True, default="")
    status_code = models.PositiveIntegerField(_("status code"), null=True, blank=True)
    response_headers = models.JSONField(_("response headers"), default=dict, blank=True)
    response_body = models.TextField(_("response body (excerpt)"), blank=True, default="")
    response_truncated = models.BooleanField(_("response truncated"), default=False)
    elapsed_ms = models.PositiveIntegerField(_("elapsed (ms)"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("HTTP exchange")
        verbose_name_plural = _("HTTP exchanges")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["assignment", "created_at"], name="heval_http_assign_created")]

    def __str__(self) -> str:
        return f"{self.method} {self.path[:60]}"


class EvaluatorProfile(models.Model):
    """Marks a user as an evaluator account; restricted ones may only use the evaluation portal."""

    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="evaluator_profile",
    )
    is_restricted = models.BooleanField(
        _("restricted"),
        default=True,
        help_text="Restricted evaluators can only reach the evaluation portal (middleware.EvaluatorScopeMiddleware)",
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Evaluator profile")
        verbose_name_plural = _("Evaluator profiles")

    def __str__(self) -> str:
        return f"evaluator {self.user_id}"
