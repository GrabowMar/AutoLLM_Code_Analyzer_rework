"""Let a study's reasoning effort reach OpenCode Go models (C2): set ``metadata.supports_reasoning``.

Without the flag the budget policy clamps a Go model's ``reasoning`` to ``provider_default`` (note
``reasoning_unsupported``), because the gateway's OpenRouter ``reasoning`` object is ignored by Go. Since C2
the executor proxy sends the policy's effort to ``opencode.ai`` as top-level ``reasoning_effort``
(``inference.upstream.opencode_reasoning``), so a flagged row keeps ``reasoning.mode=effort`` in the plan.
The command also writes ``metadata.reasoning_modes = ["effort"]``: Go takes only ``reasoning_effort``, so the
policy clamps any other forced mode (``max_tokens``) and ``reasoning.exclude`` on these rows
(``budget.policy``), instead of recording a treatment that is never sent.

The flag is read when a study is **launched** (each run manifest freezes the clamp), so set it before
launching; manifests of studies launched earlier are not affected either way.

Which rows: by default the final study's Go models **whose serving provider lists the effort** (``--effort``,
default ``high``) in its catalogue record. The other final-study rows (qwen3.8-flash, mimo-v2.6-flash,
mimo-v2.6-pro, minimax-m3: no listed ``high``) are skipped unless named with ``--model`` or ``--force`` is
given, because Go may ignore or remap the value there and the manifest would then record an effort that was
not honoured.

Idempotent; touches only ``metadata`` (``updated_at``, the manifests' pricing ``as_of``, is left alone)::

    manage.py go_reasoning_effort --dry-run            # what would change
    manage.py go_reasoning_effort                      # flag the rows that list the effort
    manage.py go_reasoning_effort --force              # all 8 final-study Go rows
    manage.py go_reasoning_effort --model local-opencode-go/hy4 --model ...
    manage.py go_reasoning_effort --probe              # + two live calls per row: without / with the effort
    manage.py go_reasoning_effort --unset              # back to provider default (removes the keys)

"Effort high" on these rows means *high where the route exposes it*: the providers' scales differ
(DeepSeek low/high/max, Hy4 none/high, Gemini low/medium/high, qwen3.8-flash low/medium/xhigh, MiMo and
MiniMax list no effort at all), and the command prints, per row, what the serving provider lists. A provider
that rejects the value fails the call with a 400 whose message names the effort and this command
(``inference.chat.REASONING_REJECTED_HINT``). ``--probe`` finds a rejection before a launch and compares the
reasoning tokens of a call without and with the effort; equal counts mean the value may be ignored (a hint,
not a proof: one small prompt).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from backend.llm_models.models import LLMModel

ENDPOINT_SLUG = "opencode-go"
# The Go models of the final study (proposal section 3.2 / task C2).
FINAL_STUDY_MODELS = (
    "local-opencode-go/deepseek-v4-pro",
    "local-opencode-go/deepseek-v4.1-flash",
    "local-opencode-go/hy4",
    "local-opencode-go/mimo-v2.6-flash",
    "local-opencode-go/mimo-v2.6-pro",
    "local-opencode-go/qwen3.8-flash",
    "local-opencode-go/minimax-m3",
    "local-opencode-go/gemini-4-argon",
)
FLAG = "supports_reasoning"
MODES = "reasoning_modes"
MODES_VALUE = ["effort"]
SOURCE = "reasoning_control"
SOURCE_VALUE = "opencode_go_reasoning_effort"
PROBE_PROMPT = "What is 37 * 43? Think it through, then reply with the number only."
PROBE_MAX_TOKENS = 2048


def listed_efforts(metadata: dict[str, Any] | None) -> list[str] | None:
    """The effort values the serving provider's record lists (None: the record lists no effort option)."""
    from backend.generation.budget.model_facts import serving_record_facts

    options = serving_record_facts(metadata).get("reasoning_options") or []
    values = [str(v) for o in options if o.get("type") == "effort" for v in o.get("values") or []]
    return values or None


class Command(BaseCommand):
    help = "Set (or --unset) metadata.supports_reasoning on OpenCode Go models so a study's effort reaches them."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help="LLMModel.model_id (repeatable, flagged even without a listed effort); default: the final "
            "study's Go models whose provider lists the effort",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="with the default list: flag every final-study Go row, also those that list no such effort",
        )
        parser.add_argument("--unset", action="store_true", help="remove the flag (provider default again)")
        parser.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
        parser.add_argument(
            "--probe",
            action="store_true",
            help="after writing, two small live calls per row, without and with reasoning_effort (needs the "
            "executor; not with --dry-run)",
        )
        parser.add_argument("--effort", default="high", help="the effort the study uses (selection and --probe)")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["probe"] and options["dry_run"]:
            msg = "--probe makes live calls and records LLM requests: it cannot be combined with --dry-run"
            raise CommandError(msg)
        explicit = bool(options["models"])
        wanted = tuple(options["models"] or FINAL_STUDY_MODELS)
        rows = {
            row.model_id: row
            for row in LLMModel.objects.select_related("endpoint").filter(
                model_id__in=wanted,
                endpoint__slug=ENDPOINT_SLUG,
            )
        }
        missing = [m for m in wanted if m not in rows]
        if missing:
            msg = f"not OpenCode Go ({ENDPOINT_SLUG}) models here: {', '.join(missing)}"
            raise CommandError(msg)
        effort = str(options["effort"])
        selected = explicit or options["force"] or options["unset"]
        changed = 0
        done: list[str] = []
        with transaction.atomic():
            for model_id in wanted:
                row = LLMModel.objects.select_for_update().get(pk=rows[model_id].pk)
                efforts = listed_efforts(row.metadata)
                exposed = "provider lists " + ("/".join(efforts) if efforts else "no effort option")
                listed = efforts is not None and effort in efforts
                if not (listed or selected):
                    self.stdout.write(
                        f"{row.pk:>5} {model_id:<42} {'skipped':<12} ({exposed}): {effort!r} is not listed; "
                        "name it with --model or pass --force to flag it anyway",
                    )
                    continue
                meta = dict(row.metadata or {})
                if options["unset"]:
                    update = any(key in meta for key in (FLAG, MODES, SOURCE))
                    for key in (FLAG, MODES, SOURCE):
                        meta.pop(key, None)
                else:
                    update = meta.get(FLAG) is not True or meta.get(MODES) != MODES_VALUE
                    meta[FLAG] = True
                    meta[MODES] = list(MODES_VALUE)
                    meta[SOURCE] = {"value": SOURCE_VALUE, "set_at": timezone.now().isoformat()}
                warn = "" if options["unset"] or listed else f"  WARNING: {effort!r} is not a listed effort"
                action = "unchanged" if not update else ("would update" if options["dry_run"] else "updated")
                self.stdout.write(f"{row.pk:>5} {model_id:<42} {action:<12} ({exposed}){warn}")
                changed += int(update)
                done.append(model_id)
                if update and not options["dry_run"]:
                    # A queryset update: ``updated_at`` (the manifests' pricing ``as_of``) is left alone.
                    LLMModel.objects.filter(pk=row.pk).update(metadata=meta)
        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write(f"{verb} {changed} of {len(wanted)} row(s)")
        if options["probe"] and not options["unset"]:
            for model_id in done:
                self.stdout.write(f"probe {model_id}: {self._probe(rows[model_id], effort)}")

    @staticmethod
    def _probe(row: LLMModel, effort: str) -> str:
        """Two small calls through the queue: provider default, then the canonical reasoning object (sent as
        ``reasoning_effort``). A rejection is explicit; equal reasoning tokens hint that the value is ignored."""
        from backend.inference.access import LLMAccessError
        from backend.inference.access import endpoint_access
        from backend.inference.chat import chat

        access = endpoint_access(row.endpoint, served_model_name=row.served_model_name)
        tokens: dict[str, Any] = {}
        for label, extra in (("default", {}), ("effort", {"reasoning": {"effort": effort}})):
            try:
                result = chat(
                    access,
                    messages=[{"role": "user", "content": PROBE_PROMPT}],
                    params={"max_tokens": PROBE_MAX_TOKENS, **extra},
                    seed=None,
                    source="probe",
                )
            except LLMAccessError as exc:
                if label == "effort":
                    return f"REJECTED/FAILED: {exc}"
                tokens[label] = f"failed ({exc.code})"
                continue
            usage = result.response.get("usage") or {}
            tokens[label] = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        base, forced = tokens.get("default"), tokens.get("effort")
        if isinstance(base, int) and isinstance(forced, int):
            verdict = "effect visible" if forced != base else "no visible effect: the value may be ignored"
        else:
            verdict = "reasoning tokens not reported: cannot tell whether the value is honoured"
        return f"accepted (reasoning_tokens default={base}, {effort}={forced}; {verdict})"
