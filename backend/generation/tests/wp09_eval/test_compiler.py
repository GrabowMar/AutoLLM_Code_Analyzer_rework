"""WP-09: the acceptance compiler (spec 8.1-8.4): snapshots for 3 tasks x 3 auth modes and the merge rules.

Snapshots live in ``snapshots/<task>.<auth_mode>.json``; regenerate them (after an intended compiler change,
with a ``COMPILER_VERSION`` bump) with ``WP09_UPDATE_SNAPSHOTS=1``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.generation.contracts.library import TaskSpecContent
from backend.generation.evaluation import acceptance
from backend.generation.evaluation import probes as probe_catalog

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOTS = Path(__file__).with_name("snapshots")
TASKS = ("crud_todo_list", "team_roles_multitenant", "file_share_multipart")
STACK = yaml.safe_load((DATA / "stacks" / "flask-react.yaml").read_text())["content"]


def task_content(slug: str) -> dict[str, Any]:
    content = yaml.safe_load((DATA / "tasks" / slug / "task.yaml").read_text())
    content["prose"] = {
        p.stem: p.read_text().rstrip("\n") for p in sorted((DATA / "tasks" / slug / "prose").glob("*.md"))
    }
    return TaskSpecContent.model_validate(content).model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("mode", acceptance.AUTH_MODES)
@pytest.mark.parametrize("slug", TASKS)
def test_compiler_snapshot(slug: str, mode: str) -> None:
    plan = acceptance.plan_json(acceptance.compile_content(task_content(slug), mode, STACK))
    path = SNAPSHOTS / f"{slug}.{mode}.json"
    text = json.dumps(plan, indent=1, sort_keys=True) + "\n"
    if os.environ.get("WP09_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"compiler output for {slug}/{mode} changed; bump COMPILER_VERSION"


def test_plan_shape_and_rules() -> None:
    content = task_content("crud_todo_list")
    plan = acceptance.compile_content(content, "contract_bearer", STACK)
    ids = [c.id for c in plan.checks]
    assert ids[0] == "health"
    assert {"index_served", "spa_fallback"} <= set(ids)
    assert {"todos.get.ok", "todos.get.anon_401", "todos.get.other_user", "todos.create.invalid.missing"} <= set(ids)
    assert "todos.list.page_size" in ids
    assert "todos.list.filter.priority" in ids
    assert "todos.get.persists" in ids
    by_id = {c.id: c for c in plan.checks}
    assert by_id["todos.get.other_user"].expect.status == 404  # errors lists 404 only
    assert by_id["todos.create.invalid.missing"].expect.status == [400, 422]
    assert by_id["todos.get.persists"].restart_before is True
    assert all(c.depends_on == ["health"] for c in plan.checks if c.id != "health")
    # the hand-written check is merged and keeps its statedness
    assert by_id["todos.list.open_first"].source == "acceptance"
    assert by_id["todos.list.open_first"].stated_in == ["standard", "exhaustive"]
    # compiled checks are tagged by the lint rules
    assert any(c.stated_in for c in plan.checks if c.source == "compiler")
    # no secret in the plan
    assert "ADMIN_PASSWORD" not in json.dumps(acceptance.plan_json(plan))


def test_hand_written_check_wins_on_id_collision() -> None:
    content = task_content("crud_todo_list")
    content["acceptance"].append(
        {
            "id": "todos.create.ok",
            "category": "functional",
            "endpoint": "todos.create",
            "request": {"method": "POST", "path": "/api/todos", "json": {"title": "mine"}},
            "expect": {"status": 201},
        },
    )
    plan = acceptance.compile_content(content, "contract_bearer", STACK)
    check = next(c for c in plan.checks if c.id == "todos.create.ok")
    assert check.source == "acceptance"
    body = check.request.json_body
    assert body["title"] == "mine"  # what the hand-written check sets is never changed
    # round 3: the properties it omits are completed with the compiler's valid values
    assert set(body) == {"title", "description", "priority", "due_date", "completed"}


def test_cookie_and_spec_defined_drivers() -> None:
    content = task_content("crud_todo_list")
    cookie = acceptance.compile_content(content, "cookie_session", STACK).auth
    assert cookie.credential == "cookie"
    assert cookie.csrf == "double_submit_cookie"
    spec = acceptance.compile_content(content, "spec_defined", STACK).auth
    assert spec.login_path == "/api/auth/login"
    assert spec.register_path == "/api/auth/register"


def test_probes_get_preconditions_and_controls() -> None:
    plan = acceptance.compile_content(task_content("crud_todo_list"), "contract_bearer", STACK)
    idor = next(p for p in plan.probes if p.kind == "idor_enumeration")
    assert idor.requires_checks == ["todos.get.ok"]
    assert idor.positive_control is not None
    plaintext = next(p for p in plan.probes if p.kind == "plaintext_password_db")
    assert plaintext.requires_checks == ["auth.login.ok"]


def test_unvalidated_probe_kinds_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_catalog, "kind_validated", lambda kind, record=None: kind != "idor_enumeration")
    plan = acceptance.compile_content(task_content("crud_todo_list"), "contract_bearer", STACK)
    idor = next(p for p in plan.probes if p.kind == "idor_enumeration")
    assert idor.params["disabled"] is True
    assert "probe_unvalidated" in idor.params["disabled_reason"]


def test_public_checks_create_their_resource_visible() -> None:
    """E2E-25: an anon check reads what user_a created, so its setup sends visibility flags as ``true``.

    The boolean sample value is ``false``: the public page of an unpublished note (and an inactive product) is a
    404 by the task's own rules, so every correct app failed ``public.render.ok`` (``oracle_validate``).
    """
    notes = {
        c.id: c
        for c in acceptance.compile_content(task_content("notes_markdown_render"), "contract_bearer", STACK).checks
    }
    assert notes["public.render.ok"].setup[-1].request.json_body["published"] is True
    # an owner-only check keeps the sample value (the flag is not about who may read it)
    assert notes["notes.get.anon_401"].setup[-1].request.json_body["published"] is False
    shop = {
        c.id: c for c in acceptance.compile_content(task_content("ecommerce_cart"), "contract_bearer", STACK).checks
    }
    assert shop["products.get.ok"].setup[-1].request.json_body["is_active"] is True


def test_all_applicable_adds_generic_probes() -> None:
    content = task_content("crud_todo_list")
    plan = acceptance.compile_content(content, "contract_bearer", STACK, probes="all_applicable")
    kinds = {p.kind for p in plan.probes}
    assert {"idor_enumeration", "mass_assignment", "plaintext_password_db", "rate_limit_missing"} <= kinds
    none = acceptance.compile_content(content, "contract_bearer", STACK, probes="none")
    assert none.probes == []


def test_webhook_routes_are_signed() -> None:
    plan = acceptance.compile_content(task_content_webhook(), "contract_bearer", STACK)
    assert plan.signed_routes == [{"method": "POST", "path": "/api/webhooks/payments"}]


def task_content_webhook() -> dict[str, Any]:
    return task_content("webhook_receiver_signed")


@pytest.mark.django_db
def test_checks_for_endpoints_lists_compiled_and_hand_written_checks() -> None:
    from backend.generation.testing.factories import TaskSpecVersionFactory

    version = TaskSpecVersionFactory(content=task_content("crud_todo_list"))
    checks = acceptance.checks_for_endpoints(version, ["todos.list"])
    assert "todos.list.ok" in checks
    assert "todos.list.open_first" in checks
    assert all(c.startswith("todos.list") for c in checks)
    functional = acceptance.checks_for_endpoints(version, ["todos.list"], categories=["functional"])
    assert "todos.list.anon_401" not in functional
    assert "todos.list.ok" in functional


# =====================================================================================
# Compiler v2 (the instrument fix): semantic samples, fresh creates, the instrument overlay
# =====================================================================================

ALL_TASKS = tuple(sorted(p.parent.name for p in (DATA / "tasks").glob("*/task.yaml")))


def checks_of(slug: str, mode: str = "contract_bearer", **kwargs: Any) -> dict[str, Any]:
    plan = acceptance.plan_json(acceptance.compile_content(task_content(slug), mode, STACK, **kwargs))
    return {c["id"]: c for c in plan["checks"]}


def overlay(**parts: Any) -> acceptance.Overlay:
    parsed, issues = acceptance.parse_overlay({"schema": 1, "notes": "test overlay", **parts}, "test")
    assert issues == []
    return parsed


def test_compiler_version_is_two() -> None:
    assert acceptance.COMPILER_VERSION == 2


def test_placeholder_table_matches_the_tester() -> None:
    from backend.generation.evaluation.tester import tester_module

    tester = tester_module()
    generators = set(tester.GENERATORS) - {"digitsN"}
    assert generators <= set(acceptance.PLACEHOLDER_EXAMPLES)
    assert set(acceptance.PER_CONTEXT) <= generators
    assert acceptance.known_placeholder("digits4")
    assert acceptance.known_placeholder("digits18")
    assert not acceptance.known_placeholder("digits19")
    assert not acceptance.known_placeholder("nonsense")
    # every example has the shape (and, for fixed-width generators, the length) of the tester's value
    ctx = {"rand": "0a1b2c3d", "_epoch": 1_900_000_000}
    for name in sorted(generators - {"oversize"} - set(tester._FILES) - set(tester._BIG_FILES)):
        value = tester.resolve(name, ctx)
        assert isinstance(value, str)
        assert len(value) == len(acceptance.PLACEHOLDER_EXAMPLES[name]), name


@pytest.mark.parametrize("slug", ALL_TASKS)
def test_every_task_compiles_and_valid_bodies_satisfy_their_request_schema(slug: str) -> None:
    """P2: the body of every compiled ``<endpoint>.ok`` check is valid under the endpoint's request schema."""
    content = task_content(slug)
    endpoints = {e["id"]: e for e in content["endpoints"]}
    for mode in acceptance.AUTH_MODES:
        plan = acceptance.plan_json(acceptance.compile_content(content, mode, STACK))
        assert plan["compiler_version"] == 2
        assert plan["skipped"] == []
        # without an overlay the only recorded adjustments are the compiler's own, each with its reason:
        # hand-written setup creates that accept 200 and 201 (round 2), completed hand-written requests and
        # reference properties that accept the id types of the referenced entity (round 3)
        for entry in plan["adjusted"]:
            assert entry["why"]
            assert set(entry) - {"id", "why"} in ({"setup_status"}, {"completed"}, {"reference_types"}), entry
        assert not any(c["overlay"] for c in plan["checks"])
        for check in plan["checks"]:
            captured = {name for step in check["setup"] for name in step.get("capture") or {}}
            unknown = {n for n in acceptance.placeholder_names(check["request"].get("json")) if n != "oversize"}
            # a listed resource id ({listed_<field>}) is read by a setup step of the same check
            assert all(n in captured for n in unknown if n.startswith("listed_")), (check["id"], unknown)
            assert all(acceptance.known_placeholder(n) for n in unknown - captured), (check["id"], unknown)
            if check["source"] != "compiler" or not check["id"].endswith(".ok"):
                continue
            schema = (endpoints.get(check["endpoint"]) or {}).get("request_schema")
            body = check["request"].get("json")
            if not isinstance(schema, dict) or not isinstance(body, dict):
                continue
            rendered = _with_listed_ids(acceptance.render_example(body))
            assert acceptance.schema_violations(rendered, schema, check["id"]) == [], check["id"]


def _with_listed_ids(value: Any) -> Any:
    """``value`` with every ``{listed_<field>}`` (an id the app lists; typed integer or string) as ``1``."""
    if isinstance(value, str) and value.startswith("{listed_") and value.endswith("}"):
        return 1
    if isinstance(value, dict):
        return {k: _with_listed_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_with_listed_ids(v) for v in value]
    return value


def test_semantic_samples() -> None:
    books = checks_of("crud_book_library")
    body = books["books.create.ok"]["request"]["json"]
    assert body["isbn"] == "{isbn13}"
    assert isinstance(body["year"], int)
    assert 1000 < body["year"] < 2026
    crm = checks_of("crm_customer_list")
    customer = crm["customers.create.ok"]["request"]["json"]
    assert customer["phone"] == "+1202555{digits4}"
    assert "{rand}" in customer["email"]
    bank = checks_of("banking_transfers_cookie")
    transfer = bank["transfers.create.ok"]["request"]["json"]
    # the recipient is a third account: never the sender and never the "other user" of the ownership checks
    assert transfer["to_username"] == "{user_c_username}"


def test_end_fields_come_after_start_fields() -> None:
    """An end date/time uses the ``...2`` generator, which the tester renders later than the start's.

    Both are generated from the same context, so this also holds for an update body that carries only the end
    (the resource was created with the start of the same context). No body uses one generator for two fields.
    """
    pairs = (
        ("{future_date}", "{future_date2}"),
        ("{future_datetime}", "{future_datetime2}"),
        ("{time_hhmm}", "{time_hhmm2}"),
    )
    both = 0
    for slug in ALL_TASKS:
        for check in checks_of(slug).values():
            body = check["request"].get("json")
            if check["source"] != "compiler" or not isinstance(body, dict):
                continue
            values = [v for v in body.values() if isinstance(v, str)]
            for start, end in pairs:
                assert values.count(start) <= 1, (slug, check["id"], start)
                assert values.count(end) <= 1, (slug, check["id"], end)
                both += start in values and end in values
    assert both > 0


def test_repeated_creates_are_fresh_and_setup_accepts_200_and_201() -> None:
    crm = checks_of("crm_customer_list")
    page = crm["customers.list.page_size"]["setup"]
    assert len(page) == 3
    assert all(step["fresh"] for step in page)
    assert all(step["expect_status"] == [200, 201] for step in page)
    search = crm["customers.list.search"]["setup"]
    assert [step["fresh"] for step in search] == [False, True]  # the decoy never shares a unique value
    # the measured check keeps the exact status
    assert crm["customers.create.ok"]["expect"]["status"] == 201
    assert crm["customers.create.ok"]["setup"] == []


def test_invalid_checks_break_one_field_and_run_last() -> None:
    crm = checks_of("crm_customer_list")
    valid = crm["customers.create.ok"]["request"]["json"]
    too_long = crm["customers.create.invalid.too_long"]["request"]["json"]
    changed = [k for k in valid if too_long.get(k) != valid[k]]
    assert len(changed) == 1
    assert set(too_long) == set(valid)
    missing = crm["customers.create.invalid.missing"]["request"]["json"]
    assert len(set(valid) - set(missing)) == 1
    assert all(missing[k] == valid[k] for k in missing)
    ids = list(crm)
    first_invalid = min(i for i, cid in enumerate(ids) if ".invalid." in cid and crm[cid]["source"] == "compiler")
    assert all(".invalid." in cid for cid in ids[first_invalid:])


def test_a_too_long_url_is_still_a_url() -> None:
    previews = checks_of("link_preview_fetcher")
    url = previews["previews.create.invalid.too_long"]["request"]["json"]["url"]
    assert url.startswith("http")
    assert len(acceptance.render_example(url)) == 2049


def test_the_plan_does_not_depend_on_key_order() -> None:
    content = task_content("crud_book_library")

    def reverse(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: reverse(value[k]) for k in reversed(list(value))}
        if isinstance(value, list):
            return [reverse(v) for v in value]
        return value

    first = acceptance.plan_json(acceptance.compile_content(content, "contract_bearer", STACK))
    second = acceptance.plan_json(acceptance.compile_content(reverse(content), "contract_bearer", STACK))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_overlay_structure_is_strict() -> None:
    def issues(raw: Any) -> list[str]:
        return acceptance.parse_overlay(raw, "t")[1]

    good = {"schema": 1, "notes": "why", "samples": {"title": "x {rand}"}}
    assert issues(good) == []
    assert any("unknown key 'fixtures'" in i for i in issues({**good, "fixtures": {}}))
    step = {"request": {"method": "POST", "path": "/api/todos", "json": {"title": "x"}}, "capture": {"id": "$.id"}}
    assert issues({**good, "probes": {"probe.todos.idor": {"why": "w", "setup": [step]}}}) == []
    assert any("`why` is required" in i for i in issues({**good, "probes": {"probe.todos.idor": {"setup": [step]}}}))
    assert any("changes nothing" in i for i in issues({**good, "probes": {"probe.todos.idor": {"why": "w"}}}))
    # only the setup of a probe can be patched: never its attack, its control or its verdict
    for key in ("target", "positive_control", "requires_checks", "params"):
        patch = {"why": "w", "setup": [step], key: {}}
        assert any(f"unknown key {key!r}" in i for i in issues({**good, "probes": {"probe.todos.idor": patch}}))
    bad = {**step, "request": {"method": "GET", "path": "/api/todos/{nope}"}}
    patched = {**good, "probes": {"probe.todos.idor": {"why": "w", "setup": [bad]}}}
    assert any("unknown placeholder {nope}" in i for i in issues(patched))
    assert any("schema" in i for i in issues({**good, "schema": 2}))
    assert any("notes" in i for i in issues({"schema": 1, "samples": {}}))
    assert any("unknown placeholder {nope}" in i for i in issues({**good, "samples": {"title": "{nope}"}}))
    assert any("unknown key 'body'" in i for i in issues({**good, "endpoints": {"todos.create": {"body": {}}}}))
    no_why = {**good, "checks": {"todos.create.ok": {"request": {"json": {"title": "x"}}}}}
    assert any("`why` is required" in i for i in issues(no_why))
    unknown = {**good, "checks": {"todos.create.ok": {"why": "w", "retries": 3}}}
    assert any("unknown key 'retries'" in i for i in issues(unknown))
    nothing = {**good, "checks": {"todos.create.ok": {"why": "w"}}}
    assert any("changes nothing" in i for i in issues(nothing))
    both = {**good, "checks": {"todos.create.ok": {"why": "w", "skip": True, "expect": {"status": 200}}}}
    assert any("takes no other patch" in i for i in issues(both))


def test_overlay_ids_and_samples_are_checked_against_the_task() -> None:
    content = task_content("crm_customer_list")

    def problems(**parts: Any) -> list[str]:
        with pytest.raises(acceptance.OverlayError) as caught:
            acceptance.compile_content(content, "contract_bearer", STACK, overlay=overlay(**parts))
        return caught.value.issues

    assert any("no such check" in i for i in problems(checks={"nope.ok": {"why": "w", "skip": True}}))
    assert any("no such endpoint" in i for i in problems(endpoints={"nope.create": {"json": {"a": 1}}}))
    assert any("no endpoint of the task" in i for i in problems(samples={"nonexistent_field": "x"}))
    # a sample must be valid under every request schema that has the field ...
    assert any("violates" in i for i in problems(samples={"email": "not-an-email {rand}"}))
    assert any("violates" in i for i in problems(samples={"phone": "+1202555{digits4}" + "0" * 40}))
    # ... and a unique field needs a per-context generator
    assert any("per-context generator" in i for i in problems(samples={"email": "fixed@example.com"}))
    assert any(
        "not a valid check" in i
        for i in problems(checks={"customers.create.ok": {"why": "w", "expect": {"statuz": 201}}})
    )


def test_overlay_precedence_and_flags() -> None:
    plain = checks_of("crm_customer_list")
    patched = checks_of(
        "crm_customer_list",
        overlay=overlay(
            samples={"company": "sample {rand}", "phone": "+1202555{digits4}"},
            endpoints={"customers.create": {"json": {"company": "endpoint co", "notes": None}}},
            checks={"customers.create.ok": {"why": "w", "request": {"json": {"company": "check co"}}}},
        ),
    )
    # checks.<id>.request > endpoints.<id> > samples.<field> > the sampler
    assert patched["customers.create.ok"]["request"]["json"]["company"] == "check co"
    assert "notes" not in patched["customers.create.ok"]["request"]["json"]
    assert patched["customers.get.ok"]["setup"][-1]["request"]["json"]["company"] == "endpoint co"
    replace = patched["customers.replace.ok"]["request"]["json"]
    assert replace["company"] == "sample {rand}"
    assert patched["customers.create.ok"]["overlay"] is True
    assert patched["customers.replace.ok"]["overlay"] is True
    # a sample equal to the sampler's value changes nothing and flags nothing
    untouched = [cid for cid, c in patched.items() if not c["overlay"]]
    assert untouched
    assert all(plain[cid] == patched[cid] for cid in untouched)


def test_overlay_skip_and_adjusted_are_reported() -> None:
    content = task_content("crud_todo_list")
    plain = acceptance.compile_content(content, "contract_bearer", STACK)
    plan = acceptance.compile_content(
        content,
        "contract_bearer",
        STACK,
        slug="crud_todo_list",
        overlay=overlay(
            checks={
                "todos.get.ok": {"why": "the precondition is dropped", "skip": True},
                "todos.create.ok": {"why": "the spec allows both", "expect": {"status": [200, 201]}},
                "todos.list.open_first": {"why": "only the exhaustive prose says so", "stated_in": ["minimal"]},
            },
        ),
    )
    data = acceptance.plan_json(plan)
    assert data["skipped"] == [{"id": "todos.get.ok", "why": "the precondition is dropped"}]
    ids = [c["id"] for c in data["checks"]]
    assert "todos.get.ok" not in ids
    assert len(ids) == len(plain.checks) - 1
    # a skipped check is nobody's precondition any more
    assert all("todos.get.ok" not in p["requires_checks"] for p in data["probes"])
    adjusted = {a["id"]: a for a in data["adjusted"]}
    assert adjusted["todos.create.ok"]["expect"]["before"]["status"] == 201
    assert adjusted["todos.create.ok"]["expect"]["after"]["status"] == [200, 201]
    assert adjusted["todos.create.ok"]["why"] == "the spec allows both"
    assert adjusted["todos.list.open_first"]["stated_in"]["after"] == ["minimal", "standard", "exhaustive"]
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["todos.create.ok"]["overlay"] is True
    assert by_id["todos.list.ok"]["overlay"] is False


def test_upstream_is_attached_only_when_the_plan_names_it() -> None:
    content = task_content("link_preview_fetcher")
    assert acceptance.plan_json(acceptance.compile_content(content, "contract_bearer", STACK))["upstream"] is None
    plan = acceptance.plan_json(
        acceptance.compile_content(
            content,
            "contract_bearer",
            STACK,
            slug="link_preview_fetcher",
            overlay=overlay(samples={"url": "{upstream_base}/page/llmlab-{rand}"}),
        ),
    )
    assert plan["upstream"] == {"hosts": ["example.com", "www.example.com", "example.org"], "port": 80}
    by_id = {c["id"]: c for c in plan["checks"]}
    assert by_id["previews.create.ok"]["request"]["json"]["url"] == "{upstream_base}/page/llmlab-{rand}"
    # the round-trip assertion keeps the placeholder value (the tester substitutes it in the check's context)
    assert by_id["previews.get.persists"]["expect"]["json_contains"] == {"url": "{upstream_base}/page/llmlab-{rand}"}
    # legitimate probe requests use the sample too
    idor = next(p for p in plan["probes"] if p["kind"] == "idor_enumeration")
    assert "{upstream_base}" in json.dumps(idor["params"]["setup"])


def test_fixtures_digest_and_overlay_loading(tmp_path: Path) -> None:
    empty = acceptance.fixtures_digest(tmp_path)
    assert len(empty) == 64
    assert set(empty) <= set("0123456789abcdef")
    assert acceptance.fixtures_digest(tmp_path) == empty
    assert acceptance.load_overlay("crud_todo_list", tmp_path) == acceptance.Overlay()
    folder = tmp_path / "tasks" / "crud_todo_list"
    folder.mkdir(parents=True)
    (folder / "tester.yaml").write_text("schema: 1\nnotes: n\nsamples: {title: 'llmlab {rand}'}\n")
    one = acceptance.fixtures_digest(tmp_path)
    assert one != empty
    loaded = acceptance.load_overlay("crud_todo_list", tmp_path)
    assert loaded.samples == {"title": "llmlab {rand}"}
    assert acceptance.overlay_problems("crud_todo_list", task_content("crud_todo_list"), tmp_path) == []
    (folder / "tester.yaml").write_text("schema: 1\nnotes: n\nsamples: {title: 'llmlab {rand}'}\nextra: 1\n")
    assert acceptance.fixtures_digest(tmp_path) != one
    with pytest.raises(acceptance.OverlayError):
        acceptance.load_overlay("crud_todo_list", tmp_path)
    assert any("unknown key" in p for p in acceptance.overlay_problems("crud_todo_list", {}, tmp_path))
    (folder / "tester.yaml").write_text("schema: 1\nnotes: [unclosed\n")
    with pytest.raises(acceptance.OverlayError):
        acceptance.load_overlay("crud_todo_list", tmp_path)


def test_the_tree_ships_only_usable_overlays() -> None:
    """Every ``tester.yaml`` in the tree loads and applies (a broken overlay never reaches an evaluation)."""
    for path in sorted((DATA / "tasks").glob(f"*/{acceptance.OVERLAY_FILE}")):
        slug = path.parent.name
        assert acceptance.overlay_problems(slug, task_content(slug), DATA) == [], slug


# --- round 2 of the instrument fix ----------------------------------------------------------------


def test_too_long_breaks_every_field_whose_limit_the_prose_states() -> None:
    """One check id, one request per limited field, in a fixed order (required fields first)."""
    inventory = checks_of("inventory_stock_list")
    check = inventory["items.create.invalid.too_long"]
    labels = [check["label"], *(extra["label"] for extra in check["also"])]
    assert labels == ["name", "sku", "location"]  # required (schema order), then the optional one
    bodies = [check["request"]["json"], *(extra["request"]["json"] for extra in check["also"])]
    valid = inventory["items.create.ok"]["request"]["json"]
    for label, body in zip(labels, bodies, strict=True):
        changed = {k for k in body if body[k] != valid[k]}
        assert changed == {label}  # a single fault per request
    # sku is unique: its over-long value differs per context, so a wrongly stored one cannot collide later
    assert bodies[1]["sku"].endswith("{rand}")
    assert acceptance.rendered_len(bodies[1]["sku"]) == 41
    assert len(bodies[0]["name"]) == 201
    assert len(bodies[2]["location"]) == 101
    # a task whose limits live in the request schema only keeps one field (no prose states a limit)
    reset = checks_of("account_password_reset")
    assert reset["me.password.invalid.too_long"]["also"] == []
    assert reset["me.password.invalid.too_long"]["label"] is None
    # the over-long password is the valid password, padded: a strength rule cannot be what refuses it
    assert reset["me.password.invalid.too_long"]["request"]["json"]["new_password"].startswith("Tk7-{alpha}-Wq9!y")


def test_too_long_values_are_wrong_in_nothing_but_their_length() -> None:
    import base64
    import xml.etree.ElementTree as ET

    b64 = checks_of("utility_base64_tool")["base64.decode.invalid.too_long"]["request"]["json"]["encoded"]
    assert len(b64) == 140004
    assert base64.b64decode(b64, validate=True) == b"y" * 105003
    doc = checks_of("validation_xml_checker")["xml.check.invalid.too_long"]["request"]["json"]["xml"]
    assert len(doc) == 200001
    assert ET.fromstring(doc).tag == "llmlab"  # noqa: S314
    upload = checks_of("fileproc_image_upload")["images.upload.invalid.too_long"]
    assert upload["label"] == "filename"
    assert upload["request"]["json"]["filename"].endswith(".png")
    assert len(upload["request"]["json"]["filename"]) == 256
    assert upload["request"]["json"]["data"] == "{png_b64}"


def test_enum_junk_is_a_plain_word() -> None:
    body = checks_of("crud_todo_list")["todos.create.invalid.enum"]["request"]["json"]
    assert body["priority"] == "notlisted"
    assert acceptance._not_an_option({"enum": ["notlisted", "other"]}) not in ("notlisted", "other")
    assert acceptance._not_an_option({"enum": ["a"], "maxLength": 4}) == "notl"


def test_oversize_upload_is_a_valid_file_of_the_same_kind() -> None:
    oversize = checks_of("fileproc_image_upload")["images.upload.oversize"]
    assert oversize["request"]["json"]["data"] == "{png_oversize_b64}"
    assert oversize["request"]["json"]["filename"] == "llmlab-{rand}.png"
    assert oversize["expect"]["status"] == 413
    assert acceptance.known_placeholder("png_oversize_b64")
    assert acceptance.known_placeholder("wav_oversize_b64")
    assert acceptance.rendered_len("{png_oversize_b64}") is None


def test_handwritten_setup_creates_accept_200_and_201_where_the_prose_leaves_the_status_open() -> None:
    plan = acceptance.plan_json(
        acceptance.compile_content(task_content("iot_sensor_display"), "contract_bearer", STACK),
    )
    check = next(c for c in plan["checks"] if c["id"] == "readings.updates_last_value")
    assert [step["expect_status"] for step in check["setup"]] == [[200, 201], [200, 201]]
    assert check["expect"]["status"] == 200  # the expectation itself is untouched
    entries = [e for e in plan["adjusted"] if e["id"] == "readings.updates_last_value" and "setup_status" in e]
    assert [e["setup_status"] for e in entries] == [
        {"step": 0, "before": 201, "after": [200, 201]},
        {"step": 1, "before": 201, "after": [200, 201]},
    ]
    assert all("standard" in e["why"] for e in entries)
    # a check stated only in a variant that gives the status keeps the exact status
    content = task_content("iot_sensor_display")
    for hand in content["acceptance"]:
        if hand["id"] == "readings.updates_last_value":
            hand["stated_in"] = ["exhaustive"]
    plan = acceptance.plan_json(acceptance.compile_content(content, "contract_bearer", STACK))
    check = next(c for c in plan["checks"] if c["id"] == "readings.updates_last_value")
    assert [step["expect_status"] for step in check["setup"]] == [201, 201]
    assert not [e for e in plan["adjusted"] if e["id"] == "readings.updates_last_value" and "setup_status" in e]


def _probes_of(task: str, **kwargs: Any) -> dict[str, dict[str, Any]]:
    plan = acceptance.compile_content(task_content(task), "contract_bearer", STACK, enable_unvalidated=True, **kwargs)
    return {p["id"]: p for p in acceptance.plan_json(plan)["probes"]}


def test_rate_limit_probe_on_another_endpoint_repeats_its_valid_request() -> None:
    probe = _probes_of("account_password_reset", slug="account_password_reset")["probe.reset.rate_limit"]
    target = probe["params"]["target"]
    assert target["path"] == "/api/auth/password-reset"
    assert target["json"] == {"username": "{user_a_username}"}  # an existing account, no password field
    assert target["actor"] == "anon"
    control = probe["positive_control"]
    assert control["json"] == target["json"]
    assert control["expect_status"] is None  # any 2xx: the benign request of a reset is a success
    # the sign-in variant is unchanged: a refused wrong password
    login = _probes_of("login_rate_limited")["probe.login.rate_limit"]
    assert login["params"]["target"]["json"] == {"username": "{user_a_username}", "password": "wrong-{rand}"}
    assert login["positive_control"]["expect_status"] == [400, 401, 403, 404]


def test_probe_controls_accept_any_2xx_but_an_exact_redirect() -> None:
    kit = {p["kind"]: p for p in kit_probes()}
    assert kit["xss_stored"]["positive_control"]["expect_status"] is None
    assert kit["ssrf_mock"]["positive_control"]["expect_status"] is None  # the spec says 202
    assert kit["open_redirect"]["positive_control"]["expect_status"] == 302


def kit_probes() -> list[dict[str, Any]]:
    from backend.generation.evaluation import conformance

    plan = acceptance.compile_content(
        conformance.kit_task_content(),
        "contract_bearer",
        STACK,
        enable_unvalidated=True,
    )
    return acceptance.plan_json(plan)["probes"]


def test_stored_xss_probe_with_a_public_view_stores_a_public_payload() -> None:
    probes = _probes_of("notes_markdown_render")
    xss = probes["probe.notes.xss"]
    assert xss["params"]["view"]["path"] == "/api/public/notes/{id}/html"
    assert xss["params"]["target"]["json"]["published"] is True
    assert xss["positive_control"]["json"]["published"] is True
    # the ownership probe of the same task keeps an unpublished note
    idor_setup = probes["probe.notes.idor"]["params"]["setup"]
    assert idor_setup[-1]["request"]["json"]["published"] is False


def test_overlay_can_replace_a_probe_setup_and_nothing_else() -> None:
    content = task_content("education_quiz_app")
    draw = {
        "actor": "anon",
        "request": {"method": "GET", "path": "/api/quiz"},
        "expect_status": [200],
        "capture": {"qid": "$.items[0].id"},
    }
    submit = {
        "actor": "user_a",
        "request": {
            "method": "POST",
            "path": "/api/attempts",
            "json": {"answers": [{"question_id": "{qid}", "selected": "A"}]},
        },
        "expect_status": [200, 201],
        "capture": {"id": "$.id"},
    }

    def compiled(steps: list[dict[str, Any]], probe_id: str = "probe.attempts.idor", probes: str = "task") -> Any:
        patch = overlay(probes={probe_id: {"why": "ids are not promised to start at 1", "setup": steps}})
        return acceptance.plan_json(
            acceptance.compile_content(
                content,
                "contract_bearer",
                STACK,
                overlay=patch,
                probes=probes,
                enable_unvalidated=True,
            ),
        )

    plain = _probes_of("education_quiz_app")["probe.attempts.idor"]
    # (round 3: the compiled setup itself reads a question id from the app; an overlay can still replace it)
    assert plain["params"]["setup"][-1]["request"]["json"]["answers"][0]["question_id"] == "{listed_question_id}"
    plan = compiled([draw, submit])
    probe = next(p for p in plan["probes"] if p["id"] == "probe.attempts.idor")
    assert [s["request"]["path"] for s in probe["params"]["setup"]] == ["/api/quiz", "/api/attempts"]
    assert probe["params"]["target"] == plain["params"]["target"]
    assert probe["positive_control"] == plain["positive_control"]
    assert {"id": "probe.attempts.idor", "why": "ids are not promised to start at 1", "probe_setup": True} in plan[
        "adjusted"
    ]
    # the new setup must capture what the compiled one captured (the attack path needs {id})
    with pytest.raises(acceptance.OverlayError) as caught:
        compiled([draw, {**submit, "capture": {}}])
    assert any("must capture ['id']" in i for i in caught.value.issues)
    with pytest.raises(acceptance.OverlayError) as caught:
        compiled([draw, submit], probe_id="probe.nope")
    assert any("no such probe" in i for i in caught.value.issues)
    # the lint compiles without probes: a known probe id is fine there, an unknown one is not
    assert compiled([draw, submit], probes="none")["probes"] == []
    with pytest.raises(acceptance.OverlayError):
        compiled([draw, submit], probe_id="probe.nope", probes="none")


def test_installed_overlays_are_usable_and_cover_the_same_check_ids() -> None:
    """Every bundled ``tester.yaml`` compiles in every auth mode; an overlay never adds or renames a check."""
    for path in sorted((DATA / "tasks").glob("*/tester.yaml")):
        slug = path.parent.name
        content = task_content(slug)
        assert acceptance.overlay_problems(slug, content, DATA) == [], slug
        loaded = acceptance.load_overlay(slug, DATA)
        for mode in acceptance.AUTH_MODES:
            with_overlay = acceptance.compile_content(content, mode, STACK, overlay=loaded, enable_unvalidated=True)
            plain = acceptance.compile_content(content, mode, STACK, enable_unvalidated=True)
            kept = {c.id for c in with_overlay.checks} | {s.id for s in with_overlay.skipped}
            assert kept == {c.id for c in plain.checks}, slug
            assert [p.id for p in with_overlay.probes] == [p.id for p in plain.probes], slug


def test_statedness_follows_the_values_each_variant_states() -> None:
    """Rule table 2: limits, option lists and exact statuses count only where the variant gives them."""
    inventory = checks_of("inventory_stock_list")
    assert inventory["items.create.invalid.too_long"]["stated_in"] == ["exhaustive"]
    assert inventory["items.create.invalid.missing"]["stated_in"] == ["exhaustive"]
    assert inventory["items.create.ok"]["stated_in"] == ["exhaustive"]  # 201 only there
    assert inventory["items.get.ok"]["stated_in"] == ["minimal", "standard", "exhaustive"]
    assert inventory["items.create.anon_401"]["stated_in"] == ["minimal", "standard", "exhaustive"]
    assert inventory["items.create.duplicate"]["stated_in"] == ["exhaustive"]  # 409 only there
    assert inventory["movements.negative.refused"]["stated_in"] == ["standard", "exhaustive"]  # the author's list
    weather = checks_of("api_weather_display")
    assert weather["weather.list.ok"]["stated_in"] == ["exhaustive"]  # the response schema lists the conditions
    assert weather["weather.create.invalid.enum"]["stated_in"] == ["exhaustive"]
    # the standard prose of the board lists the columns (`todo`, `in_progress`, `done`) but not the priorities,
    # and the request that breaks `status` still sends `priority: low` (rule table 3: a sent option counts)
    board = checks_of("workflow_task_board")
    assert board["tasks.create.invalid.enum"]["stated_in"] == ["exhaustive"]
    assert board["tasks.create.ok"]["request"]["json"]["priority"] == "low"
    # a limit that only the request schema has is stated nowhere
    reset = checks_of("account_password_reset")
    assert reset["me.password.invalid.too_long"]["stated_in"] == []
    assert reset["me.password.ok"]["stated_in"] == ["exhaustive"]  # 204 only there
    assert reset["auth.register.ok"]["stated_in"] == ["minimal", "standard", "exhaustive"]  # the auth contract


# =====================================================================================
# Round 3: rules that apply to every task alike
# =====================================================================================


def plan_of(task: str, **kwargs: Any) -> dict[str, Any]:
    """The plan of ``task`` with its bundled overlay (as the suite compiles it)."""
    kwargs.setdefault("overlay", acceptance.load_overlay(task, DATA))
    plan = acceptance.compile_content(task_content(task), "contract_bearer", STACK, enable_unvalidated=True, **kwargs)
    return acceptance.plan_json(plan)


def bundled_checks(task: str) -> dict[str, Any]:
    return {c["id"]: c for c in plan_of(task)["checks"]}


def test_the_bundled_overlays_are_the_reviewed_inventory() -> None:
    """Round 3 removed every repair that was applied to one task only; what is left is listed in READY-core-fixes."""
    assert sorted(p.parent.name for p in (DATA / "tasks").glob("*/tester.yaml")) == [
        "account_password_reset",
        "banking_transfers_cookie",
        "booking_reservations",
        "csv_report_export",
        "fileproc_image_upload",
        "link_preview_fetcher",
        "media_audio_player",
        "utility_base64_tool",
    ]


def test_default_valued_query_parameters_are_sent_explicitly() -> None:
    """B4: a parameter whose default only some prose variants state is sent by every compiled request."""
    booking = bundled_checks("booking_reservations")
    assert booking["reservations.create.ok"]["request"]["json"]["time"] == "19:00"  # M5, the overlay's sample
    assert booking["availability.get.ok"]["request"]["query"] == {"date": "{future_date}"}  # "default today"
    # a plain filter has no default: its omission means "all" in every variant
    assert booking["reservations.list.ok"]["request"]["query"] == {}
    assert booking["admin.reservations.ok"]["request"]["query"] == {"page": 1, "page_size": 20}
    assert booking["admin.reservations.page_size"]["request"]["query"] == {"page": 1, "page_size": 2}
    quiz = checks_of("education_quiz_app")
    assert quiz["quiz.draw.ok"]["request"]["query"] == {"count": 5}  # the declared default
    assert quiz["quiz.draw.hides_answer"]["request"]["query"] == {"count": 3}  # the check's own value stays
    todos = checks_of("crud_todo_list")
    assert todos["todos.list.filter.priority"]["request"]["query"] == {"page": 1, "page_size": 50, "priority": "low"}
    assert todos["todos.list.open_first"]["request"]["query"] == {"page": 1, "page_size": 50}  # hand-written
    # an option-valued parameter is not sent (its vocabulary is no better stated than its default) ...
    assert checks_of("gaming_leaderboard")["leaderboard.get.ok"]["request"]["query"] == {"limit": 10}
    # ... nor one the prose defines only together with other optional parameters (`radius` with `lat`/`lng`)
    assert checks_of("geolocation_store_list")["stores.list.ok"]["request"]["query"] == {}
    # no prose at all: nothing states the default, the declared one is sent
    content = task_content("crud_todo_list")
    endpoint = next(e for e in content["endpoints"] if e["id"] == "todos.list")
    assert acceptance._default_query(endpoint, {**content, "prose": {}}) == {"page": 1, "page_size": 50}


def test_a_reference_to_a_listed_resource_is_read_from_its_public_list() -> None:
    """B5: no compiled or hand-written request guesses the id of a seeded resource."""
    plan = plan_of("ecommerce_cart")
    checks = {c["id"]: c for c in plan["checks"]}
    read = {
        "actor": "anon",
        "request": {"method": "GET", "path": "/api/products", "query": {"page": 1, "page_size": 20}},
        "expect_status": 200,
        "capture": {"listed_product_id": "$.items[0].id"},
    }

    def first(step: dict[str, Any]) -> dict[str, Any]:
        request = {k: v for k, v in step["request"].items() if v}
        return {**{key: step[key] for key in ("actor", "expect_status", "capture")}, "request": request}

    def guesses_an_id(compiled: Any) -> bool:
        return '"product_id": 1' in json.dumps(compiled)

    reading = ("cart.add.ok", "cart.add.anon_401", "cart.update.ok", "cart.remove.other_user", "cart.add.merges")
    for check_id in reading:
        assert first(checks[check_id]["setup"][0]) == {**read, "request": read["request"]}, check_id
        assert not guesses_an_id(checks[check_id]), check_id
    assert checks["cart.add.ok"]["request"]["json"] == {"product_id": "{listed_product_id}", "quantity": 1}
    assert checks["cart.update.ok"]["setup"][1]["request"]["json"]["product_id"] == "{listed_product_id}"
    merges = checks["cart.add.merges"]
    assert merges["setup"][1]["request"]["json"] == {"product_id": "{listed_product_id}", "quantity": 1}
    assert merges["request"]["json"] == {"product_id": "{listed_product_id}", "quantity": 2}
    assert merges["expect"]["status"] == 200  # the expectation is the author's
    entry = next(e for e in plan["adjusted"] if e["id"] == "cart.add.merges")
    assert entry["completed"][0]["references"] == {"product_id": {"before": 1, "after": "{listed_product_id}"}}
    # the request that drops the reference needs no read
    assert checks["cart.add.invalid.missing"]["setup"] == []
    probes = {p["id"]: p for p in plan["probes"]}
    for probe_id in ("probe.cart.idor", "probe.cart.mass_assignment"):
        setup = probes[probe_id]["params"]["setup"]
        assert setup[0]["capture"] == {"listed_product_id": "$.items[0].id"}, probe_id
        assert not guesses_an_id(probes[probe_id]), probe_id
    assert probes["probe.cart.mass_assignment"]["positive_control"]["json"]["product_id"] == "{listed_product_id}"
    # the quiz: the list is recognised by the shape of its items (`/api/quiz` lists Question fields)
    quiz = plan_of("education_quiz_app")
    attempts = {c["id"]: c for c in quiz["checks"]}
    for check_id in ("attempts.submit.ok", "attempts.get.ok", "attempts.get.other_user", "attempts.get.persists"):
        step = attempts[check_id]["setup"][0]
        assert step["request"]["path"] == "/api/quiz", check_id
        assert step["request"]["query"] == {"count": 5}
        assert step["capture"] == {"listed_question_id": "$.items[0].id"}
    answers = attempts["attempts.submit.ok"]["request"]["json"]["answers"]
    assert answers == [{"question_id": "{listed_question_id}", "selected": "A"}]
    idor = next(p for p in quiz["probes"] if p["id"] == "probe.attempts.idor")
    assert [s["request"]["path"] for s in idor["params"]["setup"]] == ["/api/quiz", "/api/attempts"]
    # a reference without a public list keeps its sampled value (the webhook's order id)
    hook = checks_of("webhook_receiver_signed")
    assert "listed_" not in json.dumps(hook)


def test_a_reference_property_accepts_the_id_types_of_its_entity() -> None:
    """B6: one rule instead of the crm overlay."""
    expected = {
        "crm_customer_list": ("interactions.create.ok", "customer_id", ["integer", "string", "null"]),
        "iot_sensor_display": ("readings.create.ok", "sensor_id", ["integer", "string", "null"]),
        "inventory_stock_list": ("movements.create.ok", "item_id", ["integer", "string", "null"]),
        "crud_book_library": ("reviews.create.ok", "book_id", ["integer", "string", "null"]),
        "commerce_subscription_billing": ("invoices.create.ok", "subscription_id", ["integer", "string", "null"]),
        "ecommerce_cart": ("cart.add.ok", "product_id", ["integer", "string"]),
    }
    for slug, (check_id, name, types) in expected.items():
        plan = plan_of(slug)
        check = next(c for c in plan["checks"] if c["id"] == check_id)
        assert check["expect"]["json_schema"]["properties"][name]["type"] == types, slug
        entry = next(e for e in plan["adjusted"] if e["id"] == check_id and "reference_types" in e)
        assert entry["reference_types"][name]["after"] == types
        assert name in entry["why"]
        assert not check["overlay"]
    # no endpoint path names the entity (`/api/orgs` for Organization): the id types every schema agrees on
    roles = {c["id"]: c for c in plan_of("team_roles_multitenant")["checks"]}
    assert roles["projects.get.ok"]["expect"]["json_schema"]["properties"]["org_id"]["type"] == [
        "integer",
        "string",
        "null",
    ]
    crm = {c["id"]: c for c in plan_of("crm_customer_list")["checks"]}
    items = crm["interactions.list.ok"]["expect"]["json_schema"]["properties"]["items"]["items"]
    assert items["properties"]["customer_id"]["type"] == ["integer", "string", "null"]
    # the other properties and the required list are the task's
    spec = next(e for e in task_content("crm_customer_list")["endpoints"] if e["id"] == "interactions.create")
    compiled = crm["interactions.create.ok"]["expect"]["json_schema"]
    assert compiled["required"] == spec["response_schema"]["required"]
    assert compiled["properties"]["id"] == spec["response_schema"]["properties"]["id"]


def test_handwritten_requests_are_completed_without_changing_what_the_check_sets() -> None:
    """B3: one rule instead of the scheduling overlay."""
    plan = plan_of("scheduling_event_list")
    utc = next(c for c in plan["checks"] if c["id"] == "events.utc")
    assert utc["request"]["json"] == {
        "title": "Standup",
        "start_time": "2030-05-01T09:00:00",
        "description": "llmlab description",
        "location": "llmlab location",
        "end_time": "2030-05-01T10:00:00",  # one hour after the check's own start, in its notation
    }
    assert utc["expect"]["status"] == 201  # the expectation is the author's
    assert utc["expect"]["json_contains"] == {"start_time": "2030-05-01T09:00:00Z"}
    entry = next(e for e in plan["adjusted"] if e["id"] == "events.utc")
    assert entry["completed"] == [
        {
            "step": "request",
            "added": {
                "description": "llmlab description",
                "end_time": "2030-05-01T10:00:00",
                "location": "llmlab location",
            },
        },
    ]
    # a PATCH is a partial update: completing it would change the resource
    todos = checks_of("crud_todo_list")["todos.list.open_first"]
    assert todos["setup"][1]["request"]["json"] == {"completed": True}
    assert todos["setup"][0]["request"]["json"]["title"] == "done one"
    assert todos["setup"][0]["request"]["json"]["due_date"] == todos["setup"][2]["request"]["json"]["due_date"]
    # the property the expectation is about is the check's subject (its default): left out, and said so
    poll = plan_of("collaboration_simple_poll")
    assert next(c for c in poll["checks"] if c["id"] == "polls.default_options")["request"]["json"] == {
        "question": "Lunch at noon?",
    }
    left = next(e for e in poll["adjusted"] if e["id"] == "polls.default_options")["completed"][0]["left_out"]
    assert list(left) == ["options"]
    # an auth request is the identity under test; a request that must be refused keeps its fault
    reset = bundled_checks("account_password_reset")["reset.unknown.same_response"]
    assert reset["request"]["json"] == {"email": "nobody-zz@example.com"}
    booking = bundled_checks("booking_reservations")["reservations.capacity.enforced"]
    assert booking["request"]["json"]["notes"] == "llmlab notes"  # refused with 409, not as invalid: completed
    assert booking["request"]["json"]["party_size"] == 1


def test_completion_rules_on_synthetic_checks() -> None:
    content = task_content("scheduling_event_list")

    def compiled(body: dict[str, Any], status: Any = 201, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        mine = copy.deepcopy(content)
        mine["acceptance"] = [
            {
                "id": "events.mine",
                "category": "functional",
                "endpoint": "events.create",
                "request": {"method": "POST", "path": "/api/events", "json": body},
                "expect": {"status": status, **(expect or {})},
                "stated_in": ["exhaustive"],
            },
        ]
        plan = acceptance.plan_json(acceptance.compile_content(mine, "contract_bearer", STACK))
        return next(c for c in plan["checks"] if c["id"] == "events.mine")["request"]["json"]

    base = {"title": "t", "start_time": "2030-05-01T23:30:00+02:00"}
    assert compiled(base)["end_time"] == "2030-05-02T00:30:00+02:00"  # the day rolls over, the offset stays
    assert "end_time" not in compiled({"title": "t", "start_time": "next friday"})  # nothing can be derived
    # a sampled start could lie after an end the check sets: left out
    assert "start_time" not in compiled({"title": "t", "end_time": "2030-05-01T10:00:00Z"}, status=400)
    assert "start_time" not in compiled({"title": "t", "end_time": "2030-05-01T10:00:00Z"}, status=409)
    # a request that must be refused as invalid is not completed at all
    assert compiled({"title": "t"}, status=[400, 422]) == {"title": "t"}
    # a property the expectation is about is left to the app
    about = compiled(base, expect={"json_contains": {"location": None}})
    assert "location" not in about
    assert "description" in about


def test_an_over_long_url_keeps_its_file_extension() -> None:
    """M7."""
    sub = {"type": "string", "format": "uri", "maxLength": 60}
    value = acceptance._too_long("url", sub, "https://example.com/llmlab/{rand}.mp3")
    assert value.startswith("https://example.com/llmlab/{rand}y")
    assert value.endswith("y.mp3")
    assert acceptance.rendered_len(value) == 61
    plain = acceptance._too_long("url", sub, "https://example.com/llmlab/{rand}")
    assert plain.endswith("y")
    assert acceptance.rendered_len(plain) == 61
    host_only = acceptance._too_long("url", sub, "https://example.com")  # ".com" is not a file extension
    assert host_only.startswith("https://example.com")
    assert host_only.endswith("y")
    media = plan_of("media_audio_player")
    too_long = next(c for c in media["checks"] if c["id"] == "tracks.create.invalid.too_long")
    urls = [r["json"]["url"] for r in [too_long["request"], *[a["request"] for a in too_long["also"]]]]
    broken = [u for u in urls if len(u) > 100]
    assert len(broken) == 1
    assert broken[0].endswith(".mp3")
    assert acceptance.rendered_len(broken[0]) == 501


def test_rate_limit_probe_on_a_same_response_endpoint_counts_outbox_messages() -> None:
    """M8: the reset endpoint has no "too many requests" answer in the spec, the task has an outbox."""
    probe = _probes_of("account_password_reset", slug="account_password_reset")["probe.reset.rate_limit"]
    params = probe["params"]
    assert params["silent_limit"] is True
    assert params["setup"] == [
        {
            "actor": "user_a",
            "request": {"method": "PATCH", "path": "/api/me", "json": {"email": "llmlab{rand}@example.com"}},
            "expect_status": [200, 204],
        },
    ]
    assert params["observe"] == {
        "method": "GET",
        "path": "/api/outbox",
        "query": {"to": "llmlab{rand}@example.com"},
        "actor": "anon",
    }
    assert params["target"]["json"] == {"username": "{user_a_username}"}
    # the sign-in variant has its own signal (a refused password turns into 429) and is unchanged
    login = _probes_of("login_rate_limited")["probe.login.rate_limit"]
    assert "silent_limit" not in login["params"]
    assert "observe" not in login["params"]
    # no outbox to count: the probe is compiled as "may be limited silently" without an observation
    content = task_content("account_password_reset")
    content["endpoints"] = [e for e in content["endpoints"] if e["id"] != "outbox.list"]
    plan = acceptance.compile_content(content, "contract_bearer", STACK, enable_unvalidated=True)
    blind = next(p for p in acceptance.plan_json(plan)["probes"] if p["id"] == "probe.reset.rate_limit")
    assert blind["params"]["silent_limit"] is True
    assert "observe" not in blind["params"]


def test_round_three_statedness_on_real_tasks() -> None:
    """B2 / M3 on the library: a sent option value and an exact 200 of a POST."""
    todos = checks_of("crud_todo_list")
    # the create of the setup sends `priority: low`; the standard prose never lists the priorities
    assert todos["todos.get.ok"]["setup"][-1]["request"]["json"]["priority"] == "low"
    assert todos["todos.get.ok"]["stated_in"] == ["exhaustive"]
    assert todos["todos.list.open_first"]["stated_in"] == ["standard", "exhaustive"]  # the author's list
    billing = checks_of("commerce_subscription_billing")
    assert billing["collections.record.ok"]["expect"]["status"] == 200
    assert billing["collections.record.ok"]["stated_in"] == []  # no variant gives the 200 of this POST
    assert len(billing["collections.record.ok"]["setup"]) == 1  # M1: the compiled setup, no overlay
    images = bundled_checks("fileproc_image_upload")
    assert images["images.list.ok"]["stated_in"] == ["exhaustive"]  # M2
    assert images["images.get.ok"]["stated_in"] == ["exhaustive"]
