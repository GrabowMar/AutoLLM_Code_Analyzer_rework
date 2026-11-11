"""Library lint: fixtures per kind, the vocabulary, starter rules and check statedness (section 8.2)."""

from __future__ import annotations

import copy

import pytest

from backend.generation import identity
from backend.generation.contracts.issues import ISSUE_CODES
from backend.generation.library import lint as library_lint
from backend.generation.library.lint import Vocabulary
from backend.generation.library.lint import check_statedness
from backend.generation.library.lint import endpoint_mentioned
from backend.generation.library.lint import lint
from backend.generation.library.lint import tag_checks
from backend.generation.testing import content as builders
from backend.generation.testing.content import fake_ref

VOCAB = Vocabulary(features=library_lint.DEFAULT_FEATURES, surfaces=frozenset({"cwe-89", "cwe-639", "cwe-79"}))


def codes(issues) -> list[str]:
    return [issue.code for issue in issues]


def task(**changes):
    content = builders.task_content()
    content["prose"] = {
        "minimal": "A todo app with users and todos. Endpoints: POST /api/auth/register, POST /api/auth/login, "
        "GET /api/todos, POST /api/todos, PATCH /api/todos/{id}.",
        "standard": "Users register (POST /api/auth/register) and log in (POST /api/auth/login). "
        "They list todos with GET /api/todos and create them with POST /api/todos. "
        "PATCH /api/todos/{id} edits a todo; each user sees only their own todos.",
        "exhaustive": "Users register (POST /api/auth/register) and log in (POST /api/auth/login). "
        "GET /api/todos lists todos sorted by creation; POST /api/todos creates one. "
        "PATCH /api/todos/:id edits a todo; only the owner may edit (403 otherwise, 404 when unknown). "
        "Titles longer than 200 characters are rejected with 400.",
    }
    content.update(changes)
    return content


def lint_task(content, **kwargs):
    kwargs.setdefault("vocabulary", VOCAB)
    return lint("task", content, resolve=False, **kwargs)


class TestTask:
    def test_a_well_formed_task_is_clean(self):
        assert [i for i in lint_task(task(), system=True) if i.severity != "info"] == []

    def test_invalid_content(self):
        issues = lint_task({"title": "x"})
        assert set(codes(issues)) == {"lint_content_invalid"}
        assert all(issue.severity == "error" for issue in issues)

    def test_vocabulary(self):
        issues = lint_task(task(features=["auth", "telepathy"], surfaces=["cwe-89", "sqli", "cwe-1"]))
        assert codes(issues).count("lint_unknown_feature") == 1
        assert codes(issues).count("lint_unknown_surface") == 2  # not a CWE id; not in the vocabulary

    def test_fallback_vocabulary_is_reported(self, settings, tmp_path):
        settings.GENERATION_LIBRARY_DATA_DIR = str(tmp_path)
        assert "lint_vocabulary_fallback" in codes(lint("task", task(), resolve=False))

    def test_vocabulary_file(self, settings, tmp_path):
        (tmp_path / "tasks").mkdir()
        (tmp_path / "tasks" / "vocab.yaml").write_text(
            "features: [{key: auth}, ownership, search]\nsurfaces: [{id: cwe-89}]\n",
        )
        settings.GENERATION_LIBRARY_DATA_DIR = str(tmp_path)
        issues = lint("task", task(), resolve=False)
        assert "lint_vocabulary_fallback" not in codes(issues)
        assert codes(issues).count("lint_unknown_surface") == 1  # cwe-639 is not in this vocabulary

    def test_references_and_ids(self):
        content = task()
        content["endpoints"].append(copy.deepcopy(content["endpoints"][0]))
        content["endpoints"][4]["owner_entity"] = "Nope"
        content["endpoints"].append({"id": "x.y", "method": "GET", "path": "/api/x", "auth": "owner"})
        content["acceptance"][0]["endpoint"] = "missing.endpoint"
        content["ui"][0]["endpoints"] = ["also.missing"]
        content["entities"][0]["fields"].append({"name": "parent", "type": "ref", "ref": "Ghost"})
        content["seed_data"] = {"Phantom": [{"a": 1}]}
        found = codes(lint_task(content))
        assert found.count("lint_duplicate_id") == 1
        assert found.count("lint_unknown_entity_ref") == 3
        assert found.count("lint_unknown_endpoint_ref") == 2
        assert "lint_owner_entity_missing" in found

    def test_owner_entity_not_owned_and_auth_flow(self):
        content = task(auth_flow=None)
        content["entities"][0]["owned_by_user"] = False
        found = codes(lint_task(content))
        assert "lint_owner_entity_not_owned" in found
        assert "lint_auth_flow_missing" in found

    def test_system_tasks_need_three_variants(self):
        content = task()
        del content["prose"]["minimal"]
        assert "lint_prose_variant_missing" not in codes(lint_task(content))
        assert codes(lint_task(content, system=True)).count("lint_prose_variant_missing") == 1

    @pytest.mark.parametrize(
        "phrase",
        ["We evaluate it", "a test suite runs", "it is graded", "log in as admin/admin", "the default password is x"],
    )
    def test_forbidden_phrases(self, phrase):
        content = task()
        content["prose"]["standard"] += " " + phrase
        assert "lint_prose_forbidden_phrase" in codes(lint_task(content))

    def test_minimal_word_budget(self):
        content = task()
        content["prose"]["minimal"] = " ".join(["word"] * 121)
        assert "lint_prose_too_long" in codes(lint_task(content))

    def test_every_endpoint_must_be_mentioned_in_standard_and_exhaustive(self):
        content = task()
        content["prose"]["standard"] = content["prose"]["standard"].replace("PATCH /api/todos/{id}", "editing")
        issues = [i for i in lint_task(content) if i.code == "lint_prose_endpoint_unmentioned"]
        assert [(i.path, i.data["endpoint"]) for i in issues] == [("prose.standard", "todos.update")]


class TestEndpointMention:
    @pytest.mark.parametrize(
        ("prose", "expected"),
        [
            ("PATCH /api/todos/{id}", True),
            ("PATCH /api/todos/:id updates", True),
            ("`PATCH /api/todos/<id>`", True),
            ("PATCH /api/todos/42", True),
            ("PATCH /api/todos", False),
            ("GET /api/todos/{id}", False),
            ("PATCH /api/todos/{id}/archive", False),
        ],
    )
    def test_method_and_path(self, prose, expected):
        endpoint = {"method": "PATCH", "path": "/api/todos/{id}"}
        assert endpoint_mentioned(prose, endpoint) is expected

    def test_a_prefix_path_does_not_match_a_longer_one(self):
        assert not endpoint_mentioned("GET /api/todos/{id}", {"method": "GET", "path": "/api/todos"})
        assert endpoint_mentioned("GET /api/todos.", {"method": "GET", "path": "/api/todos"})


class TestStatedness:
    """``stated_in`` tagging on fixtures for the three prose variants (section 8.2)."""

    CHECKS = [
        {"id": "list.ok", "category": "functional", "endpoint": "todos.list", "expect": {"status": 200}},
        {"id": "title.too_long", "category": "validation", "endpoint": "todos.create", "expect": {"status": 400}},
        {"id": "update.not_owner", "category": "ownership", "endpoint": "todos.update", "expect": {"status": 403}},
        {"id": "update.unknown", "category": "negative", "endpoint": "todos.update", "expect": {"status": 404}},
        {"id": "login.required", "category": "auth", "endpoint": None, "expect": {"status": 401}},
    ]

    def content(self):
        content = task()
        content["prose"] = {
            "minimal": "A todo app. GET /api/todos lists todos.",
            "standard": "GET /api/todos lists the todos and POST /api/todos creates one. "
            "PATCH /api/todos/{id} edits a todo. Users log in first.",
            "exhaustive": "GET /api/todos lists the todos and POST /api/todos creates one; titles over 200 "
            "characters are invalid (400). PATCH /api/todos/{id} edits a todo: only the owner may edit it, "
            "and an unknown id gives 404. Users log in first.",
        }
        return content

    def test_tags_per_variant(self):
        tags = check_statedness(self.content(), self.CHECKS)
        assert tags == {
            "list.ok": ["minimal", "standard", "exhaustive"],
            "title.too_long": ["exhaustive"],
            "update.not_owner": ["exhaustive"],
            "update.unknown": ["exhaustive"],
            "login.required": ["standard", "exhaustive"],
        }

    def test_tag_checks_fills_stated_in(self):
        tagged = tag_checks(self.content(), self.CHECKS)
        assert [item["stated_in"] for item in tagged][1] == ["exhaustive"]
        assert "stated_in" not in self.CHECKS[1]

    def test_hand_written_checks_keep_an_explicit_stated_in(self):
        content = self.content()
        assert check_statedness(content)["todos.sorted"] == ["exhaustive"]
        content["acceptance"][0]["stated_in"] = []
        assert check_statedness(content)["todos.sorted"] == ["minimal", "standard", "exhaustive"]

    def test_an_unstated_check_is_info(self):
        content = self.content()
        content["acceptance"][0]["stated_in"] = []
        content["acceptance"][0]["endpoint"] = "todos.update"
        content["prose"] = {"standard": "Nothing about endpoints.", "exhaustive": "Nothing either."}
        unstated = [i for i in lint_task(content) if i.code == "lint_check_unstated"]
        assert len(unstated) == 1
        assert unstated[0].severity == "info"


class TestPrompts:
    def test_clean_template(self):
        assert lint("prompt", builders.prompt_content(), resolve=False) == []

    def test_syntax_and_filters(self):
        assert codes(lint("prompt", builders.prompt_content(template="{% if %}"), resolve=False)) == [
            "lint_template_syntax",
        ]
        found = codes(lint("prompt", builders.prompt_content(template="{{ task.title | safe }}"), resolve=False))
        assert found
        assert set(found) == {"lint_template_syntax"}

    def test_variables(self):
        content = builders.prompt_content(
            template="{{ task.title }} {{ style.system }}",
            variables=("task", "nope", "slot"),
        )
        found = codes(lint("prompt", content, resolve=False))
        assert found.count("lint_undeclared_variable") == 1  # style
        assert found.count("lint_unused_variable") == 2  # nope, slot
        assert found.count("lint_unknown_variable") == 2  # nope; slot is not an app-mode variable

    def test_fragments_may_not_read_fragment_texts(self):
        content = builders.prompt_content(
            role="fragment",
            template="{{ auth_contract }}",
            variables=("auth_contract",),
        )
        assert "lint_unknown_variable" in codes(lint("prompt", content, resolve=False))


def _resolver(rows):
    return lambda kind, ref: rows.get((kind, ref))


class TestPromptSets:
    def test_content_rules(self):
        found = codes(lint("prompt_set", builders.prompt_set_content(fragments={}), resolve=False))
        assert found.count("lint_prompt_fragment_missing") == 5
        slot = builders.prompt_set_content(mode="slot", roles={"slot_single_shot_user": fake_ref("u@1")})
        assert codes(lint("prompt_set", slot, resolve=False)) == ["lint_prompt_role_missing"]

    def test_ref_rules(self):
        content = builders.prompt_set_content()
        brief = content["roles"]["brief"]
        fragments = content["fragments"]
        rows = {("prompt", brief): builders.prompt_content(role="system", modes=("slot",))}
        for ref in fragments.values():
            rows[("prompt", ref)] = builders.prompt_content(role="fragment", variables=(), template="x")
        rows[("prompt", fragments["network.none"])] = builders.prompt_content(role="brief")
        del rows[("prompt", fragments["network.packages"])]
        found = codes(lint("prompt_set", content, resolve=_resolver(rows)))
        assert found.count("lint_prompt_role_mismatch") == 1
        assert found.count("lint_prompt_mode_mismatch") == 1
        assert found.count("lint_fragment_role") == 1
        assert found.count("lint_unknown_library_ref") == 1


class TestStacksStylesGuidance:
    def test_fragment_role(self):
        content = builders.stack_content()
        rows = {("prompt", content["brief_fragment"]): builders.prompt_content(role="brief")}
        assert codes(lint("stack", content, resolve=_resolver(rows))) == ["lint_fragment_role"]
        assert codes(lint("stack", content, resolve=_resolver({}))) == ["lint_unknown_library_ref"]

    def test_style_frontends(self):
        content = {**builders.style_content(), "compatible_frontends": ["react", "angular"]}
        assert codes(lint("style", content, resolve=False)) == ["lint_style_frontend_unknown"]

    def test_guidance_needs_a_fragment(self):
        assert codes(lint("guidance", builders.guidance_content(security_hint="generic"), resolve=False)) == [
            "lint_guidance_fragment_missing",
        ]
        assert lint("guidance", builders.guidance_content(), resolve=False) == []


DOCKERFILE = f"FROM {builders.BASE_IMAGE['digest']} AS build\nFROM build\nCOPY . .\n".encode()


def starter(files: dict[str, bytes], **changes):
    entries = {path: {"blob": identity.bytes_sha(data), "mode": 0o644} for path, data in files.items()}
    content = builders.starter_content(files=entries)
    content.update(changes)
    store = {identity.bytes_sha(data): data for data in files.values()}
    return content, store.get


class TestStarters:
    """Starter lint = blob presence + the composer flag + WP-05's rule list (``build/starter_rules.py``)."""

    BASE = {
        "Dockerfile": DOCKERFILE,
        "backend/requirements.txt": b"flask==3.1.0\n",
        "backend/requirements.lock": b"flask==3.1.0 --hash=sha256:00\n",
        "backend/app.py": b"from flask import Flask\n",
        "frontend/package.json": b'{"dependencies": {"react": "19.0.0"}}\n',
        "frontend/package-lock.json": b'{"packages": {"": {"dependencies": {"react": "19.0.0"}}}}\n',
    }

    def stack_resolver(self, content, **stack_changes):
        return _resolver({("stack", content["stack_profile"]): {**builders.stack_content(), **stack_changes}})

    def test_a_clean_minimal_starter(self):
        content, read = starter(dict(self.BASE))
        assert lint("starter", content, resolve=self.stack_resolver(content), read_blob=read) == []

    def test_forbidden_content_and_security_libraries(self):
        files = dict(self.BASE)
        files["backend/app.py"] = b"# LLM: write the auth here\n"
        files["backend/requirements.txt"] = b"flask==3.1.0\nFlask-Login==0.6\nbcrypt\n"
        content, read = starter(files)
        found = codes(lint("starter", content, resolve=False, read_blob=read))
        assert found.count("lint_starter_forbidden_content") == 1
        assert found.count("lint_starter_security_library") == 2

    def test_unpinned_from_and_missing_lockfile(self):
        files = dict(self.BASE)
        files["Dockerfile"] = b"FROM node:24 AS web\nFROM python:3.13-slim\nCOPY --from=web /a /b\n"
        content, read = starter(files, lockfiles=["backend/uv.lock"])
        found = codes(lint("starter", content, resolve=False, read_blob=read))
        assert found.count("lint_starter_unpinned_image") == 2
        assert "lint_starter_file_missing" in found

    def test_missing_blobs_and_failed_composer_check(self):
        content, _ = starter(dict(self.BASE), forbidden_content_check={"passed": False, "linter_version": 1})
        found = codes(lint("starter", content, resolve=False, read_blob=lambda sha: None))
        assert found.count("lint_starter_blob_missing") == len(self.BASE)
        assert found.count("lint_starter_forbidden_content") == 1
        assert "lint_starter_file_missing" not in found  # tree rules need every file

    def test_stack_mismatch(self):
        content, read = starter(dict(self.BASE))
        no_frontend = self.stack_resolver(
            content,
            frontend={"framework": "none", "language": "none", "build_tool": "none"},
        )
        assert "lint_starter_stack_mismatch" in codes(lint("starter", content, resolve=no_frontend, read_blob=read))

    def test_the_stack_ref_must_exist(self):
        content, read = starter(dict(self.BASE))
        assert "lint_unknown_library_ref" in codes(lint("starter", content, resolve=_resolver({}), read_blob=read))


class TestDetectors:
    def test_regex_and_semgrep(self):
        assert lint("detector", builders.detector_content(), resolve=False) == []
        bad_regex = {**builders.detector_content(), "rule": "execute(("}
        assert codes(lint("detector", bad_regex, resolve=False)) == ["lint_detector_rule_invalid"]
        semgrep = {**builders.detector_content(), "engine": "semgrep", "rule": "rules: []"}
        assert codes(lint("detector", semgrep, resolve=False)) == ["lint_detector_rule_invalid"]
        good = {**semgrep, "rule": "rules:\n  - id: x\n    pattern: eval(...)\n"}
        assert lint("detector", good, resolve=False) == []


@pytest.mark.parametrize("kind", ["harness_preset", "sampling", "resource_policy", "eval_suite"])
def test_preset_kinds_lint_on_content(kind):
    assert lint(kind, builders.library_content(kind), resolve=False) == []
    assert codes(lint(kind, {"nope": 1}, resolve=False))[0] == "lint_content_invalid"


def test_every_lint_code_is_registered_and_owned_by_lint():
    lint_codes = [code for code in ISSUE_CODES if code.startswith("lint_")]
    assert all(ISSUE_CODES[code].owner == "library/lint.py" for code in lint_codes)
    for code in (
        "lint_starter_blob_missing",
        "lint_template_render_error",
        "lint_detector_rule_invalid",
        "lint_vocabulary_fallback",
    ):
        assert code in ISSUE_CODES


def test_editor_brief_issues():
    assert library_lint.editor_brief_issues(editor_harness=True, brief_content={"tags": []})[0].code == (
        "editor_harness_agent_brief"
    )
    assert library_lint.editor_brief_issues(editor_harness=True, brief_content={"tags": ["editor"]}) == []
    assert library_lint.editor_brief_issues(editor_harness=False, brief_content={"tags": []}) == []


class TestInstrumentOverlay:
    """The task's ``tester.yaml`` is linted with the acceptance compiler's strict loader."""

    @staticmethod
    def _content(slug):
        from pathlib import Path

        import yaml

        folder = Path(library_lint.__file__).resolve().parents[1] / "data" / "tasks" / slug
        content = yaml.safe_load((folder / "task.yaml").read_text())
        content["prose"] = {p.stem: p.read_text().rstrip("\n") for p in sorted((folder / "prose").glob("*.md"))}
        return content

    @staticmethod
    def _overlay(tmp_path, slug, text):
        folder = tmp_path / "tasks" / slug
        folder.mkdir(parents=True)
        (folder / "tester.yaml").write_text(text)

    def test_no_overlay_is_no_issue(self, tmp_path):
        assert library_lint.overlay_issues("crud_todo_list", self._content("crud_todo_list"), tmp_path) == []

    def test_a_usable_overlay_is_clean(self, tmp_path):
        self._overlay(tmp_path, "crud_todo_list", "schema: 1\nnotes: n\nsamples: {title: 'llmlab {rand}'}\n")
        assert library_lint.overlay_issues("crud_todo_list", self._content("crud_todo_list"), tmp_path) == []

    def test_a_broken_overlay_is_an_error(self, tmp_path):
        self._overlay(
            tmp_path,
            "crud_todo_list",
            "schema: 1\nnotes: n\nsamples: {title: '{nope}'}\n"
            "checks: {todos.nope.ok: {why: w, skip: true}, todos.create.ok: {expect: {status: 200}}}\n",
        )
        issues = library_lint.overlay_issues("crud_todo_list", self._content("crud_todo_list"), tmp_path)
        assert set(codes(issues)) == {"lint_content_invalid"}
        assert all(issue.severity == "error" for issue in issues)
        text = " | ".join(issue.message for issue in issues)
        assert "unknown placeholder {nope}" in text
        assert "`why` is required" in text

    def test_unknown_ids_are_reported(self, tmp_path):
        self._overlay(
            tmp_path,
            "crud_todo_list",
            "schema: 1\nnotes: n\nendpoints: {todos.nope: {json: {a: 1}}}\n"
            "checks: {todos.nope.ok: {why: w, skip: true}}\n",
        )
        issues = library_lint.overlay_issues("crud_todo_list", self._content("crud_todo_list"), tmp_path)
        text = " | ".join(issue.message for issue in issues)
        assert "no such endpoint" in text

    def test_lint_checks_the_overlay_of_a_named_task(self):
        # the tree's own overlays (if any) are usable; a task without one is unaffected
        content = self._content("crud_todo_list")
        issues = lint("task", content, resolve=False, vocabulary=VOCAB, slug="crud_todo_list")
        assert not [i for i in issues if "tester.yaml" in i.message]


class TestStatednessValues:
    """Rule table 2 / 3: a limit, an option list, a requiredness or a status counts where the variant gives it."""

    STANDARD = (
        "- `POST /api/items` adds an item with its opening quantity.\n"
        "- `GET /api/items` lists items; each has a `status` (`open`, `closed`).\n"
        "- `DELETE /api/items/{id}` deletes an item.\n"
        "- Add and edit forms that show the API's validation messages, including a duplicate sku."
    )
    EXHAUSTIVE = (
        "- `POST /api/items` adds an item with its opening quantity. On success it answers 201. It answers 400 "
        "for invalid input; 409 for a sku another item already has.\n"
        "- `GET /api/items` lists items; each has a `status` (`open`, `closed`).\n"
        "- `DELETE /api/items/{id}` deletes an item. On success it answers 204.\n"
        "- Item: `name` required, at most 200 characters; `sku` required, at most 40 characters, unique; "
        "`location` optional, at most 100 characters; `kind` one of `part`, `tool`; `notes` at most 1,000 characters."
    )
    ENDPOINTS = [
        {
            "id": "items.create",
            "method": "POST",
            "path": "/api/items",
            "request_schema": {
                "type": "object",
                "required": ["name", "sku"],
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "sku": {"type": "string", "maxLength": 40},
                    "location": {"type": "string", "maxLength": 100},
                    "kind": {"type": "string", "enum": ["part", "tool"]},
                    "secret": {"type": "string", "maxLength": 128},
                },
            },
        },
        {"id": "items.list", "method": "GET", "path": "/api/items"},
        {"id": "items.delete", "method": "DELETE", "path": "/api/items/{id}"},
    ]
    VALID = {"name": "llmlab name", "sku": "SKU-1", "location": "llmlab location", "kind": "part", "secret": "s"}

    def content(self):
        return {"endpoints": self.ENDPOINTS, "prose": {"standard": self.STANDARD, "exhaustive": self.EXHAUSTIVE}}

    def check(self, check_id, body=None, status=(400, 422), **extra):
        request = {"method": "POST", "path": "/api/items", "json": body if body is not None else dict(self.VALID)}
        base = {"id": check_id, "category": "validation", "endpoint": "items.create", "request": request}
        return {**base, "expect": {"status": list(status) if isinstance(status, tuple) else status}, **extra}

    def tags(self, *checks):
        return check_statedness(self.content(), list(checks))

    def test_value_functions(self):
        assert library_lint.limit_stated(self.EXHAUSTIVE, "name", 200)
        assert library_lint.limit_stated(self.EXHAUSTIVE, "notes", 1000)  # written 1,000
        assert not library_lint.limit_stated(self.EXHAUSTIVE, "name", 40)  # the 40 belongs to the sku clause
        assert not library_lint.limit_stated(self.EXHAUSTIVE, "username", 200)
        assert not library_lint.limit_stated(self.STANDARD, "name", 200)
        assert library_lint.options_stated(self.STANDARD, ["open", "closed", None])
        assert not library_lint.options_stated(self.STANDARD, ["open", "closed", "archived"])
        assert not library_lint.options_stated("reopened and enclosed", ["open", "closed"])  # whole tokens only
        assert library_lint.required_stated(self.EXHAUSTIVE, "sku")
        assert not library_lint.required_stated(self.EXHAUSTIVE, "location")  # its clause says optional
        assert not library_lint.required_stated(self.STANDARD, "name")
        create, delete = self.ENDPOINTS[0], self.ENDPOINTS[2]
        assert library_lint.status_stated(self.EXHAUSTIVE, create, 201)
        assert not library_lint.status_stated(self.EXHAUSTIVE, create, 204)  # the 204 stands on the DELETE line
        assert library_lint.status_stated(self.EXHAUSTIVE, delete, 204)
        assert library_lint.status_stated(self.EXHAUSTIVE, create, 409)
        assert not library_lint.status_stated(self.STANDARD, create, 201)
        # an action endpoint: the line names its input and says invalid input is refused
        line = "- `POST /api/me/password` changes the password; the body holds `current_password`. It answers 400."
        endpoint = {"method": "POST", "path": "/api/me/password"}
        assert library_lint.required_stated(line, "current_password", endpoint)
        assert not library_lint.required_stated(line.replace(" It answers 400.", ""), "current_password", endpoint)

    def test_a_keyword_is_not_enough_for_a_validation_check(self):
        """The frontend sentence 'validation messages' made every .invalid.* check stated in version 1."""
        too_long = self.check("too_long", {**self.VALID, "name": "y" * 201})
        missing = self.check("missing", {k: v for k, v in self.VALID.items() if k != "name"})
        enum = self.check("enum", {**self.VALID, "kind": "notlisted"})
        assert self.tags(too_long, missing, enum) == {
            "too_long": ["exhaustive"],
            "missing": ["exhaustive"],
            "enum": ["exhaustive"],
        }

    def test_every_broken_limit_must_be_stated(self):
        both = self.check(
            "too_long",
            {**self.VALID, "name": "y" * 201},
            also=[
                {
                    "label": "sku",
                    "request": {"method": "POST", "path": "/api/items", "json": {**self.VALID, "sku": "y" * 41}},
                },
            ],
        )
        assert self.tags(both)["too_long"] == ["exhaustive"]
        # a limit that only the request schema has makes the check unstated everywhere
        schema_only = self.check("too_long", {**self.VALID, "secret": "y" * 129})
        assert self.tags(schema_only)["too_long"] == []
        reasons = library_lint.statedness_reasons(self.content(), [schema_only])["too_long"]
        # (rule table 3: the standard prose does not list the options of `kind` either, and the body sends one)
        assert reasons == {
            "standard": ["sent_options:kind", "limit:secret=128"],
            "exhaustive": ["limit:secret=128"],
        }

    def test_exact_statuses(self):
        plain_body = {k: v for k, v in self.VALID.items() if k != "kind"}  # no option value: statuses only
        create = self.check("create.ok", plain_body, status=201, category="functional")
        either = self.check("create.either", plain_body, status=(200, 201), category="functional")
        plain = {**self.check("list.ok", status=200, category="functional"), "endpoint": "items.list"}
        plain["request"] = {"method": "GET", "path": "/api/items"}
        duplicate = self.check("duplicate", plain_body, status=409, category="negative")
        tags = self.tags(create, either, plain, duplicate)
        assert tags["create.ok"] == ["exhaustive"]
        assert tags["create.either"] == ["standard", "exhaustive"]  # a tolerant expectation states nothing exact
        assert tags["list.ok"] == ["standard", "exhaustive"]
        assert tags["duplicate"] == ["exhaustive"]  # "a duplicate sku" without the 409 is not the status

    def test_response_options_and_valid_request_options(self):
        listed = {"properties": {"items": {"items": {"properties": {"status": {"enum": ["open", "closed", None]}}}}}}
        unlisted = {"properties": {"kind": {"enum": ["part", "tool"]}}}
        base = {"category": "functional", "endpoint": "items.list", "request": {"method": "GET", "path": "/api/items"}}
        with_listed = {**base, "id": "list.listed", "expect": {"status": 200, "json_schema": listed}}
        with_unlisted = {**base, "id": "list.unlisted", "expect": {"status": 200, "json_schema": unlisted}}
        # rule table 3: a setup step that sends a valid option makes the check depend on the option list too
        # (round 2 left such a check in the stated set of a variant that never lists the options)
        sends_valid = {**base, "id": "list.after_create", "expect": {"status": 200}}
        sends_valid["setup"] = [{"request": {"method": "POST", "path": "/api/items", "json": dict(self.VALID)}}]
        no_option = {**base, "id": "list.after_plain_create", "expect": {"status": 200}}
        body = {k: v for k, v in self.VALID.items() if k != "kind"}
        no_option["setup"] = [{"request": {"method": "POST", "path": "/api/items", "json": body}}]
        tags = self.tags(with_listed, with_unlisted, sends_valid, no_option)
        assert tags == {
            "list.listed": ["standard", "exhaustive"],
            "list.unlisted": ["exhaustive"],
            "list.after_create": ["exhaustive"],
            "list.after_plain_create": ["standard", "exhaustive"],
        }
        reasons = library_lint.statedness_reasons(self.content(), [sends_valid])["list.after_create"]
        assert reasons == {"standard": ["sent_options:kind"]}

    def test_sent_options_in_nested_bodies_and_query_parameters(self):
        """Rule table 3: option values at any depth of a body and in the query string count."""
        prose = {
            "standard": "- `POST /api/attempts` scores the answers.\n- `GET /api/attempts` lists attempts.",
            "exhaustive": (
                "- `POST /api/attempts` scores the answers; `selected` is one of `A`, `B`.\n"
                "- `GET /api/attempts` lists attempts; `grade` keeps `pass` or `fail`."
            ),
        }
        answers = {"type": "array", "items": {"type": "object", "properties": {"selected": {"enum": ["A", "B"]}}}}
        endpoints = [
            {
                "id": "attempts.submit",
                "method": "POST",
                "path": "/api/attempts",
                "request_schema": {"type": "object", "properties": {"answers": answers}},
            },
            {
                "id": "attempts.list",
                "method": "GET",
                "path": "/api/attempts",
                "query": [
                    {"name": "grade", "type": "enum", "enum": ["pass", "fail"]},
                    {"name": "page", "type": "int"},
                ],
            },
        ]
        content = {"endpoints": endpoints, "prose": prose}
        tolerant = {"status": [200, 201]}
        submit = {"id": "submit", "category": "functional", "endpoint": "attempts.submit", "expect": tolerant}
        submit["request"] = {"method": "POST", "path": "/api/attempts", "json": {"answers": [{"selected": "A"}]}}
        listed = {"id": "list", "category": "functional", "endpoint": "attempts.list", "expect": {"status": 200}}
        listed["request"] = {"method": "GET", "path": "/api/attempts", "query": {"grade": "pass", "page": 1}}
        paged = {**listed, "id": "paged", "request": {"method": "GET", "path": "/api/attempts", "query": {"page": 1}}}
        assert check_statedness(content, [submit, listed, paged]) == {
            "submit": ["exhaustive"],
            "list": ["exhaustive"],
            "paged": ["standard", "exhaustive"],
        }
        assert library_lint.statedness_reasons(content, [submit, listed])["list"] == {
            "standard": ["sent_options:grade"],
        }

    def test_an_exact_200_of_a_post_is_stated_only_where_the_variant_gives_it(self):
        """Rule table 3: 200 is the default answer of a read; a POST may as well answer 201 or 204."""
        prose = {
            "standard": "- `POST /api/runs` records a run.\n- `GET /api/runs` lists the runs.",
            "exhaustive": "- `POST /api/runs` records a run and answers 200.\n- `GET /api/runs` lists the runs.",
        }
        endpoints = [
            {"id": "runs.record", "method": "POST", "path": "/api/runs"},
            {"id": "runs.list", "method": "GET", "path": "/api/runs"},
        ]
        content = {"endpoints": endpoints, "prose": prose}
        record = {"id": "record", "category": "functional", "endpoint": "runs.record", "expect": {"status": 200}}
        record["request"] = {"method": "POST", "path": "/api/runs", "json": {}}
        either = {**record, "id": "either", "expect": {"status": [200, 201]}}
        read = {"id": "read", "category": "functional", "endpoint": "runs.list", "expect": {"status": 200}}
        read["request"] = {"method": "GET", "path": "/api/runs"}
        assert check_statedness(content, [record, either, read]) == {
            "record": ["exhaustive"],
            "either": ["standard", "exhaustive"],  # a tolerance is not an exact status
            "read": ["standard", "exhaustive"],
        }
        assert library_lint.statedness_reasons(content, [record])["record"] == {"standard": ["status:200"]}

    def test_query_parameter_defaults(self):
        """Round 3: where a variant states the default of a query parameter, and a parameter that only
        qualifies another one."""
        listing = {
            "method": "GET",
            "path": "/api/stores",
            "query": [{"name": "lat"}, {"name": "lng"}, {"name": "radius", "default": 25}, {"name": "page"}],
        }
        standard = (
            "- `GET /api/stores` lists stores; with `lat` and `lng` it keeps the stores within `radius` "
            "kilometres (query parameters: `lat`, `lng`, `radius`, `page`)."
        )
        exhaustive = (
            "- `GET /api/stores` lists stores. `radius` is the search radius (default 25, at most 500); "
            "`page` is the 1-based page number (default 1).\n"
            "- `GET /api/days` shows a day. `date` is the day to show (default today)."
        )
        assert library_lint.default_stated(exhaustive, listing, "radius")
        assert library_lint.default_stated(exhaustive, listing, "page")
        assert not library_lint.default_stated(exhaustive, listing, "lat")
        assert not library_lint.default_stated(standard, listing, "page")
        assert not library_lint.default_stated(exhaustive, listing, "date")  # stated for another endpoint
        assert library_lint.default_stated(exhaustive, {"method": "GET", "path": "/api/days"}, "date")
        assert library_lint.qualifies_other_parameter(standard, listing, "radius")
        assert not library_lint.qualifies_other_parameter(standard, listing, "page")
        assert not library_lint.qualifies_other_parameter(exhaustive, listing, "radius")

    def test_auth_contract_endpoints_need_no_status_in_the_prose(self):
        content = {
            "endpoints": [{"id": "auth.register", "method": "POST", "path": "/api/auth/register"}],
            "prose": {"standard": "`POST /api/auth/register` creates an account."},
        }
        check = {
            "id": "auth.register.ok",
            "category": "functional",
            "endpoint": "auth.register",
            "expect": {"status": 201},
        }
        check["request"] = {"method": "POST", "path": "/api/auth/register", "json": {}}
        assert check_statedness(content, [check]) == {"auth.register.ok": ["standard"]}

    def test_the_rule_can_be_compared_with_an_authors_list(self):
        content = self.content()
        content["acceptance"] = [
            {**self.check("refused", status=409, category="functional"), "stated_in": ["standard", "exhaustive"]},
        ]
        assert check_statedness(content) == {"refused": ["standard", "exhaustive"]}
        assert check_statedness(content, explicit=False) == {"refused": ["exhaustive"]}

    def test_version(self):
        assert library_lint.STATEDNESS_VERSION == 3
