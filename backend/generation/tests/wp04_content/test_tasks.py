"""WP-04 task content (spec 11.2, 11.3, 12.3): 33 rewritten + new tasks, three prose variants each, consistent
with the structured part, closed vocabulary, and not every task with an admin surface."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.generation.contracts.library import PROSE_VARIANTS
from backend.generation.contracts.library import TASK_SCHEMA_PATH
from backend.generation.contracts.library import TaskSpecContent
from backend.generation.contracts.library import render_task_schema
from backend.generation.contracts.library import validate_content
from backend.generation.tests.wp04_content import content

SLUGS = content.task_slugs()


def test_every_v2_task_is_rewritten_and_the_new_tasks_exist():
    assert set(content.V2_TASKS) <= set(SLUGS)
    assert set(content.NEW_TASKS) <= set(SLUGS)
    assert len(content.NEW_TASKS) >= 10
    assert len(SLUGS) >= len(content.V2_TASKS) + 10
    assert set(content.KIT_TASKS) <= set(SLUGS)


def test_task_directories_hold_only_the_documented_layout():
    for slug in SLUGS:
        directory = content.TASKS_ROOT / slug
        names = {path.name for path in directory.iterdir() if path.name != "__pycache__"}
        # ``tester.yaml``: the instrument overlay of the acceptance compiler (never part of the task content)
        assert names <= {"task.yaml", "prose", "reference", "tester.yaml"}, slug
        assert {path.name for path in (directory / "prose").iterdir()} == {f"{v}.md" for v in PROSE_VARIANTS}, slug


@pytest.mark.parametrize("slug", SLUGS)
def test_task_validates_against_the_contract(slug):
    parsed = TaskSpecContent.model_validate(content.task(slug))
    assert set(parsed.prose) == set(PROSE_VARIANTS)
    validate_content("task", content.task(slug))


def test_task_yaml_never_holds_prose_or_v2_keys():
    v2_keys = {
        "backend_requirements",
        "frontend_requirements",
        "admin_requirements",
        "api_endpoints",
        "data_models",
        "difficulty",
    }
    for slug in SLUGS:
        raw = content._yaml(content.TASKS_ROOT / slug / "task.yaml")
        assert "prose" not in raw, slug
        assert not v2_keys & set(raw), slug


@pytest.mark.parametrize("slug", SLUGS)
def test_prose_is_consistent_with_the_structured_part(slug):
    assert content.prose_issues(slug, content.task(slug)) == []


@pytest.mark.parametrize("slug", SLUGS)
def test_structure_rules(slug):
    assert content.structure_issues(slug, content.task(slug)) == []


def test_ids_are_unique_within_each_task():
    for slug, task in content.tasks().items():
        for group, key in (
            ("endpoints", "id"),
            ("ui", "id"),
            ("acceptance", "id"),
            ("probes", "id"),
            ("entities", "name"),
        ):
            ids = [item[key] for item in task.get(group) or []]
            assert len(ids) == len(set(ids)), (slug, group)
        routes = [(e["method"], e["path"]) for e in task["endpoints"]]
        assert len(routes) == len(set(routes)), slug


def test_at_least_ten_tasks_have_no_admin_surface():
    without = [
        s
        for s, t in content.tasks().items()
        if "admin" not in t["features"] and all(e["auth"] != "admin" for e in t["endpoints"])
    ]
    assert len(without) >= 10, without
    # The uniform v2 template (two admin endpoints + is_active toggle on every task) is gone.
    with_admin = [s for s, t in content.tasks().items() if "admin" in t["features"]]
    assert len(with_admin) < len(SLUGS) / 2


def test_no_v2_uniform_template_remnants():
    for slug, task in content.tasks().items():
        paths = [endpoint["path"] for endpoint in task["endpoints"]]
        assert "/api/health" not in paths, f"{slug}: the health route belongs to the run contract"
        for entity in task["entities"]:
            assert "is_active" not in {f["name"] for f in entity["fields"]} or entity["name"] in {"Product", "User"}, (
                slug,
                entity["name"],
            )


def test_minimal_prose_states_no_security_auth_detail_or_error_format():
    pattern = re.compile(r"bearer|cookie|csrf|hash|jwt|token|\{\"detail\"|sanitiz|escape|validation", re.I)
    for slug, task in content.tasks().items():
        description = task["prose"]["minimal"].split("\n\nEndpoints:", 1)[0]
        assert not pattern.search(description), slug


def test_prose_never_prescribes_mechanisms_or_libraries():
    pattern = re.compile(
        r"\b(?:bcrypt|argon2|pbkdf2|jwt|bleach|dompurify|helmet|csurf|flask-login|passport|sqlalchemy"
        r"|prepared statement|parameteri[sz]ed)\b",
        re.I,
    )
    for slug, task in content.tasks().items():
        for variant, text in task["prose"].items():
            assert not pattern.search(text), (slug, variant)


def test_variants_grow_in_detail():
    for slug, task in content.tasks().items():
        prose = task["prose"]
        assert (
            content.word_count(prose["minimal"])
            < content.word_count(prose["standard"])
            < content.word_count(prose["exhaustive"])
        ), slug


def test_tasks_cover_broad_security_surfaces_by_tags():
    vocab = content.vocabulary()
    used_features = {f for t in content.tasks().values() for f in t["features"]}
    used_surfaces = {s for t in content.tasks().values() for s in t["surfaces"]}
    # Every feature tag except `sse` (no seed task streams events yet) is exercised by some task.
    assert set(vocab["features"]) - used_features <= {"sse"}
    assert len(used_surfaces) >= 45
    probe_kinds = {p["kind"] for t in content.tasks().values() for p in t.get("probes") or []}
    assert len(probe_kinds) >= 14, sorted(probe_kinds)


def test_new_tasks_carry_their_section_11_2_surfaces():
    expected = {
        "notes_markdown_render": ({"render-markdown", "render-html"}, {"cwe-79"}),
        "file_share_multipart": ({"upload-multipart"}, {"cwe-22", "cwe-434"}),
        "account_password_reset": ({"password-reset", "email-mock"}, {"cwe-640"}),
        "banking_transfers_cookie": ({"csrf"}, {"cwe-352"}),
        "link_preview_fetcher": ({"outbound-http"}, {"cwe-918"}),
        "login_rate_limited": ({"rate-limit"}, {"cwe-307"}),
        "team_roles_multitenant": ({"roles"}, {"cwe-639", "cwe-285"}),
        "csv_report_export": ({"csv-export"}, {"cwe-1236"}),
        "webhook_receiver_signed": ({"webhook-signature"}, {"cwe-347"}),
        "search_catalog_sql": ({"search", "filter"}, {"cwe-89"}),
    }
    for slug, (features, surfaces) in expected.items():
        task = content.task(slug)
        assert features <= set(task["features"]), slug
        assert surfaces <= set(task["surfaces"]), slug
    banking = content.task("banking_transfers_cookie")["auth_flow"]
    assert banking["credential"] == "cookie"
    assert banking["csrf"] == "header_from_endpoint"


def test_owner_endpoints_name_owned_entities():
    for slug, task in content.tasks().items():
        owned = {e["name"] for e in task["entities"] if e.get("owned_by_user")}
        for endpoint in task["endpoints"]:
            if endpoint["auth"] == "owner":
                assert endpoint.get("owner_entity") in owned, (slug, endpoint["id"])


def test_acceptance_checks_reference_only_known_endpoints_and_captures():
    for slug, task in content.tasks().items():
        ids = {e["id"] for e in task["endpoints"]}
        for check in task.get("acceptance") or []:
            assert check.get("endpoint") is None or check["endpoint"] in ids, (slug, check["id"])
            captured: set[str] = set()
            for step in check.get("setup") or []:
                assert _placeholders(step["request"]) <= captured, (slug, check["id"])
                captured |= set(step.get("capture") or {})
            assert _placeholders(check["request"]) <= captured, (slug, check["id"])


def _placeholders(request: dict) -> set[str]:
    """``{name}`` captures used in the path and in whole-string JSON body values."""
    used = set(re.findall(r"\{(\w+)\}", request["path"]))
    body = request.get("json")
    if isinstance(body, dict):
        used |= {m.group(1) for v in body.values() if isinstance(v, str) and (m := re.fullmatch(r"\{(\w+)\}", v))}
    return used


_TICK = re.compile(r"`([^`]+)`")
_JSON_KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')


def _stated_names(text: str) -> set[str]:
    """What the prose names: a backticked span, or a JSON key inside a backticked span."""
    names: set[str] = set()
    for span in _TICK.findall(text):
        names.add(span.strip())
        names.update(_JSON_KEY.findall(span))
    return names


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("variant", ["standard", "exhaustive"])
def test_prose_names_every_field_the_structured_part_requires(slug, variant):
    """Every response_schema ``required`` key (also of list items) and every request_schema property is named
    in backticks, so the compiled response-schema checks are satisfiable from the prose alone."""
    task = content.task(slug)
    names = _stated_names(task["prose"][variant])
    missing = []
    for endpoint in task["endpoints"]:
        response = endpoint.get("response_schema") or {}
        missing += [(endpoint["id"], key) for key in response.get("required") or [] if key not in names]
        item = ((response.get("properties") or {}).get("items") or {}).get("items") or {}
        missing += [(endpoint["id"], f"items[].{key}") for key in item.get("required") or [] if key not in names]
        body = (endpoint.get("request_schema") or {}).get("properties") or {}
        missing += [(endpoint["id"], f"body.{key}") for key in body if key not in names]
    assert not missing, missing


def test_hand_set_stated_in_matches_the_prose():
    """A hand-written ``stated_in`` is kept by lint, so every JSON key the check asserts must be named in each
    variant it claims."""
    for slug, task in content.tasks().items():
        for check in task.get("acceptance") or []:
            keys = _keys(check["expect"].get("json_contains") or {})
            for variant in check.get("stated_in") or []:
                missing = keys - _stated_names(task["prose"][variant])
                assert not missing, (slug, check["id"], variant, sorted(missing))


def test_mass_assignment_probes_inject_fields_the_endpoint_does_not_accept():
    for slug, task in content.tasks().items():
        endpoints = {e["id"]: e for e in task["endpoints"]}
        for probe in task.get("probes") or []:
            if probe["kind"] != "mass_assignment":
                continue
            accepted = (endpoints[probe["endpoint"]].get("request_schema") or {}).get("properties") or {}
            assert probe["params"]["field"] not in accepted, (slug, probe["id"])


def test_prose_leaves_the_auth_mechanism_to_the_auth_contract():
    """The auth mode is a clean factor: no prose variant prescribes credentials or CSRF transport."""
    pattern = re.compile(r"X-CSRF-Token|Authorization:|Bearer|csrf_token`? cookie|session cookie", re.I)
    for slug, task in content.tasks().items():
        for variant, text in task["prose"].items():
            assert not pattern.search(text), (slug, variant)


def test_webhook_signature_scheme_is_stated_in_every_variant():
    """The signature scheme is an interoperability contract (the tester signs its events), not a detail."""
    prose = content.task("webhook_receiver_signed")["prose"]
    for variant, text in prose.items():
        for needle in ("X-Signature", "X-Timestamp", "HMAC-SHA256", "<raw", "WEBHOOK_SECRET"):
            assert needle in text, (variant, needle)


def test_team_roles_cover_owner_escalation_with_an_authz_check():
    task = content.task("team_roles_multitenant")
    check = next(c for c in task["acceptance"] if c["id"] == "members.admin_cannot_add_owner")
    assert check["endpoint"] == "members.add"
    assert check["request"]["json"]["role"] == "owner"
    assert check["expect"]["status"] == 403
    assert set(check["stated_in"]) == {"standard", "exhaustive"}


def test_task_schema_json_is_the_generated_one():
    """``data/tasks/schema.json`` is WP-01's generated file: WP-04 never hand-edits it."""
    assert Path(TASK_SCHEMA_PATH).read_text(encoding="utf-8") == render_task_schema()
    assert json.loads(Path(TASK_SCHEMA_PATH).read_text())["$id"].endswith("/v4")


def test_tasks_lint_clean_with_wp03_lint_or_fallback():
    """``lint_library`` clean: no error or warning issue on any task (WP-03's lint when it exists)."""
    try:
        from backend.generation.library import lint as library_lint

        vocab = library_lint.load_vocabulary(content.DATA_ROOT)
        results = {
            slug: library_lint.lint("task", task, system=True, resolve=False, vocabulary=vocab)
            for slug, task in content.tasks().items()
        }
    except NotImplementedError:
        pytest.skip("WP-03 task lint not implemented: covered by the fallback tests above")
    noisy = {
        slug: [(i.code, i.message) for i in issues if i.severity in {"error", "warn"}]
        for slug, issues in results.items()
    }
    assert {slug: found for slug, found in noisy.items() if found} == {}


def test_reference_trees_follow_the_layout():
    """``tasks/<slug>/reference/<stack>/`` (wave 3): a known stack, a Dockerfile and a PROVENANCE.md."""
    for slug in SLUGS:
        root = content.TASKS_ROOT / slug / "reference"
        if not root.is_dir():
            continue
        for tree in root.iterdir():
            assert tree.name in content.stacks(), (slug, tree.name)
            assert (tree / "Dockerfile").is_file(), (slug, tree.name)
            assert (tree / "PROVENANCE.md").is_file(), (slug, tree.name)


# Section 8.5: the kit frameworks whose conformance-kit references are checked in. The WP-09 trees were written by
# hand; the go-stdlib and spring-boot trees (E2E-25) were written by an AI coding assistant, which their
# PROVENANCE.md says (no generation run and no model under study was involved).
KIT_REFERENCE_STACKS = ("flask-none", "fastapi-none", "django-none", "express-none")
KIT_REFERENCE_STACKS_ASSISTED = ("go-none", "spring-boot-none")


@pytest.mark.parametrize("slug", content.KIT_TASKS)
def test_kit_tasks_have_a_reference_for_every_phase_a_framework(slug):
    """Without these trees ``conformance.coverage()`` is all-False and confirmatory app studies are blocked."""
    root = content.TASKS_ROOT / slug / "reference"
    for stack in KIT_REFERENCE_STACKS + KIT_REFERENCE_STACKS_ASSISTED:
        tree = root / stack
        assert (tree / "Dockerfile").is_file(), (slug, stack)
        assert (tree / "backend").is_dir(), (slug, stack)
        provenance = (tree / "PROVENANCE.md").read_text(encoding="utf-8")
        if stack in KIT_REFERENCE_STACKS:
            assert "No LLM was involved" in provenance, (slug, stack)
        else:
            assert "no model under study was involved" in provenance, (slug, stack)
        assert not list(tree.rglob("__pycache__")), (slug, stack)


def test_a_check_not_run_as_user_a_names_the_actor_of_every_setup_step():
    """A setup step without ``actor`` runs as the check's actor (WP-09 convention): for an ``anon``/``user_b``
    check that silently makes the setup anonymous or the other tenant's, so a correct app fails it."""
    implicit = [
        (slug, check["id"], index)
        for slug in SLUGS
        for check in content.task(slug).get("acceptance") or []
        if check.get("actor", "user_a") != "user_a"
        for index, step in enumerate(check.get("setup") or [])
        if "actor" not in step
    ]
    assert implicit == []
