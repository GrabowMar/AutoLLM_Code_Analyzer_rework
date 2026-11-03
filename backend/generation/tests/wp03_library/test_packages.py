"""Package format v4: export with dependencies and blobs, import with reuse, rename, versions and dry runs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from django.conf import settings

from backend.generation import blobs
from backend.generation.library import packages
from backend.generation.library import service
from backend.generation.library.service import LibraryError
from backend.generation.models import PromptTemplate
from backend.generation.models import StackProfile
from backend.generation.testing import content as builders

pytestmark = pytest.mark.django_db


def alice_stack(alice, golden):
    fragment = service.create(
        "prompt",
        slug="alice-frag",
        name="Alice fragment",
        content=builders.prompt_content(role="fragment", template="Use Flask.", variables=(), modes=("app",)),
        user=alice,
    )
    stack = service.create(
        "stack",
        slug="alice-stack",
        name="Alice stack",
        content=builders.stack_content(brief_fragment=fragment.ref),
        user=alice,
    )
    return fragment, stack


def round_trip(package):
    return packages.parse_package(packages.dump_package(package))


class TestExport:
    def test_dependencies_in_order(self, golden, alice):
        package = packages.export_package(["prompt_set:app-default@1"], user=alice)
        kinds = [(entity["kind"], entity["slug"]) for entity in package["entities"]]
        assert kinds[-1] == ("prompt_set", "app-default")
        assert {slug for kind, slug in kinds if kind == "prompt"} == {
            "brief-app-default",
            "auth-contract-contract_bearer",
            "auth-contract-cookie_session",
            "auth-contract-spec_defined",
            "network-none",
            "network-packages",
            "budget-neutral",
        }
        assert package["kind"] == packages.PACKAGE_KIND
        assert package["package_schema_version"] == 4
        assert package["blobs"] == []

    def test_starters_carry_their_blobs(self, golden, alice):
        package = packages.export_package(["starter:flask-react-minimal@1"], user=alice)
        starter = next(e for e in package["entities"] if e["kind"] == "starter")
        shas = {entry["blob"] for entry in starter["content"]["files"].values()}
        assert {item["sha256"] for item in package["blobs"]} == shas
        assert [e["kind"] for e in package["entities"]] == ["prompt", "stack", "starter"]

    def test_without_dependencies(self, golden, alice):
        package = packages.export_package(["prompt_set:app-default@1"], user=alice, include_dependencies=False)
        assert [e["slug"] for e in package["entities"]] == ["app-default"]

    def test_requested_refs_must_be_visible(self, golden, alice, bob):
        alice_stack(alice, golden)
        with pytest.raises(LibraryError) as excinfo:
            packages.export_package(["stack:alice-stack@1"], user=bob)
        assert excinfo.value.status_code == 404
        with pytest.raises(LibraryError):
            packages.export_package(["alice-stack@1"], user=alice)  # no kind

    def test_invisible_dependencies_are_omitted(self, golden, alice, bob):
        fragment, stack = alice_stack(alice, golden)
        service.set_visibility(stack, "shared", user=alice)
        package = packages.export_package(["stack:alice-stack@1"], user=bob)
        assert [e["slug"] for e in package["entities"]] == ["alice-stack"]
        assert package["omitted"] == [
            {"ref": f"prompt:{fragment.pinned_ref}", "reason": "missing or not visible to you"},
        ]


class TestImport:
    def test_round_trip_into_another_account_renames_conflicts_and_remaps_refs(self, golden, alice, bob):
        _, stack = alice_stack(alice, golden)
        package = round_trip(packages.export_package(["stack:alice-stack@1"], user=alice))
        result = packages.import_package(package, user=bob, mode="apply")
        assert [c["resolution"] for c in result.conflicts] == [
            "renamed to alice-frag-imported",
            "renamed to alice-stack-imported",
        ]
        imported = service.latest("stack", "alice-stack-imported")
        new_fragment = service.latest("prompt", "alice-frag-imported")
        assert imported.created_by == bob
        assert imported.content["brief_fragment"] == new_fragment.pinned_ref
        assert {k: v for k, v in imported.content.items() if k != "brief_fragment"} == {
            k: v for k, v in stack.content.items() if k != "brief_fragment"
        }
        assert new_fragment.content == service.latest("prompt", "alice-frag").content

    def test_identical_content_is_reused(self, golden, alice, bob):
        package = packages.export_package(["prompt_set:app-default@1"], user=alice)
        result = packages.import_package(package, user=bob, mode="apply")
        assert result.created == []
        assert result.conflicts == []
        assert len(result.reused) == 8
        assert PromptTemplate.objects.count() == len(golden.rows["prompt"])

    def test_own_slugs_get_a_new_version(self, golden, alice):
        _, stack = alice_stack(alice, golden)
        package = packages.export_package(["stack:alice-stack@1"], user=alice)
        stack_entity = next(e for e in package["entities"] if e["kind"] == "stack")
        stack_entity["content"]["database"] = "postgres"
        stack_entity["content_hash"] = StackProfile.compute_content_hash(stack_entity["content"])
        result = packages.import_package(package, user=alice, mode="apply")
        assert [c["as"].split("#")[0] for c in result.created] == ["alice-stack@2"]
        assert [r["kind"] for r in result.reused] == ["prompt"]
        assert service.latest("stack", "alice-stack").derived_from == stack

    def test_blobs_are_restored(self, golden, alice, bob):
        package = round_trip(packages.export_package(["starter:flask-react-minimal@1"], user=alice))
        shutil.rmtree(Path(settings.GENERATION_BLOBS_ROOT))
        sha = package["blobs"][0]["sha256"]
        assert not blobs.exists(sha)
        result = packages.import_package(package, user=bob, mode="apply")
        assert blobs.exists(sha)
        assert [r["kind"] for r in result.reused] == ["prompt", "stack", "starter"]

    def test_dry_run_changes_nothing(self, golden, alice, bob):
        alice_stack(alice, golden)
        package = packages.export_package(["stack:alice-stack@1"], user=alice)
        before = StackProfile.objects.count()
        result = packages.import_package(package, user=bob, mode="dry_run")
        assert len(result.created) == 2
        assert result.mode == "dry_run"
        assert StackProfile.objects.count() == before

    def test_on_conflict_error_refuses_everything(self, golden, alice, bob):
        alice_stack(alice, golden)
        package = packages.export_package(["stack:alice-stack@1"], user=alice)
        before = (PromptTemplate.objects.count(), StackProfile.objects.count())
        with pytest.raises(LibraryError) as excinfo:
            packages.import_package(package, user=bob, mode="apply", on_conflict="error")
        assert excinfo.value.status_code == 409
        assert [c["resolution"] for c in excinfo.value.result.conflicts] == ["refused", "refused"]
        assert (PromptTemplate.objects.count(), StackProfile.objects.count()) == before

    def test_a_new_slug_starts_at_v1(self, golden, alice, bob):
        alice_stack(alice, golden)
        package = packages.export_package(["stack:alice-stack@1"], user=alice)
        for entity in package["entities"]:
            entity["slug"] = f"bob-{entity['slug']}"
        result = packages.import_package(round_trip(package), user=bob, mode="apply")
        assert [c["as"].split("#")[0] for c in result.created] == ["bob-alice-frag@1", "bob-alice-stack@1"]

    def test_an_imported_resource_policy_is_custom(self, golden, alice):
        frugal = golden.get("resource_policy", "frugal")
        assert frugal.tier == "frugal"
        package = packages.export_package([f"resource_policy:{frugal.ref}"], user=alice)
        (entity,) = package["entities"]
        assert entity.get("tier") == "frugal"
        entity["slug"] = "alice-frugal"
        entity["content"]["run"]["max_turns"] = 41
        entity["content_hash"] = service.content_hash_of("resource_policy", entity["content"])
        result = packages.import_package(round_trip(package), user=alice, mode="apply")
        (created,) = result.created
        row = service.resolve_ref(created["as"], kind="resource_policy", user=alice)
        assert (row.tier, row.is_system, row.created_by) == ("custom", False, alice)

    def test_an_unchanged_preset_is_reused_not_copied(self, golden, alice):
        frugal = golden.get("resource_policy", "frugal")
        package = packages.export_package([f"resource_policy:{frugal.ref}"], user=alice)
        result = packages.import_package(round_trip(package), user=alice, mode="apply")
        assert result.created == []
        assert result.reused[0]["as"] == frugal.pinned_ref


class TestValidation:
    def test_tampered_content_is_refused(self, golden, alice):
        package = packages.export_package(["prompt:brief-app-default@1"], user=alice)
        package["entities"][0]["content"]["template"] += " (edited)"
        with pytest.raises(LibraryError) as excinfo:
            packages.import_package(package, user=alice, mode="apply")
        assert excinfo.value.status_code == 400

    def test_tampered_blob_is_refused(self, golden, alice):
        package = packages.export_package(["starter:flask-react-minimal@1"], user=alice)
        package["blobs"][0]["data"] = "aGVsbG8="
        with pytest.raises(LibraryError):
            packages.import_package(package, user=alice)

    @pytest.mark.parametrize(
        "text",
        [
            "not: [valid",
            "kind: llm-lab-template-package\ntemplate_package_schema_version: 3\nassets: {}\n",
            "kind: llm-lab-library-package\npackage_schema_version: 3\n",
            "kind: llm-lab-library-package\npackage_schema_version: 4\nentities: [{kind: nope}]\n",
        ],
    )
    def test_malformed_packages(self, text):
        with pytest.raises(LibraryError) as excinfo:
            packages.parse_package(text)
        assert excinfo.value.status_code == 400

    def test_bad_mode(self, alice):
        with pytest.raises(LibraryError):
            packages.import_package({"kind": packages.PACKAGE_KIND}, user=alice, mode="maybe")

    def test_the_yaml_is_plain_data(self, golden, alice):
        text = packages.dump_package(packages.export_package(["prompt:budget-neutral@1"], user=alice))
        assert yaml.safe_load(text)["entities"][0]["slug"] == "budget-neutral"
