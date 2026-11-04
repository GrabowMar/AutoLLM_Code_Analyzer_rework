"""Library service: versioning, visibility, archive, fork, override forks, usage, refs and diff."""

from __future__ import annotations

import pytest

from backend.generation.contracts.resources import preset_content
from backend.generation.library import service
from backend.generation.library.service import LibraryError
from backend.generation.models import PromptSet
from backend.generation.models import PromptTemplate
from backend.generation.models import ResourcePolicy
from backend.generation.models import StackProfile
from backend.generation.models import StudyPromptOverride
from backend.generation.models import TaskSpec
from backend.generation.testing import content as builders
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import StudyFactory

pytestmark = pytest.mark.django_db


def prompt(user, slug="my-brief", template="Build {{ task.title }}.", **kwargs):
    return service.create(
        "prompt",
        slug=slug,
        name=slug,
        content=builders.prompt_content(template=template),
        user=user,
        **kwargs,
    )


def stack(user, golden, slug="my-stack"):
    return service.create(
        "stack",
        slug=slug,
        name="My stack",
        content=builders.stack_content(brief_fragment="stack-flask-react@1"),
        user=user,
    )


class TestCreate:
    def test_v1_owned_and_private(self, alice):
        row = prompt(alice)
        assert (row.version, row.created_by, row.visibility, row.is_system) == (1, alice, "private", False)
        assert row.content_hash == PromptTemplate.compute_content_hash(row.content)

    def test_slug_taken(self, alice, bob):
        prompt(alice)
        with pytest.raises(LibraryError) as excinfo:
            prompt(bob)
        assert (excinfo.value.status_code, excinfo.value.code) == (409, "slug_exists")

    def test_bare_refs_are_pinned(self, golden, alice):
        row = stack(alice, golden)
        assert row.content["brief_fragment"] == golden.pinned_ref("prompt", "stack-flask-react")

    def test_unknown_ref(self, alice):
        with pytest.raises(LibraryError) as excinfo:
            service.create(
                "stack",
                slug="s",
                name="s",
                content=builders.stack_content(brief_fragment="nope@1"),
                user=alice,
            )
        assert excinfo.value.status_code == 422
        assert excinfo.value.issues[0].code == "lint_unknown_library_ref"

    def test_lint_errors_refuse_the_create(self, alice):
        with pytest.raises(LibraryError) as excinfo:
            prompt(alice, template="{{ style.system }}")
        assert [issue.code for issue in excinfo.value.issues] == ["lint_undeclared_variable", "lint_unused_variable"]

    def test_invalid_content(self, alice):
        with pytest.raises(LibraryError) as excinfo:
            service.create("prompt", slug="x", name="x", content={"role": "brief"}, user=alice)
        assert excinfo.value.status_code == 422
        assert {issue.code for issue in excinfo.value.issues} == {"lint_content_invalid"}

    def test_invalid_slug(self, alice):
        with pytest.raises(LibraryError):
            prompt(alice, slug="bad slug!")

    def test_task_header_and_derived(self, alice):
        content = builders.task_content()
        row = service.create("task", slug="todo", name="Todo", content=content, user=alice)
        assert row.task.slug == "todo"
        assert row.task.latest_version == 1
        assert row.task.created_by == alice
        assert row.derived["endpoint_count"] == 5
        assert row.derived["size_bucket"] == "M"
        assert row.derived["check_statedness"] == {"todos.sorted": ["exhaustive"]}
        assert row.lint["linter_version"] == 1


class TestNewVersion:
    def test_new_version_is_derived_from_the_base(self, alice):
        v1 = prompt(alice)
        v2 = service.new_version(
            v1,
            base_version=1,
            content=builders.prompt_content(template="Make {{ task.title }}."),
            user=alice,
        )
        assert (v2.version, v2.derived_from, v2.name) == (2, v1, v1.name)
        v1.refresh_from_db()
        assert v1.content["template"] == "Build {{ task.title }}."

    def test_stale_base(self, alice):
        v1 = prompt(alice)
        service.new_version(
            v1,
            base_version=1,
            content=builders.prompt_content(template="A {{ task.title }}"),
            user=alice,
        )
        with pytest.raises(LibraryError) as excinfo:
            service.new_version(
                v1,
                base_version=1,
                content=builders.prompt_content(template="B {{ task.title }}"),
                user=alice,
            )
        assert (excinfo.value.status_code, excinfo.value.code) == (409, "stale_base")

    def test_same_hash_is_unchanged(self, alice):
        v1 = prompt(alice)
        same = service.new_version(v1, base_version=1, content=dict(v1.content), user=alice)
        assert same.pk == v1.pk
        assert PromptTemplate.objects.filter(slug="my-brief").count() == 1

    def test_only_the_owner_may_version(self, alice, bob):
        v1 = prompt(alice)
        with pytest.raises(LibraryError) as excinfo:
            service.new_version(v1, base_version=1, content=builders.prompt_content(template="x {{ task }}"), user=bob)
        assert excinfo.value.status_code == 403

    def test_system_slugs_are_versioned_by_the_seeder_only(self, golden, staff):
        brief = golden.get("prompt", "brief-app-default")
        with pytest.raises(LibraryError) as excinfo:
            service.new_version(brief, base_version=1, content=dict(brief.content), user=staff)
        assert excinfo.value.status_code == 403
        v2 = service.new_version(
            brief,
            base_version=1,
            content={**brief.content, "template": brief.content["template"] + "\nMore."},
            user=None,
            system=True,
        )
        assert v2.is_system
        assert v2.visibility == "system"
        assert v2.version == 2

    def test_task_versions_move_the_header(self, alice):
        v1 = service.create("task", slug="todo", name="Todo", content=builders.task_content(), user=alice)
        v2 = service.new_version(v1, base_version=1, content=builders.task_content(title="Todos"), user=alice)
        assert v2.version == 2
        assert v2.derived_from == v1
        assert TaskSpec.objects.get(slug="todo").latest_version == 2


class TestVisibility:
    def test_private_shared_system(self, golden, alice, bob, staff):
        private = prompt(alice, slug="mine")
        shared = prompt(alice, slug="ours", visibility="shared")
        system = golden.get("prompt", "brief-app-default")
        visible_to_bob = set(service.visible("prompt", bob).values_list("slug", flat=True))
        assert "ours" in visible_to_bob
        assert "brief-app-default" in visible_to_bob
        assert "mine" not in visible_to_bob
        assert service.can_view(private, alice)
        assert not service.can_view(private, bob)
        assert service.can_view(private, staff)
        assert service.can_view(shared, bob)
        assert service.can_view(system, None)
        with pytest.raises(LibraryError) as excinfo:
            service.resolve_ref(private.ref, kind="prompt", user=bob)
        assert excinfo.value.status_code == 404

    def test_set_visibility(self, alice, bob):
        row = prompt(alice)
        service.set_visibility(row, "shared", user=alice)
        assert service.can_view(row, bob)
        with pytest.raises(LibraryError):
            service.set_visibility(row, "private", user=bob)
        with pytest.raises(LibraryError):
            service.set_visibility(row, "system", user=alice)


class TestRefs:
    def test_resolve_forms(self, golden):
        brief = golden.get("prompt", "brief-app-default")
        assert service.resolve_ref("prompt:brief-app-default@1") == brief
        assert service.resolve_ref(brief.pinned_ref, kind="prompt") == brief
        with pytest.raises(LibraryError) as excinfo:
            service.resolve_ref("brief-app-default@1#000000000000", kind="prompt")
        assert (excinfo.value.status_code, excinfo.value.code) == (409, "hash_mismatch")
        with pytest.raises(LibraryError):
            service.resolve_ref("brief-app-default@1")  # no kind
        with pytest.raises(LibraryError):
            service.resolve_ref("not a ref", kind="prompt")

    def test_latest_skips_archived(self, alice):
        v1 = prompt(alice)
        v2 = service.new_version(
            v1,
            base_version=1,
            content=builders.prompt_content(template="v2 {{ task }}"),
            user=alice,
        )
        assert service.latest("prompt", "my-brief") == v2
        service.archive(v2, archived=True, user=alice, all_versions=False)
        assert service.latest("prompt", "my-brief") == v1
        service.archive(v1, archived=True, user=alice)
        with pytest.raises(LibraryError):
            service.latest("prompt", "my-brief")


class TestArchive:
    def test_whole_slug_and_permissions(self, golden, alice, bob, staff):
        row = prompt(alice)
        with pytest.raises(LibraryError):
            service.archive(row, user=bob)
        service.archive(row, user=alice)
        assert PromptTemplate.objects.get(slug="my-brief").is_archived
        system = golden.get("prompt", "brief-app-default")
        with pytest.raises(LibraryError):
            service.archive(system, user=alice)
        service.archive(system, user=staff)
        assert PromptTemplate.objects.get(slug="brief-app-default").is_archived

    def test_tasks_archive_their_header(self, golden, staff):
        task = golden.get("task", "crud_todo_list")
        service.archive(task, user=staff)
        assert TaskSpec.objects.get(slug="crud_todo_list").is_archived


class TestFork:
    def test_fork_a_system_entity(self, golden, alice):
        source = golden.get("stack", "flask-react")
        forked = service.fork(source, new_slug="my-flask", name="Mine", user=alice)
        assert (forked.version, forked.created_by, forked.derived_from) == (1, alice, source)
        assert forked.content == source.content
        assert forked.content_hash == source.content_hash

    def test_forked_budget_presets_are_custom(self, golden, alice):
        forked = service.fork(golden.get("resource_policy", "frugal"), new_slug="cheap", name="Cheap", user=alice)
        assert ResourcePolicy.objects.get(pk=forked.pk).tier == "custom"

    def test_forked_task(self, golden, alice):
        forked = service.fork(golden.get("task", "crud_todo_list"), new_slug="my-todo", name="My todo", user=alice)
        assert forked.task.created_by == alice
        assert forked.derived_from is not None

    def test_invisible_sources_cannot_be_forked(self, alice, bob):
        with pytest.raises(LibraryError) as excinfo:
            service.fork(prompt(alice), new_slug="stolen", name="x", user=bob)
        assert excinfo.value.status_code == 404


class TestForkForOverride:
    def test_prompt_override_forks_the_template_and_the_prompt_set(self, golden, alice):
        prompt_set = golden.get("prompt_set", "app-default")
        brief = golden.get("prompt", "brief-app-default")
        new_text = brief.content["template"] + "\nKeep it small."
        result = service.fork_for_override(
            "prompt",
            brief.pinned_ref,
            new_text,
            user=alice,
            prompt_set_ref=prompt_set.pinned_ref,
        )
        new_brief = service.resolve_ref(result.owner_ref_new, kind="prompt")
        new_set = service.resolve_ref(result.level_new, kind="prompt_set")
        assert new_brief.derived_from == brief
        assert new_brief.created_by == alice
        assert not new_brief.is_system
        assert new_brief.content["template"] == new_text
        assert new_set.derived_from == prompt_set
        assert new_set.content["roles"]["brief"] == new_brief.pinned_ref
        assert new_set.content["fragments"] == prompt_set.content["fragments"]
        assert (result.swapped_factor, result.level_base) == ("prompt_set", prompt_set.pinned_ref)
        assert "+Keep it small." in result.diff
        assert len(result.created) == 2
        override = StudyPromptOverride(study=StudyFactory.create(created_by=alice), **result.fields())
        override.full_clean()
        # The same edit again reuses both forks.
        again = service.fork_for_override(
            "prompt",
            brief.pinned_ref,
            new_text,
            user=alice,
            prompt_set_ref=prompt_set.pinned_ref,
        )
        assert (again.owner_ref_new, again.level_new, again.created) == (result.owner_ref_new, result.level_new, ())

    def test_own_entities_get_a_new_version(self, golden, alice):
        brief = service.fork(golden.get("prompt", "brief-app-default"), new_slug="my-brief", name="b", user=alice)
        set_content = {**golden.get("prompt_set", "app-default").content, "roles": {"brief": brief.pinned_ref}}
        my_set = service.create("prompt_set", slug="my-set", name="s", content=set_content, user=alice)
        result = service.fork_for_override(
            "prompt",
            brief.pinned_ref,
            brief.content["template"] + "\nv2",
            user=alice,
            prompt_set_ref=my_set.pinned_ref,
        )
        assert result.owner_ref_new.startswith("my-brief@2#")
        assert result.level_new.startswith("my-set@2#")

    def test_auth_fragment_override(self, golden, alice):
        prompt_set = golden.get("prompt_set", "app-default")
        fragment = golden.get("prompt", "auth-contract-cookie_session")
        result = service.fork_for_override(
            "auth_fragment",
            fragment.pinned_ref,
            "Authentication: a session cookie.",
            user=alice,
            prompt_set_ref=prompt_set.pinned_ref,
            role="cookie_session",
        )
        new_set = service.resolve_ref(result.level_new, kind="prompt_set")
        assert new_set.content["fragments"]["auth_contract.cookie_session"] == result.owner_ref_new
        assert (
            new_set.content["fragments"]["auth_contract.contract_bearer"]
            == prompt_set.content["fragments"]["auth_contract.contract_bearer"]
        )
        assert result.role == "cookie_session"
        StudyPromptOverride(study=StudyFactory.create(created_by=alice), **result.fields()).full_clean()
        with pytest.raises(LibraryError):  # the fragment of another mode
            service.fork_for_override(
                "auth_fragment",
                fragment.pinned_ref,
                "x",
                user=alice,
                prompt_set_ref=prompt_set.pinned_ref,
                role="contract_bearer",
            )

    @pytest.mark.parametrize(
        ("owner_kind", "slug", "field"),
        [
            ("stack", "flask-react", "brief_fragment"),
            ("style", "tailwind", "fragment"),
            ("guidance", "generic", "fragment"),
        ],
    )
    def test_fragment_owners(self, golden, alice, owner_kind, slug, field):
        owner = golden.get(owner_kind, slug)
        result = service.fork_for_override(owner_kind, owner.pinned_ref, "Edited fragment.", user=alice)
        new_owner = service.resolve_ref(result.owner_ref_new, kind=owner_kind)
        new_fragment = service.resolve_ref(new_owner.content[field], kind="prompt")
        assert new_owner.derived_from == owner
        assert new_fragment.content["template"] == "Edited fragment."
        assert new_fragment.derived_from == service.resolve_ref(owner.content[field], kind="prompt")
        assert result.level_new == result.owner_ref_new

    def test_prompt_set_override_takes_content(self, golden, alice):
        prompt_set = golden.get("prompt_set", "app-default")
        text = (
            "{" + f'"mode": "app", "roles": {prompt_set.content["roles"]!r}, "system_prompt_mode": "append", '
            f'"fragments": {prompt_set.content["fragments"]!r}' + "}"
        )
        result = service.fork_for_override("prompt_set", prompt_set.pinned_ref, text.replace("'", '"'), user=alice)
        new_set = PromptSet.objects.get(
            slug=result.level_new.split("@")[0],
            version=int(result.level_new.split("@")[1].split("#")[0]),
        )
        assert new_set.content["system_prompt_mode"] == "append"

    def test_the_same_edit_by_two_users_gives_each_their_own_fork(self, golden, alice, bob):
        style = golden.get("style", "tailwind")
        first = service.fork_for_override("style", style.pinned_ref, "Edited same.", user=alice)
        second = service.fork_for_override("style", style.pinned_ref, "Edited same.", user=bob)
        assert second.level_new != first.level_new
        assert second.created  # nothing of Alice's is reused
        for result, user in ((first, alice), (second, bob)):
            row = service.resolve_ref(result.level_new, kind="style", user=user)
            fragment = service.resolve_ref(row.content["fragment"], kind="prompt", user=user)
            assert (row.created_by, fragment.created_by) == (user, user)
            assert service.can_view(row, user)
            assert service.can_view(fragment, user)
        # The fragment edit is the same text, so Bob's template fork gets the user-scoped slug.
        bob_style = service.resolve_ref(second.level_new, kind="style", user=bob)
        assert bob_style.content["fragment"].split("@")[0].endswith(f"-u{bob.pk}")
        # Each user's repeat reuses their own fork.
        again = service.fork_for_override("style", style.pinned_ref, "Edited same.", user=bob)
        assert (again.level_new, again.created) == (second.level_new, ())
        assert service.fork_for_override("style", style.pinned_ref, "Edited same.", user=alice).created == ()

    def test_a_shared_fork_of_another_user_is_not_reused(self, golden, alice, bob):
        prompt_set = golden.get("prompt_set", "app-default")
        brief = golden.get("prompt", "brief-app-default")
        text = brief.content["template"] + "\nShared edit."
        first = service.fork_for_override(
            "prompt",
            brief.pinned_ref,
            text,
            user=alice,
            prompt_set_ref=prompt_set.pinned_ref,
        )
        for ref, kind in ((first.owner_ref_new, "prompt"), (first.level_new, "prompt_set")):
            row = service.resolve_ref(ref, kind=kind)
            row.visibility = "shared"
            row.save(update_fields=["visibility"])
        second = service.fork_for_override(
            "prompt",
            brief.pinned_ref,
            text,
            user=bob,
            prompt_set_ref=prompt_set.pinned_ref,
        )
        new_set = service.resolve_ref(second.level_new, kind="prompt_set", user=bob)
        assert new_set.created_by == bob
        assert service.resolve_ref(new_set.content["roles"]["brief"], kind="prompt", user=bob).created_by == bob

    def test_guidance_none_has_no_fragment(self, golden, alice):
        with pytest.raises(LibraryError):
            service.fork_for_override("guidance", golden.pinned_ref("guidance", "none"), "x", user=alice)


class TestUsage:
    def _design(self, golden, **factor_levels):
        design = builders.design_content()
        design["factors"] = [{"key": key, "role": "fixed", "levels": levels} for key, levels in factor_levels.items()]
        return design

    def test_studies_and_runs(self, golden, alice, bob):
        stack_row = golden.get("stack", "flask-react")
        design = builders.design_content()
        design["factors"].append({"key": "stack_profile", "role": "fixed", "levels": [stack_row.pinned_ref]})
        study = StudyFactory.create(created_by=alice, design=design)
        StudyFactory.create(created_by=bob, design=design)
        GenerationRunFactory.create(stack_profile=stack_row)
        GenerationRunFactory.create(stack_profile=stack_row)
        usage = service.usage(stack_row, user=alice)
        assert usage["runs"] == 2
        assert usage["studies"] == [{"id": str(study.id), "name": study.name, "status": study.status, "version": 1}]

    def test_prompt_usage_goes_through_referencing_entities(self, golden, alice):
        prompt_set = golden.get("prompt_set", "app-default")
        design = builders.design_content()
        design["factors"].append({"key": "prompt_set", "role": "fixed", "levels": [prompt_set.pinned_ref]})
        StudyFactory.create(created_by=alice, design=design)
        GenerationRunFactory.create(prompt_set=prompt_set)
        usage = service.usage(golden.get("prompt", "auth-contract-spec_defined"), user=alice)
        assert usage["runs"] == 1
        assert len(usage["studies"]) == 1
        fragment_usage = service.usage(golden.get("prompt", "stack-flask-react"), user=alice)
        assert fragment_usage == {"studies": [], "runs": 0}

    def test_unused(self, alice):
        assert service.usage(prompt(alice), user=alice) == {"studies": [], "runs": 0}


def test_content_diff(alice):
    v1 = prompt(alice)
    v2 = service.new_version(
        v1,
        base_version=1,
        content=builders.prompt_content(template="Make {{ task.title }}."),
        user=alice,
    )
    diff = service.content_diff(v1, v2)
    assert "-Build {{ task.title }}." in diff["unified"]
    assert "+Make {{ task.title }}." in diff["unified"]
    assert diff["structured"] == [{"path": "template", "a": "Build {{ task.title }}.", "b": "Make {{ task.title }}."}]


def test_validate_content(golden):
    result = service.validate_content("stack", builders.stack_content(brief_fragment="stack-flask-react@1"))
    assert result["ok"]
    assert result["content_hash"] == StackProfile.compute_content_hash(result["content"])
    bad = service.validate_content("stack", builders.stack_content(brief_fragment="missing@3"))
    assert not bad["ok"]
    assert bad["issues"][0].code == "lint_unknown_library_ref"
    task = service.validate_content("task", builders.task_content())
    assert task["derived"]["endpoint_count"] == 5


def test_policy_content_is_used_verbatim(golden, alice):
    content = preset_content("standard").model_dump(mode="json")
    row = service.create("resource_policy", slug="std-copy", name="Std", content=content, user=alice)
    assert row.tier == "custom"
    assert row.content == content


class TestOverrideCallerCompatibility:
    """The shape WP-10's ``study/overrides.py`` calls: no prompt_set_ref, no role, ``level_ref_*`` fields."""

    def test_prompt_set_is_found_when_unique(self, golden, alice):
        brief = golden.get("prompt", "brief-app-default")
        result = service.fork_for_override("prompt", brief.pinned_ref, brief.content["template"] + "\nX", user=alice)
        assert result.level_ref_base == golden.pinned_ref("prompt_set", "app-default")
        assert result.level_ref_new == result.level_new

    def test_auth_mode_is_inferred(self, golden, alice):
        fragment = golden.get("prompt", "auth-contract-spec_defined")
        result = service.fork_for_override("auth_fragment", fragment.pinned_ref, "Follow the task's flow.", user=alice)
        assert result.role == "spec_defined"

    def test_ambiguous_prompt_sets_need_a_ref(self, golden, alice):
        prompt_set = golden.get("prompt_set", "app-default")
        service.fork(prompt_set, new_slug="copy-set", name="Copy", user=alice)
        brief = golden.get("prompt", "brief-app-default")
        with pytest.raises(LibraryError) as excinfo:
            service.fork_for_override("prompt", brief.pinned_ref, "x {{ task.title }}", user=alice)
        assert "pass prompt_set_ref" in excinfo.value.message
