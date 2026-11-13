"""WP-01 review round 1: immutability, pinned refs, nested blob references and attempt storage.

Each test mirrors one MAJOR finding of the WP-01 review (and the bulk-update MINOR):

1. every library row and slot set refuses changes to anything but its mutable columns, also through
   ``QuerySet.update`` (the reviewer's mutability probe);
2. library refs inside content are stored hash-pinned, so equal entities hash alike;
3. blob GC keeps the shas nested inside JSON columns and inside blob payloads;
4. ``RunAttempt`` stores observed inputs and the trajectory blob.
"""

from __future__ import annotations

import copy
import os
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction

from backend.generation import blobs
from backend.generation.models import LIBRARY_MODELS
from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import PromptSet
from backend.generation.models import PromptTemplate
from backend.generation.models import ResourcePolicy
from backend.generation.models import RunAttempt
from backend.generation.models import RunEvaluation
from backend.generation.models import RunManifest
from backend.generation.models import SlotSet
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import StackProfile
from backend.generation.models import Starter
from backend.generation.models import StudyPlan
from backend.generation.models import TaskSpecVersion
from backend.generation.models import choices
from backend.generation.models.library import MUTABLE_VERSION_FIELDS
from backend.generation.models.library import VersionedEntity
from backend.generation.testing import content as samples
from backend.generation.testing import factories

pytestmark = pytest.mark.django_db

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# Audit columns: written once by Django itself (auto_now_add) or nulled by SET_NULL on a user delete.
AUDIT_FIELDS = {"id", "created_by", "created_at"}
VERSIONED_MODELS = [model for model in LIBRARY_MODELS.values() if issubclass(model, VersionedEntity)]


def factory_for(model: type[models.Model]) -> Any:
    return getattr(factories, f"{model.__name__}Factory")


def changed_value(instance: models.Model, name: str) -> Any:  # noqa: PLR0911 - one return per field type
    """A value for ``name`` that differs from the stored one (a fresh row for foreign keys)."""
    field = instance._meta.get_field(name)
    current = getattr(instance, name)
    if isinstance(field, models.ForeignKey):
        model = field.related_model
        if model is type(instance):
            return factory_for(model).create()
        if model is TaskSpecVersion:
            return factories.TaskSpecVersionFactory.create()
        if model is GenerationRun:
            return factories.GenerationRunFactory.create(tested=True)
        return factory_for(model).create()
    if isinstance(field, models.BooleanField):
        return not current
    if isinstance(field, models.IntegerField):
        return (current or 0) + 7
    if isinstance(field, models.JSONField):
        return {"changed": True}
    if isinstance(field, models.CharField) and field.max_length == 64 and SHA_RE.match(str(current or "")):
        return "f" * 64
    if field.choices:
        values = [value for value, _ in field.choices if value != current]
        return values[0]
    return f"{current}-x"[: field.max_length or 200]


def assert_refused(instance: models.Model, name: str) -> None:
    value = changed_value(instance, name)
    fresh = type(instance)._base_manager.get(pk=instance.pk)
    setattr(fresh, name, value)
    with pytest.raises(ImmutableVersionError), transaction.atomic():
        fresh.save()
    fresh = type(instance)._base_manager.get(pk=instance.pk)
    setattr(fresh, name, value)
    with pytest.raises(ImmutableVersionError), transaction.atomic():
        fresh.save(update_fields=[name])
    with pytest.raises(ImmutableVersionError), transaction.atomic():
        type(instance).objects.filter(pk=instance.pk).update(**{name: value})
    field = instance._meta.get_field(name)
    if field.attname != name:  # the attname spelling of a foreign key is refused too
        with pytest.raises(ImmutableVersionError), transaction.atomic():
            type(instance).objects.filter(pk=instance.pk).update(**{field.attname: value.pk})


def concrete_field_names(model: type[models.Model]) -> set[str]:
    return {field.name for field in model._meta.concrete_fields}


# =====================================================================================
# 1. Library rows and slot sets are immutable apart from their mutable columns
# =====================================================================================


@pytest.mark.parametrize("model", VERSIONED_MODELS, ids=lambda m: m.__name__)
def test_only_name_description_archived_and_visibility_are_mutable(model):
    """Section 3.3: the immutable list is every concrete column except the mutable four and the audit ones."""
    assert set(model.IMMUTABLE_FIELDS) == concrete_field_names(model) - set(MUTABLE_VERSION_FIELDS) - AUDIT_FIELDS
    assert set(model.CONTENT_COLUMNS) <= set(model.IMMUTABLE_FIELDS)


@pytest.mark.parametrize("model", VERSIONED_MODELS, ids=lambda m: m.__name__)
def test_every_immutable_library_column_is_refused(model):
    row = factory_for(model).create()
    for name in model.IMMUTABLE_FIELDS:
        assert_refused(row, name)
    row.refresh_from_db()
    assert model.objects.filter(pk=row.pk, content_hash=row.content_hash).exists()


@pytest.mark.parametrize("model", VERSIONED_MODELS, ids=lambda m: m.__name__)
def test_mutable_library_columns_still_change(model):
    row = factory_for(model).create()
    row.name = "renamed"
    row.description = "described"
    row.is_archived = True
    row.visibility = choices.Visibility.SHARED
    row.save()
    model.objects.filter(pk=row.pk).update(name="bulk renamed", visibility=choices.Visibility.PRIVATE)
    row.refresh_from_db()
    assert (row.name, row.description, row.is_archived, row.visibility) == (
        "bulk renamed",
        "described",
        True,
        choices.Visibility.PRIVATE,
    )


def test_the_reviewers_probe_writes_are_refused():
    profile = factories.StackProfileFactory.create()
    profile.backend_framework = "django"
    with pytest.raises(ImmutableVersionError):
        profile.save()
    with pytest.raises(ImmutableVersionError):
        StackProfile.objects.filter(pk=profile.pk).update(backend_framework="django")
    starter = factories.StarterFactory.create()
    for name, value in (("tree_hash", "f" * 64), ("stack_profile", factories.StackProfileFactory.create())):
        fresh = Starter.objects.get(pk=starter.pk)
        setattr(fresh, name, value)
        with pytest.raises(ImmutableVersionError):
            fresh.save()
    prompt_set = factories.PromptSetFactory.create()
    prompt_set.mode = choices.Mode.SLOT
    with pytest.raises(ImmutableVersionError):
        prompt_set.save()
    policy = factories.ResourcePolicyFactory.create()
    with pytest.raises(ImmutableVersionError):
        ResourcePolicy.objects.filter(pk=policy.pk).update(tier=choices.PolicyTier.GENEROUS)
    assert PromptSet.objects.get(pk=prompt_set.pk).mode == prompt_set.content["mode"]


def test_task_versions_refuse_frozen_columns_including_bulk_updates():
    version = factories.TaskSpecVersionFactory.create()
    for name in ("content", "content_hash", "structured_hash", "prose_hashes", "derived_from", "version"):
        assert_refused(version, name)
    # lint, oracle and derived stay mutable (section 3.3), also in bulk.
    TaskSpecVersion.objects.filter(pk=version.pk).update(oracle={}, lint=None, name="renamed")


def test_slot_set_parent_is_frozen():
    slot_set = factories.SlotSetFactory.create()
    for name in SlotSet.IMMUTABLE_FIELDS:
        if name == "parent_eval":
            continue  # checked below: it starts NULL
        assert_refused(slot_set, name)
    evaluation = factories.RunEvaluationFactory.create(run=slot_set.parent_run)
    with pytest.raises(ImmutableVersionError):
        SlotSet.objects.filter(pk=slot_set.pk).update(parent_eval=evaluation)
    fresh = SlotSet.objects.get(pk=slot_set.pk)
    fresh.parent_eval = evaluation
    with pytest.raises(ImmutableVersionError):
        fresh.save()
    # Name, notes and the build cache key stay editable.
    fresh = SlotSet.objects.get(pk=slot_set.pk)
    fresh.name = "renamed"
    fresh.notes = "a note"
    fresh.parent_build_key = "build-key"
    fresh.save()
    SlotSet.objects.filter(pk=slot_set.pk).update(name="bulk renamed")
    assert SlotSet.objects.get(pk=slot_set.pk).name == "bulk renamed"
    assert set(SlotSet.IMMUTABLE_FIELDS) == (
        concrete_field_names(SlotSet) - {"name", "notes", "parent_build_key"} - AUDIT_FIELDS
    )


def test_write_once_rows_refuse_bulk_updates():
    manifest = factories.RunManifestFactory.create()
    with pytest.raises(ImmutableVersionError):
        RunManifest.objects.filter(pk=manifest.pk).update(manifest_hash="f" * 64)
    entry = factories.SpendLedgerEntryFactory.create()
    with pytest.raises(ImmutableVersionError):
        SpendLedgerEntry.objects.filter(pk=entry.pk).update(amount_usd=0)
    pending = factories.RunEvaluationFactory.create()
    RunEvaluation.objects.filter(pk=pending.pk).update(status=choices.EvaluationStatus.RUNNING)
    final = factories.RunEvaluationFactory.create(measured=True)
    with pytest.raises(ImmutableVersionError):
        RunEvaluation.objects.filter(pk__in=[pending.pk, final.pk]).update(build_key="x")
    assert RunEvaluation.objects.get(pk=pending.pk).build_key == ""


def test_study_plans_refuse_bulk_updates_of_frozen_columns():
    plan = factories.StudyPlanFactory.create()
    with pytest.raises(ImmutableVersionError):
        StudyPlan.objects.filter(pk=plan.pk).update(payload_blob="f" * 64)
    StudyPlan.objects.filter(pk=plan.pk).update(status=choices.PlanStatus.SUPERSEDED)


# =====================================================================================
# 2. Library refs inside content are stored hash-pinned
# =====================================================================================


def _prompt(slug: str, **kwargs: Any) -> PromptTemplate:
    return factories.PromptTemplateFactory.create(slug=slug, content=samples.prompt_content(**kwargs))


def test_bare_refs_are_pinned_on_create_so_equal_entities_hash_alike():
    brief = _prompt("brief-a")
    bare = factories.PromptSetFactory.create(slug="set-bare", content={"mode": "app", "roles": {"brief": "brief-a@1"}})
    pinned = factories.PromptSetFactory.create(
        slug="set-pinned",
        content={"mode": "app", "roles": {"brief": brief.pinned_ref}},
    )
    assert bare.content["roles"]["brief"] == brief.pinned_ref
    assert bare.content == pinned.content
    assert bare.content_hash == pinned.content_hash
    fragment = _prompt("frag-a", role="fragment", variables=())
    stack = factories.StackProfileFactory.create(content=samples.stack_content(brief_fragment="frag-a@1"))
    assert stack.content["brief_fragment"] == fragment.pinned_ref
    starter = factories.StarterFactory.create(stack_profile=stack, content=samples.starter_content(stack.ref))
    assert starter.content["stack_profile"] == stack.pinned_ref
    starter.full_clean()


def test_unresolvable_or_mismatched_refs_are_rejected():
    _prompt("brief-b")
    with pytest.raises(ValidationError, match="does not exist"):
        factories.PromptSetFactory.create(content={"mode": "app", "roles": {"brief": "brief-missing@1"}})
    with pytest.raises(ValidationError, match="does not match"):
        factories.PromptSetFactory.create(content={"mode": "app", "roles": {"brief": "brief-b@1#" + "0" * 12}})
    # A pinned ref to a version this database does not hold is accepted (the lint reports it).
    row = factories.PromptSetFactory.create(content={"mode": "app", "roles": {"brief": "elsewhere@4#" + "0" * 12}})
    assert row.content["roles"]["brief"] == "elsewhere@4#" + "0" * 12


def test_starter_clean_requires_the_profiles_pinned_ref():
    stack = factories.StackProfileFactory.create()
    starter = factories.StarterFactory.create(stack_profile=stack)
    starter.full_clean()
    other = factories.StackProfileFactory.create(content=samples.stack_content(backend="fastapi", frontend="vue"))
    mismatched = Starter(
        slug="starter-mismatch",
        version=1,
        name="mismatch",
        content=samples.starter_content(other.pinned_ref),
        stack_profile=stack,
    )
    with pytest.raises(ValidationError, match="stack_profile"):
        mismatched.full_clean()


# =====================================================================================
# 3. Blob GC keeps nested references
# =====================================================================================


@pytest.fixture
def blob_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.GENERATION_BLOBS_ROOT = None
    return tmp_path / "media" / "blobs"


def _blobby(value: Any, key: str = "") -> Iterator[str]:
    """Every sha in a JSON value under a key that names a blob (``blob``, ``*_blob``, ``credentials_ref``)."""
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _blobby(item, str(name))
    elif isinstance(value, list):
        for item in value:
            yield from _blobby(item, key)
    elif (
        isinstance(value, str)
        and SHA_RE.match(value)
        and (key == "blob" or key.endswith("_blob") or key == "credentials_ref")
    ):
        yield value


def blob_refs_in_database() -> set[str]:
    """Every blob sha a row names: ``BLOB_FIELDS`` columns plus blob-named keys inside JSON columns."""
    from django.apps import apps

    found: set[str] = set()
    for model in apps.get_app_config("generation").get_models():
        json_fields = [field.name for field in model._meta.concrete_fields if isinstance(field, models.JSONField)]
        columns = [*getattr(model, "BLOB_FIELDS", ()), *json_fields]
        if not columns:
            continue
        for row in model._base_manager.values(*columns):
            for name in getattr(model, "BLOB_FIELDS", ()):
                if row[name]:
                    found.add(row[name])
            for name in json_fields:
                found.update(_blobby(row[name]))
    return found


def make_one_of_everything() -> None:
    """One row per factory (and per trait that writes blob-bearing JSON)."""
    for name in factories.__all__:
        if name.endswith("Factory"):
            getattr(factories, name).create()
    factories.RunEvaluationFactory.create(measured=True)
    factories.GenerationRunFactory.create(tested=True)


def test_gc_keeps_every_sha_the_factories_and_builders_write(blob_root):
    make_one_of_everything()
    # Rows whose blob references are nested: in JSON columns and inside blob payloads.
    lock_file = blobs.put_text("flask==3.1.2\n")
    lock = blobs.put_json(
        {
            "ecosystems": {"pip": [{"name": "flask", "version": "3.1.2"}]},
            "files": {"requirements.txt": lock_file},
            "resolved_at": "t",
        },
    )
    factories.GenerationRunFactory.create(tested=True, resolved_lock_blob=lock)
    stimulus = blobs.put_text("Build a todo app.")
    payload = blobs.put_json({"cells": [{"cell_key": "c0", "stimuli": [{"role": "brief", "blob": stimulus}]}]})
    factories.StudyPlanFactory.create(payload_blob=payload)
    sbom = blobs.put_json({"packages": []})
    credentials = blobs.put_bytes(b"encrypted credentials")
    adherence = samples.adherence_content() | {"sbom_blob": sbom}
    stages = {"start": {"status": "ok", "detail": {"credentials_ref": credentials}}}
    factories.RunEvaluationFactory.create(
        status=choices.EvaluationStatus.MEASURED,
        result=samples.eval_result_content(),
        adherence=adherence,
        stages=stages,
    )
    native_prompt = blobs.put_text("You are opencode.")
    trajectory = blobs.put_json({"steps": []})
    attempt = factories.RunAttemptFactory.create(
        observed_inputs={
            "system_prompt_sha": "c" * 64,
            "tool_names": ["bash"],
            "native_system_prompt_blob": native_prompt,
        },
        trajectory_blob=trajectory,
    )
    attempt.full_clean()
    control = blobs.put_text("nobody references me")

    referenced = blobs.referenced_shas()
    expected = blob_refs_in_database() | {lock_file, stimulus}
    assert expected - referenced == set()
    assert {lock, lock_file, payload, stimulus, sbom, credentials, native_prompt, trajectory} <= referenced

    old = time.time() - 30 * 86400
    for sha in (lock, lock_file, payload, stimulus, sbom, credentials, native_prompt, trajectory, control):
        path, _ = blobs.find_blob_file(sha)
        os.utime(path, (old, old))
    report = blobs.gc_blobs(dry_run=False)
    assert report.candidates == [control]
    for sha in (lock, lock_file, payload, stimulus, sbom, credentials, native_prompt, trajectory):
        assert blobs.exists(sha), sha
    assert not blobs.exists(control)


def test_unreadable_payloads_yield_no_nested_refs(blob_root):
    factories.GenerationRunFactory.create(resolved_lock_blob="e" * 64)  # no such blob file
    factories.StudyPlanFactory.create(payload_blob=blobs.put_text("not json"))
    assert list(GenerationRun.iter_blob_refs()) == []
    assert list(StudyPlan.iter_blob_refs()) == []
    assert blobs.nested_blob_refs(blobs.put_json({"files": {"a": "not-a-sha"}}), lambda p: p["files"].values()) == []


# =====================================================================================
# 4. Observed inputs and the trajectory blob are typed attempt columns
# =====================================================================================


def test_attempt_observed_inputs_are_contract_validated():
    attempt = factories.RunAttemptFactory.create()
    assert attempt.observed_inputs is None
    assert attempt.trajectory_blob == ""
    attempt.observed_inputs = {"system_prompt_sha": "c" * 64, "tool_names": ["bash", "edit"]}
    attempt.trajectory_blob = "d" * 64
    attempt.save()
    attempt.full_clean()
    assert RunAttempt.objects.filter(observed_inputs__tool_names__contains=["edit"]).get() == attempt
    attempt.observed_inputs = {"tool_names": ["bash"], "unknown_key": 1}
    with pytest.raises(ValidationError, match="observed_inputs"):
        attempt.save()
    attempt.observed_inputs = None
    attempt.trajectory_blob = "not a sha"
    with pytest.raises(ValidationError, match="trajectory_blob"):
        attempt.full_clean()
    assert "trajectory_blob" in RunAttempt.BLOB_FIELDS


def test_content_builders_stay_pinned():
    for kind, builder in samples.LIBRARY_CONTENT.items():
        content = copy.deepcopy(builder())
        assert not [
            ref
            for ref in re.findall(r"'([A-Za-z0-9][A-Za-z0-9_-]*@[1-9][0-9]*)'", repr(content))
            if ref != "jinja2-sandbox@1"
        ], kind
