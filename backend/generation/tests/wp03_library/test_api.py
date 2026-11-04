"""The 9.1 library and 9.2 prompts endpoints (schemas, visibility, errors)."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from backend.generation.api import router
from backend.generation.contracts.manifest import SourceSpan
from backend.generation.library import packages
from backend.generation.prompts.engine import RenderedText
from backend.generation.testing import content as builders
from backend.generation.tests.wp03_library.test_engine import assert_partition

pytestmark = pytest.mark.django_db

API = "/api/generation"


def post(client, path, body):
    return client.post(f"{API}{path}", data=json.dumps(body), content_type="application/json")


@pytest.fixture
def as_alice(client, alice):
    client.force_login(alice)
    return client


def create_prompt(client, slug="my-brief", template="Build {{ task.title }}.", **extra):
    return post(
        client,
        "/library/prompts/",
        {"slug": slug, "name": slug, "content": builders.prompt_content(template=template), **extra},
    )


def test_operation_ids_are_published():
    ids = set()
    for _prefix, sub, *_ in router._routers:
        for path_ops in sub.path_operations.values():
            ids.update(op.operation_id for op in path_ops.operations)
    assert {
        "library_list",
        "library_create",
        "library_validate",
        "library_detail",
        "library_version_get",
        "library_version_create",
        "library_archive",
        "library_visibility",
        "library_usage",
        "library_diff",
        "library_fork",
        "library_task_oracle_get",
        "library_task_oracle_queue",
        "library_starter_tree",
        "library_blob",
        "library_package_export",
        "library_package_import",
        "prompts_variables",
        "prompts_render",
        "prompts_diff",
    } <= ids


class TestLibrary:
    def test_list_latest_per_slug_with_filters(self, golden, as_alice, alice):
        create_prompt(as_alice)
        post(
            as_alice,
            "/library/prompts/my-brief/versions/",
            {"base_version": 1, "content": builders.prompt_content(template="v2 {{ task.title }}")},
        )
        body = as_alice.get(f"{API}/library/prompts/?page_size=200").json()
        mine = next(item for item in body["items"] if item["slug"] == "my-brief")
        assert mine["version"] == 2
        assert body["total"] == len(golden.rows["prompt"]) + 1
        assert as_alice.get(f"{API}/library/prompts/?mine=true").json()["total"] == 1
        assert [i["slug"] for i in as_alice.get(f"{API}/library/prompts/?q=network").json()["items"]] == [
            "network-none",
            "network-packages",
        ]
        assert {i["slug"] for i in as_alice.get(f"{API}/library/prompts/?tag=slot_agent_brief").json()["items"]} == {
            "slot-agent-brief",
        }
        page = as_alice.get(f"{API}/library/prompts/?page=2&page_size=5").json()
        assert (page["page"], page["page_size"], len(page["items"])) == (2, 5, 5)
        assert as_alice.get(f"{API}/library/prompts/?page_size=500").status_code == 400
        assert as_alice.get(f"{API}/library/nonsense/").status_code == 404

    def test_item_shape(self, golden, as_alice):
        item = as_alice.get(f"{API}/library/stacks/flask-react/versions/1/").json()
        assert set(item) >= {
            "kind",
            "slug",
            "version",
            "name",
            "description",
            "schema_version",
            "content",
            "content_hash",
            "pinned_ref",
            "derived_from",
            "is_system",
            "is_archived",
            "visibility",
            "created_by",
            "created_at",
            "tags",
            "columns",
            "lint",
        }
        assert item["kind"] == "stack"
        assert item["pinned_ref"] == golden.pinned_ref("stack", "flask-react")
        assert item["columns"] == {"backend_framework": "flask", "frontend_framework": "react", "database": "sqlite"}
        task = as_alice.get(f"{API}/library/tasks/crud_todo_list/versions/1/").json()
        assert task["kind"] == "task"
        assert task["columns"]["structured_hash"]
        assert task["description"].startswith("Users register")

    def test_create_version_stale_and_unchanged(self, as_alice):
        created = create_prompt(as_alice).json()
        assert created["version"] == 1
        assert created["created_by"]["username"]
        assert create_prompt(as_alice).status_code == 409
        new = post(
            as_alice,
            "/library/prompts/my-brief/versions/",
            {"base_version": 1, "content": builders.prompt_content(template="v2 {{ task.title }}")},
        )
        assert new.status_code == 200
        assert new.json()["version"] == 2
        assert new.json()["unchanged"] is False
        stale = post(
            as_alice,
            "/library/prompts/my-brief/versions/",
            {"base_version": 1, "content": builders.prompt_content()},
        )
        assert (stale.status_code, stale.json()["code"]) == (409, "stale_base")
        same = post(
            as_alice,
            "/library/prompts/my-brief/versions/",
            {"base_version": 2, "content": new.json()["content"]},
        )
        assert same.json()["unchanged"] is True
        assert same.json()["version"] == 2

    def test_lint_errors_are_422_with_issues(self, as_alice):
        response = create_prompt(as_alice, template="{{ nope }}")
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "invalid"
        assert body["issues"][0]["code"] == "lint_undeclared_variable"

    def test_detail_usage_diff_archive_visibility(self, golden, as_alice, client, bob):
        create_prompt(as_alice)
        post(
            as_alice,
            "/library/prompts/my-brief/versions/",
            {"base_version": 1, "content": builders.prompt_content(template="v2 {{ task.title }}")},
        )
        detail = as_alice.get(f"{API}/library/prompts/my-brief/").json()
        assert detail["latest"]["version"] == 2
        assert [v["version"] for v in detail["versions"]] == [2, 1]
        assert as_alice.get(f"{API}/library/prompts/my-brief/usage/").json() == {"studies": [], "runs": 0}
        diff = as_alice.get(f"{API}/library/prompts/my-brief/diff/?a=1&b=2").json()
        assert diff["structured"][0]["path"] == "template"
        assert "+v2" in diff["unified"]
        archived = post(as_alice, "/library/prompts/my-brief/archive/", {"archived": True}).json()
        assert archived["is_archived"] is True
        assert as_alice.get(f"{API}/library/prompts/?mine=true").json()["total"] == 0
        assert as_alice.get(f"{API}/library/prompts/?mine=true&archived=true").json()["total"] == 1
        client.force_login(bob)
        assert client.get(f"{API}/library/prompts/my-brief/").status_code == 404

    def test_sharing(self, as_alice, client, alice, bob):
        create_prompt(as_alice)
        assert (
            post(as_alice, "/library/prompts/my-brief/visibility/", {"visibility": "shared"}).json()["visibility"]
            == "shared"
        )
        client.force_login(bob)
        assert client.get(f"{API}/library/prompts/my-brief/").status_code == 200
        forbidden = post(
            client,
            "/library/prompts/my-brief/versions/",
            {"base_version": 1, "content": builders.prompt_content()},
        )
        assert (forbidden.status_code, forbidden.json()["code"]) == (403, "permission_denied")
        forked = post(client, "/library/prompts/my-brief/fork/", {"new_slug": "bob-brief", "name": "Bob's"}).json()
        assert forked["derived_from"].startswith("my-brief@1#")
        assert forked["version"] == 1

    def test_validate(self, golden, as_alice):
        ok = post(
            as_alice,
            "/library/stacks/validate/",
            {"content": builders.stack_content(brief_fragment="stack-flask-react@1")},
        ).json()
        assert ok["ok"] is True
        assert ok["content_hash"]
        assert ok["content"]["brief_fragment"].startswith("stack-flask-react@1#")
        bad = post(
            as_alice,
            "/library/tasks/validate/",
            {"content": {**builders.task_content(), "features": ["x"]}},
        ).json()
        assert bad["ok"] is False
        assert "lint_unknown_feature" in {i["code"] for i in bad["issues"]}
        assert bad["derived"]["endpoint_count"] == 5

    def test_starter_tree_and_blob(self, golden, as_alice):
        tree = as_alice.get(f"{API}/library/starters/flask-react-minimal/versions/1/tree/").json()
        assert "Dockerfile" in {f["path"] for f in tree["files"]}
        assert tree["dockerfile"].startswith("FROM")
        blob = next(f["blob"] for f in tree["files"] if f["path"] == "Dockerfile")
        text = as_alice.get(f"{API}/library/blobs/{blob}/")
        assert text.status_code == 200
        assert text.content.decode() == tree["dockerfile"]
        assert as_alice.get(f"{API}/library/blobs/{'0' * 64}/").status_code == 404

    def test_oracle_routes(self, golden, as_alice, client, staff):
        assert as_alice.get(f"{API}/library/tasks/crud_todo_list/versions/1/oracle/").json() == {}
        refused = post(as_alice, "/library/tasks/crud_todo_list/versions/1/oracle/", {"stacks": ["flask-react@1"]})
        assert refused.status_code == 403
        client.force_login(staff)
        pending = post(client, "/library/tasks/crud_todo_list/versions/1/oracle/", {"stacks": ["fastapi-vue@1"]})
        # WP-09: a stack without a reference tree (crud_todo_list has none for fastapi-vue) is recorded
        # as missing, nothing queued.
        assert pending.status_code == 200
        assert pending.json() == {"queued_runs": []}

    def test_packages(self, golden, as_alice, client, bob):
        create_prompt(as_alice)
        exported = post(as_alice, "/library/packages/export/", {"refs": ["prompt:my-brief@1"]})
        assert exported.status_code == 200
        assert exported["Content-Type"] == packages.PACKAGE_MEDIA_TYPE
        assert "attachment" in exported["Content-Disposition"]
        client.force_login(bob)
        upload = SimpleUploadedFile("pkg.yaml", exported.content, content_type="application/x-yaml")
        dry = client.post(f"{API}/library/packages/import/?mode=dry_run", {"file": upload}).json()
        assert dry["mode"] == "dry_run"
        assert dry["conflicts"][0]["resolution"] == "renamed to my-brief-imported"
        upload.seek(0)
        refused = client.post(f"{API}/library/packages/import/?mode=apply&on_conflict=error", {"file": upload})
        assert refused.status_code == 409
        assert refused.json()["conflicts"][0]["resolution"] == "refused"
        raw = client.post(
            f"{API}/library/packages/import/?mode=apply",
            data=exported.content,
            content_type="application/x-yaml",
        )
        assert raw.status_code == 200
        assert raw.json()["created"][0]["as"].startswith("my-brief-imported@1#")
        assert (
            client.post(
                f"{API}/library/packages/import/",
                data=b"nope: [",
                content_type="application/x-yaml",
            ).status_code
            == 400
        )
        assert post(client, "/library/packages/export/", {"refs": ["prompt:my-brief@1"]}).status_code == 404


class TestPrompts:
    APP = {
        "task": "crud_todo_list@1",
        "variant": "standard",
        "stack": "flask-react",
        "starter_level": "minimal",
        "style": "tailwind@1",
        "auth_mode": "cookie_session",
        "guidance": "generic@1",
        "harness_preset": "opencode-native@1",
    }

    def test_variables(self, as_alice):
        names = {v["name"] for v in as_alice.get(f"{API}/prompts/variables/?mode=app").json()}
        assert {"task", "stack", "auth_contract"} <= names
        assert "slot" not in names
        assert as_alice.get(f"{API}/prompts/variables/?mode=nope").status_code == 400

    def test_render(self, golden, as_alice):
        body = post(as_alice, "/prompts/render/", {"prompt_set": "app-default@1", "context_from": self.APP}).json()
        (stimulus,) = body["stimuli"]
        assert stimulus["role"] == "brief"
        assert "HttpOnly session cookie" in stimulus["text"]
        assert body["issues"] == []
        assert len(body["prompt_hash"]) == 64
        spans = tuple(SourceSpan.model_validate(span) for span in stimulus["sources"])
        assert_partition(RenderedText(stimulus["text"], spans))
        assert stimulus["chars"] == len(stimulus["text"])

    def test_render_issues(self, golden, as_alice):
        context = {
            **self.APP,
            "auth_mode": "spec_defined",
            "starter_level": "full",
            "stack": "fastapi-vue@1",
            "style": "tailwind",
        }
        body = post(as_alice, "/prompts/render/", {"prompt_set": "app-default@1", "context_from": context}).json()
        assert {issue["code"] for issue in body["issues"]} == {"starter_missing"}
        context["task"] = "notes_markdown_render@1"
        assert (
            post(as_alice, "/prompts/render/", {"prompt_set": "app-default@1", "context_from": context}).status_code
            == 200
        )

    def test_draft_templates(self, golden, as_alice):
        body = post(
            as_alice,
            "/prompts/render/",
            {
                "prompt_set": "app-default@1",
                "templates": {"brief": {"template": "Short: {{ task.title }}"}},
                "context_from": self.APP,
            },
        ).json()
        assert body["stimuli"][0]["text"] == "Short: Todo list\n"
        assert body["stimuli"][0]["template_ref"] == "draft:brief"

    def test_render_errors_are_422(self, golden, as_alice):
        response = post(
            as_alice,
            "/prompts/render/",
            {
                "prompt_set": None,
                "templates": {"brief": {"template": "{{ task.nope }}"}},
                "context_from": self.APP,
            },
        )
        assert response.status_code == 422
        assert response.json()["issues"][0]["code"] == "lint_template_render_error"
        assert post(as_alice, "/prompts/render/", {"context_from": self.APP}).status_code == 400
        missing = post(
            as_alice,
            "/prompts/render/",
            {"prompt_set": "app-default@1", "context_from": {**self.APP, "task": "ghost@1"}},
        )
        assert missing.status_code == 404

    def test_diff(self, golden, as_alice):
        a = {"prompt_set": "app-default@1", "context_from": self.APP}
        b = {"prompt_set": "app-default@1", "context_from": {**self.APP, "variant": "minimal"}}
        body = post(as_alice, "/prompts/diff/", {"a": a, "b": b}).json()
        assert [d["role"] for d in body["per_role"]] == ["brief"]
        assert body["per_role"][0]["unified"].startswith(
            "--- a/brief",
        )
