"""Ready-made study designs a researcher can start a task from (``GET presets/``).

Every preset is a TS ``Preset``: ``{slug, name, description, mode, design,
settings}`` with a normalized design and settings (``services/design.py``).
Common choices, so studies built from different presets stay comparable:

- rating criteria on 1..7 scales anchored at 1 / 4 / 7, each with help text that
  says what to judge, and "can't judge" (``na``) allowed;
- the default pre-questionnaire (programming years, web development and security
  testing experience) and consent enabled with the neutral template;
- a pre-registered primary metric (``design.analysis.primary``);
- presets with a security block run apps in exclusive isolation and recreate the
  app container before each assignment (``hosting.reset = per_assignment``), so
  no rater inherits an earlier rater's data or payloads;
- ``full-evaluation`` adds an attention check to the post-questionnaire and
  pre-registers excluding participants who fail it.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from backend.human_eval.services.checklists import default_security_checklist
from backend.human_eval.services.design import DEFAULT_CONSENT_TEXT
from backend.human_eval.services.design import DEFAULT_PRE_QUESTIONS
from backend.human_eval.services.design import deep_merge
from backend.human_eval.services.design import default_settings
from backend.human_eval.services.design import validate_design
from backend.human_eval.services.design import validate_settings

logger = logging.getLogger(__name__)


def _scale_7(low: str, mid: str, high: str) -> dict[str, Any]:
    return {
        "min": 1,
        "max": 7,
        "step": 1,
        "min_label": low,
        "max_label": high,
        "labels": {"1": low, "4": mid, "7": high},
    }


def _criterion(criterion_id: str, label: str, help_text: str, scale: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": criterion_id,
        "label": label,
        "help": help_text,
        "scale": copy.deepcopy(scale),
        "required": True,
        "allow_na": True,
    }


_QUALITY = _scale_7("Very poor", "Acceptable", "Excellent")

RATING_CRITERIA: list[dict[str, Any]] = [
    _criterion(
        "functionality",
        "Functional correctness",
        "Do the features you tried work as the specification describes? Consider whether actions give the "
        "expected result and whether data is saved and shown correctly. Judge what exists; completeness is "
        "rated separately.",
        _QUALITY,
    ),
    _criterion(
        "completeness",
        "Feature completeness",
        "How much of the specified functionality is present, including the admin features? 1 = almost "
        "nothing of the specification, 7 = everything it asks for.",
        _scale_7("Almost nothing", "About half", "Everything"),
    ),
    _criterion(
        "usability",
        "Usability",
        "How easy is the app to understand and use without help? Consider navigation, labels, feedback "
        "after actions and the clarity of error messages.",
        _QUALITY,
    ),
    _criterion(
        "visual_design",
        "Visual design",
        "Is the interface tidy, consistent and readable on a typical laptop screen (alignment, spacing, "
        "contrast, consistent styling)? Do not reward decoration that gets in the way.",
        _QUALITY,
    ),
    _criterion(
        "robustness",
        "Robustness",
        "How well does the app cope with unexpected input, empty states, page reloads and errors? Lower the "
        "rating for crashes, blank pages, raw error output or lost data.",
        _QUALITY,
    ),
    _criterion(
        "overall",
        "Overall quality",
        "Your overall judgement of the app as a deliverable for its specification, all things considered.",
        _QUALITY,
    ),
]

PAIRWISE_CRITERIA: list[dict[str, Any]] = [
    {
        "id": "functionality",
        "label": "Works as specified",
        "help": "Which app's features work better and do more of what the specification asks?",
    },
    {
        "id": "usability",
        "label": "Easier to use",
        "help": "Which app is easier to understand and use (navigation, labels, feedback, error messages)?",
    },
    {
        "id": "visual_design",
        "label": "Better visual design",
        "help": "Which interface is tidier, more consistent and more readable?",
    },
    {
        "id": "robustness",
        "label": "More robust",
        "help": "Which app copes better with unexpected input, reloads and errors?",
    },
]

COMMENTS_QUESTION: dict[str, Any] = {
    "id": "final_comments",
    "kind": "textarea",
    "label": "Anything else you want to tell the researchers about the apps or this study?",
    "help": "",
    "required": False,
}

_AGREEMENT_OPTIONS = [
    {"value": "strongly_disagree", "label": "Strongly disagree"},
    {"value": "somewhat_disagree", "label": "Somewhat disagree"},
    {"value": "neutral", "label": "Neither agree nor disagree"},
    {"value": "somewhat_agree", "label": "Somewhat agree"},
    {"value": "strongly_agree", "label": "Strongly agree"},
]

ATTENTION_QUESTION: dict[str, Any] = {
    "id": "attention_check",
    "kind": "attention",
    "label": "To show that you read each question carefully, please select 'Somewhat disagree' here.",
    "help": "",
    "required": True,
    "options": copy.deepcopy(_AGREEMENT_OPTIONS),
    "expected": "somewhat_disagree",
}


def _ratings_block(block_id: str = "ratings") -> dict[str, Any]:
    return {
        "id": block_id,
        "type": "rating",
        "title": "Quality ratings",
        "instructions": (
            "Use the app for a few minutes, trying the main features from the specification, then rate it on "
            'each criterion. Choose "can\'t judge" only when you could not form an opinion.'
        ),
        "criteria": copy.deepcopy(RATING_CRITERIA),
        "comment": "optional",
    }


def _requirements_block() -> dict[str, Any]:
    return {
        "id": "requirements",
        "type": "requirements",
        "title": "Requirements checklist",
        "instructions": (
            "Check each requirement of the specification in the running app. **Met**: works as described. "
            "**Partial**: present but incomplete or buggy. **Not met**: missing or not working. **Blocked**: an "
            "app defect stops you from testing it (counts as not met). **Can't test**: you could not test it "
            "for another reason (excluded). Add a short note for anything that is not met."
        ),
        "sources": ["backend", "frontend", "admin", "api", "admin_api"],
        "custom": [],
        "note": "required_unless_met",
    }


def _security_block(*, finding_review: bool) -> dict[str, Any]:
    return {
        "id": "security",
        "type": "security",
        "title": "Security testing",
        "instructions": (
            "Work through the checklist, testing each item against this app only. Report every confirmed "
            "vulnerability with the steps to reproduce it and, if useful, a screenshot. Use the HTTP console to "
            "send crafted requests. Stay within this app and keep load low: no denial-of-service attempts. "
            "The admin login the platform gives you (admin / admin) is part of the test deployment and is out "
            "of scope."
        ),
        "checklist": default_security_checklist(),
        "reports": True,
        "finding_review": {
            "enabled": finding_review,
            "max_findings": 10,
            "categories": ["security", "secrets"],
            "include_ai": False,
            "include_dependency": False,
            "include_dynamic": False,
        },
        "http_console": True,
    }


def _bugs_block() -> dict[str, Any]:
    return {
        "id": "bugs",
        "type": "bugs",
        "title": "Bug log",
        "instructions": (
            "Log each distinct defect you notice (one report per bug) with the steps to reproduce it. If you "
            "found none, confirm that instead."
        ),
        "min_reports": 0,
    }


def _sus_block() -> dict[str, Any]:
    return {
        "id": "sus",
        "type": "questionnaire",
        "title": "System Usability Scale",
        "instructions": 'Answer about the app you just used. "System" means this app.',
        "instrument": "sus",
        "questions": [],
    }


def _scenarios_block() -> dict[str, Any]:
    return {
        "id": "scenarios",
        "type": "scenarios",
        "title": "Usability tasks",
        "instructions": (
            "Start the timer, try to complete the task without outside help, then stop the timer and record "
            "the outcome. Give up when you would give up in real life; that is a valid result."
        ),
        "scenarios": [
            {
                "id": "first_impression",
                "title": "Understand the app",
                "description": (
                    "Without reading the specification, explore the start page for up to two minutes and work "
                    "out what the app is for and what you can do with it."
                ),
                "success_criteria": "You can name the app's purpose and its main feature.",
            },
            {
                "id": "sign_up_in",
                "title": "Create an account and sign in",
                "description": "Register a new account, sign out, and sign in again with it.",
                "success_criteria": "You are signed in with the account you created.",
            },
            {
                "id": "main_task",
                "title": "Complete the main task",
                "description": (
                    "Use the app's main feature end to end as the specification describes it, for example create "
                    "an item, change it and find it again."
                ),
                "success_criteria": "The result is saved and visible after reloading the page.",
            },
        ],
        "timed": True,
        "ease_question": True,
    }


def _design(blocks: list[dict[str, Any]], primary: dict[str, str], **analysis: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "blocks": blocks,
        "analysis": {"primary": primary, **analysis},
        "consent": {"enabled": True, "text": DEFAULT_CONSENT_TEXT},
        "pre_questions": copy.deepcopy(DEFAULT_PRE_QUESTIONS),
        "post_questions": [copy.deepcopy(COMMENTS_QUESTION)],
    }


_ISOLATED_HOSTING = {"isolation": "exclusive", "reset": "per_assignment"}

_RAW_PRESETS: list[dict[str, Any]] = [
    {
        "slug": "app-grading",
        "name": "App grading",
        "description": (
            "Raters use each app and score it on six quality criteria (1-7), logging any bugs they find. "
            "A quick, general-purpose quality comparison."
        ),
        "mode": "single",
        "design": _design(
            [_ratings_block(), _bugs_block()],
            {"block_id": "ratings", "metric": "composite"},
            planned_ratings_per_item=3,
        ),
        "settings": {"quality": {"min_seconds_per_item": 180}},
    },
    {
        "slug": "requirements-audit",
        "name": "Requirements audit",
        "description": (
            "Raters check every requirement and endpoint of the app's specification in the running app (met, "
            "partial, not met). Compares human compliance with the automated conformance suite."
        ),
        "mode": "single",
        "design": _design(
            [_requirements_block(), _bugs_block()],
            {"block_id": "requirements", "metric": "compliance"},
            planned_ratings_per_item=2,
        ),
        "settings": {"allocation": {"ratings_per_item": 2}, "quality": {"min_seconds_per_item": 300}},
    },
    {
        "slug": "security-assessment",
        "name": "Security assessment",
        "description": (
            "Guided security testing with an OWASP-based checklist, vulnerability reports, an HTTP console and "
            "true/false-positive review of the automated analysers' findings. Each rater gets a fresh copy "
            "of the app."
        ),
        "mode": "single",
        "design": _design(
            [_security_block(finding_review=True)],
            {"block_id": "security", "metric": "vulnerable_share"},
            planned_ratings_per_item=2,
        ),
        "settings": {
            "hosting": dict(_ISOLATED_HOSTING),
            "allocation": {"ratings_per_item": 2, "assignment_timeout_minutes": 120},
            "quality": {"min_seconds_per_item": 600},
        },
    },
    {
        "slug": "usability-test",
        "name": "Usability test",
        "description": (
            "Timed task scenarios with a Single Ease Question after each, followed by the System Usability "
            "Scale (SUS)."
        ),
        "mode": "single",
        "design": _design(
            [_scenarios_block(), _sus_block()],
            {"block_id": "sus", "metric": "score"},
            planned_ratings_per_item=3,
        ),
        "settings": {"blinding": {"show_spec": False}, "quality": {"min_seconds_per_item": 240}},
    },
    {
        "slug": "ab-comparison",
        "name": "A/B comparison",
        "description": (
            "Raters see two apps built from the same specification side by side and say which is better on "
            "each criterion and overall. Pairs only cross conditions; sides are randomised."
        ),
        "mode": "pairwise",
        "design": _design(
            [
                {
                    "id": "preference",
                    "type": "pairwise",
                    "title": "Which app is better?",
                    "instructions": (
                        "Use both apps on the same features, then pick the better one for each criterion. "
                        'Choose "tie" only when you see no meaningful difference.'
                    ),
                    "criteria": copy.deepcopy(PAIRWISE_CRITERIA),
                    "scale": "ab_tie",
                    "overall": True,
                    "rationale": "optional",
                },
            ],
            {"block_id": "preference", "metric": "overall"},
            planned_ratings_per_item=3,
        ),
        "settings": {
            "pairing": {"strategy": "all_pairs", "cross_condition_only": True},
            "quality": {"min_seconds_per_item": 240},
        },
    },
    {
        "slug": "full-evaluation",
        "name": "Full evaluation",
        "description": (
            "The complete protocol: requirements checklist, quality ratings, guided security testing with "
            "finding review, bug log and SUS, plus an attention check. Long sessions; each rater gets a fresh "
            "copy of the app."
        ),
        "mode": "single",
        "design": {
            **_design(
                [
                    _requirements_block(),
                    _ratings_block(),
                    _security_block(finding_review=True),
                    _bugs_block(),
                    _sus_block(),
                ],
                {"block_id": "ratings", "metric": "composite"},
                planned_ratings_per_item=2,
                exclusion_rules={"attention_failed": True},
            ),
            "post_questions": [copy.deepcopy(ATTENTION_QUESTION), copy.deepcopy(COMMENTS_QUESTION)],
        },
        "settings": {
            "hosting": dict(_ISOLATED_HOSTING),
            "allocation": {"ratings_per_item": 2, "assignment_timeout_minutes": 180},
            "quality": {"min_seconds_per_item": 900},
        },
    },
]


def _build(raw: dict[str, Any]) -> dict[str, Any]:
    design, design_errors = validate_design(raw["mode"], raw["design"])
    settings, settings_errors = validate_settings(deep_merge(default_settings(), raw["settings"]))
    if design_errors or settings_errors:  # pragma: no cover - guarded by tests
        logger.error("human_eval preset %s is invalid: %s %s", raw["slug"], design_errors, settings_errors)
    return {
        "slug": raw["slug"],
        "name": raw["name"],
        "description": raw["description"],
        "mode": raw["mode"],
        "design": design if design is not None else copy.deepcopy(raw["design"]),
        "settings": settings if settings is not None else deep_merge(default_settings(), raw["settings"]),
    }


#: The presets in display order (normalized TS ``Preset`` dicts). Treat as read-only; use :func:`get_preset`.
PRESETS: list[dict[str, Any]] = [_build(raw) for raw in _RAW_PRESETS]
PRESET_SLUGS: tuple[str, ...] = tuple(preset["slug"] for preset in PRESETS)


def list_presets() -> list[dict[str, Any]]:
    """Deep copies of every preset (``GET presets/``)."""
    return copy.deepcopy(PRESETS)


def get_preset(slug: str) -> dict[str, Any] | None:
    """A deep copy of one preset, or None for an unknown slug."""
    for preset in PRESETS:
        if preset["slug"] == slug:
            return copy.deepcopy(preset)
    return None
