"""Run presets: validation, versioning, seeding, harness options, identity keys, agent create/preview, cloning."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import RunPreset
from backend.generation.services import presets as preset_service
from backend.generation.services.agent.harnesses import HARNESSES
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.runner import assess_validity
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.job_cloning import clone_job
from backend.generation.services.presets import PresetError
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

SUPPORTED = ["temperature", "top_p", "seed", "tools", "tool_choice", "max_tokens", "reasoning"]
API = "/api/generation"


@pytest.fixture(autouse=True)
def _no_auto_evaluation(settings):
    settings.AGENT_AUTO_EVALUATE = False


def _model(**kw):
    kw.setdefault("model_id", "vendor/coder")
    kw.setdefault("metadata", {"openrouter_supported_parameters": SUPPORTED})
    kw.setdefault("max_output_tokens", 64_000)
    return LLMModelFactory(**kw)


def _app(slug="notes"):
    existing = AppRequirementTemplate.objects.filter(slug=slug).first()
    if existing is not None:
        return existing
    return AppRequirementTemplateFactory(
        slug=slug,
        is_default=True,
        name="Notes",
        description="",
        backend_requirements=["Notes have a title"],
        api_endpoints=[
            {"method": "GET", "path": "/api/notes", "description": "List", "request": None, "response": {}},
        ],
    )


def _preset(user, kind, slug, config, **kw):
    return preset_service.create_preset(user, kind=kind, slug=slug, name=kw.pop("name", slug), config=config, **kw)


def _snapshot(model=None, app=None, **kw):
    return build_agent_snapshot(
        app_requirement=app if app is not None else _app(),
        description="",
        profile=kw.pop("profile", None),
        stack_slug="flask-react",
        model=model or _model(),
        max_tokens=kw.pop("max_tokens", 32000),
        experiment_seed=kw.pop("experiment_seed", 7),
        llm_overrides=kw.pop("llm_overrides", None),
        agent_overrides=kw.pop("agent_overrides", None),
        harness_options=kw.pop("harness_options", None),
    )


@pytest.fixture
def user_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def _post(client, path, payload, *, has_key=True):
    with (
        mock.patch("backend.generation.api.views.custom.has_resolvable_key", return_value=has_key),
        mock.patch("backend.generation.api.views.custom.dispatch_job") as dispatch,
    ):
        res = client.post(f"{API}{path}", data=json.dumps(payload), content_type="application/json")
    return res, dispatch


def _codes(preview: dict) -> dict[str, str]:
    return {w["code"]: w["severity"] for w in preview["warnings"]}


# ── validation / normalization ─────────────────────────────────────────


class TestValidation:
    def test_harness_options_drop_defaults_and_validate(self):
        assert preset_service.normalize_config(
            "harness",
            {"harness": "aider", "options": {"edit_format": "diff"}},
        ) == {
            "harness": "aider",
            "options": {},
        }
        assert preset_service.normalize_config(
            "harness",
            {"harness": "aider", "options": {"edit_format": "whole", "map_tokens": 512.0}},
        ) == {"harness": "aider", "options": {"edit_format": "whole", "map_tokens": 512}}
        for bad, needle in (
            ({"harness": "cursor"}, "harness must be one of"),
            ({"harness": "aider", "options": {"edit_format": "architect"}}, "edit_format must be one of"),
            (
                {"harness": "aider", "options": {"map_tokens": 9000}},
                "map_tokens must be an integer between 0 and 8192",
            ),
            ({"harness": "aider", "options": {"map_tokens": True}}, "map_tokens"),
            ({"harness": "aider", "options": {"bogus": 1}}, "Unknown aider options: bogus"),
            ({"harness": "opencode", "options": {"disabled_tools": ["edit"]}}, "allowed: none"),
            ({"harness": "aider", "extra": 1}, "Unknown harness preset keys: extra"),
        ):
            with pytest.raises(ValueError, match=needle):
                preset_service.normalize_config("harness", bad)

    def test_sampling_rejects_routing_and_output_formatting(self):
        normalized = preset_service.normalize_config(
            "sampling",
            {"llm_params": {"temperature": 0, "reasoning": {"effort": "high"}}, "sampling_policy": "provider_default"},
        )
        assert normalized == {
            "llm_params": {"temperature": 0, "reasoning": {"effort": "high"}},
            "sampling_policy": "provider_default",
        }
        assert preset_service.normalize_config("sampling", {}) == {"llm_params": {}}
        _, errors = preset_service.check_config(
            "sampling",
            {"llm_params": {"provider": {"only": ["x"]}, "stop": ["END"], "response_format": {"type": "json_object"}}},
        )
        assert len(errors) == 3
        assert any(e.startswith("provider:") for e in errors)
        for bad in ({"llm_params": {"temperature": 3}}, {"sampling_policy": "hot"}, {"llm_params": []}, {"x": 1}):
            assert preset_service.check_config("sampling", bad)[1]

    def test_budget_is_limits_and_network_only(self):
        assert preset_service.normalize_config("budget", {"max_llm_requests": 30.0, "max_cost_usd": 3}) == {
            "max_llm_requests": 30,
            "max_cost_usd": 3,
        }
        for bad, needle in (
            ({"harness": "aider"}, "Unknown budget preset keys: harness"),
            ({"sampling_policy": "harness_native"}, "sampling_policy"),
            ({"max_llm_requests": 0}, "max_llm_requests"),
            ({"network_policy": "internet"}, "network_policy"),
        ):
            with pytest.raises(ValueError, match=needle):
                preset_service.normalize_config("budget", bad)

    def test_content_hash_is_about_behaviour(self):
        h = preset_service.config_hash
        n = preset_service.normalize_config
        assert h("harness", n("harness", {"harness": "aider"})) == h(
            "harness",
            n("harness", {"harness": "aider", "options": {"edit_format": "diff", "map_tokens": 1024}}),
        )
        assert h("sampling", n("sampling", {"llm_params": {"temperature": 0}})) == h(
            "sampling",
            n("sampling", {"llm_params": {"temperature": 0.0}}),
        )
        assert h("budget", {}) != h("sampling", {})  # kind is part of the content

    def test_check_config_bad_kind_and_shape(self):
        assert preset_service.check_config("model", {})[1] == ["kind must be one of harness, sampling, budget"]
        assert preset_service.check_config("budget", [])[1] == ["config must be an object"]


# ── create / versions / slugs / permissions ────────────────────────────


class TestPresetWrites:
    def test_versioning_content_vs_metadata(self):
        user = UserFactory()
        v1 = _preset(user, "budget", "smoke", {"max_llm_requests": 30}, name="Smoke")
        assert (v1.version, v1.is_system, v1.created_by) == (1, False, user)
        same = preset_service.update_preset(
            user,
            kind="budget",
            slug="smoke",
            name="Smoke test",
            description="d",
            config={"max_llm_requests": 30.0},
        )
        assert (same.id, same.version, same.name) == (v1.id, 1, "Smoke test")
        v2 = preset_service.update_preset(
            user,
            kind="budget",
            slug="smoke",
            name="Smoke",
            config={"max_llm_requests": 20},
        )
        assert v2.version == 2
        assert v2.content_hash != v1.content_hash
        metadata_only = preset_service.update_preset(user, kind="budget", slug="smoke", name="Renamed")
        assert (metadata_only.id, metadata_only.config) == (v2.id, {"max_llm_requests": 20})
        v1.refresh_from_db()
        assert v1.config == {"max_llm_requests": 30}  # immutable content

    def test_slugs_are_global_per_kind_and_reserved(self):
        alice, bob = UserFactory(), UserFactory()
        _preset(alice, "sampling", "temperature-zero", {"llm_params": {"temperature": 0}})
        with pytest.raises(PresetError) as exc:
            _preset(bob, "sampling", "temperature-zero", {"llm_params": {"temperature": 0.2}})
        assert exc.value.status == 400
        assert exc.value.detail == "slug already in use"  # no hint whose it is
        # Another kind may reuse the slug.
        _preset(bob, "budget", "temperature-zero", {"max_llm_requests": 5})
        for slug in ("aider-default", "my-default", "provider-default"):
            with pytest.raises(PresetError, match="reserved"):
                _preset(bob, "sampling", slug, {"llm_params": {"top_p": 0.5}})
        for slug in ("A", "-x", "has space", "x" * 82):
            with pytest.raises(PresetError, match="slug must be"):
                _preset(bob, "sampling", slug, {"llm_params": {"top_p": 0.5}})

    def test_all_default_harness_preset_is_refused(self):
        with pytest.raises(PresetError, match="use the system preset aider-default"):
            _preset(UserFactory(), "harness", "aider-plain", {"harness": "aider", "options": {"map_tokens": 1024}})
        with pytest.raises(PresetError, match="opencode-default"):
            _preset(UserFactory(), "harness", "oc", {"harness": "opencode"})

    def test_only_owners_change_and_system_presets_are_locked(self):
        preset_service.ensure_system_presets()
        owner, other = UserFactory(), UserFactory()
        _preset(owner, "budget", "mine", {"max_llm_requests": 9})
        for user, slug in ((other, "mine"), (owner, "platform-default")):
            with pytest.raises(PresetError) as exc:
                preset_service.update_preset(user, kind="budget", slug=slug, name="x", config={})
            assert exc.value.status == 403
            with pytest.raises(PresetError) as exc:
                preset_service.archive_preset(user, kind="budget", slug=slug)
            assert exc.value.status == 403
        with pytest.raises(PresetError) as exc:
            preset_service.update_preset(owner, kind="budget", slug="nope", name="x", config={})
        assert exc.value.status == 404


class TestPresetApi:
    def test_crud_flow(self, user_client):
        client, _user = user_client
        body = {
            "kind": "harness",
            "slug": "aider-whole",
            "name": "Aider whole",
            "config": {"harness": "aider", "options": {"edit_format": "whole"}},
        }
        res = client.post(f"{API}/presets/", data=json.dumps(body), content_type="application/json")
        assert res.status_code == 201, res.content
        created = res.json()
        assert created["version"] == 1
        assert created["latest_version"] == 1
        assert created["is_default"] is False
        assert created["config"] == {"harness": "aider", "options": {"edit_format": "whole"}}
        res = client.put(
            f"{API}/presets/harness/aider-whole/",
            data=json.dumps(
                {
                    "name": "Aider whole",
                    "config": {"harness": "aider", "options": {"edit_format": "whole", "map_tokens": 0}},
                },
            ),
            content_type="application/json",
        )
        assert res.status_code == 200
        assert res.json()["version"] == 2
        versions = client.get(f"{API}/presets/harness/aider-whole/versions/").json()
        assert [v["version"] for v in versions] == [2, 1]
        assert {v["latest_version"] for v in versions} == {2}
        old = client.get(f"{API}/presets/{created['id']}/").json()
        assert (old["version"], old["latest_version"]) == (1, 2)

        listed = client.get(f"{API}/presets/?kind=harness").json()
        slugs = [p["slug"] for p in listed]
        assert slugs[0] == "opencode-default"  # the overall default first
        assert set(slugs[:3]) == {f"{h}-default" for h in HARNESSES}
        assert slugs[-1] == "aider-whole"
        assert next(p for p in listed if p["slug"] == "aider-whole")["version"] == 2

        assert client.delete(f"{API}/presets/harness/aider-whole/").json() == {"success": True}
        assert "aider-whole" not in [p["slug"] for p in client.get(f"{API}/presets/?kind=harness").json()]
        archived = client.get(f"{API}/presets/?kind=harness&include_archived=true").json()
        assert next(p for p in archived if p["slug"] == "aider-whole")["is_archived"] is True
        # Archived versions stay readable (jobs reference them).
        assert client.get(f"{API}/presets/{created['id']}/").status_code == 200

    def test_error_statuses(self, user_client):
        client, _user = user_client
        other = _preset(UserFactory(), "budget", "theirs", {"max_llm_requests": 9})
        assert client.get(f"{API}/presets/{other.id}/").status_code == 404  # invisible
        res = client.put(
            f"{API}/presets/budget/theirs/",
            data=json.dumps({"name": "x", "config": {}}),
            content_type="application/json",
        )
        assert res.status_code == 403
        assert client.delete(f"{API}/presets/budget/platform-default/").status_code == 403
        res = client.post(
            f"{API}/presets/",
            data=json.dumps({"kind": "budget", "slug": "theirs", "name": "x", "config": {}}),
            content_type="application/json",
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "slug already in use"
        assert client.get(f"{API}/presets/?kind=model").status_code == 400
        assert client.get(f"{API}/presets/budget/nope/versions/").status_code == 404

    def test_validate_is_routed_before_the_id_route(self, user_client):
        client, _user = user_client
        res = client.post(
            f"{API}/presets/validate/",
            data=json.dumps(
                {
                    "kind": "harness",
                    "config": {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 120}},
                },
            ),
            content_type="application/json",
        )
        assert res.status_code == 200
        assert res.json() == {
            "ok": True,
            "errors": [],
            "config": {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 120}},
        }
        res = client.post(
            f"{API}/presets/validate/",
            data=json.dumps({"kind": "harness", "config": {"harness": "aider"}}),
            content_type="application/json",
        )
        assert res.json()["ok"] is False
        assert "aider-default" in res.json()["errors"][0]
        assert res.json()["config"] is None
        res = client.post(
            f"{API}/presets/validate/",
            data=json.dumps({"kind": "sampling", "config": {"llm_params": {"temperature": 9}}}),
            content_type="application/json",
        )
        assert res.json()["ok"] is False
        system = preset_service.system_default_preset("budget")
        assert client.get(f"{API}/presets/{system.id}/").json()["is_default"] is True
        # The id route only takes integers: no shadowing of static segments.
        assert client.get(f"{API}/presets/validate/").status_code == 405

    def test_route_registration_order(self):
        from backend.generation.api.views import router

        paths = [path for path, _view in router.path_operations.items()]
        assert paths.index("/presets/validate/") < paths.index("/presets/{int:preset_id}/")


# ── system presets / seeding ───────────────────────────────────────────


class TestSystemPresets:
    def test_files_define_exactly_the_builtin_defaults(self):
        files = {
            (e.kind, e.slug): preset_service.normalize_config(e.kind, e.config)
            for e in preset_service.load_system_preset_files()
        }
        builtin = {(e.kind, e.slug): e.config for e in preset_service._builtin_default_presets()}
        # A default with content would change every default run's identity.
        assert {key: files.get(key) for key in builtin} == builtin

    def test_seeding_is_idempotent_and_versions_content_changes(self):
        preset_service.seed_presets()
        count = RunPreset.objects.count()
        assert preset_service.seed_presets() == (0, 0)
        assert RunPreset.objects.count() == count
        row = RunPreset.objects.get(kind="budget", slug="platform-default", version=1)
        row.content_hash = "stale-hash-simulating-an-edited-file"
        row.description = "edited by hand"
        row.save()
        assert preset_service.seed_presets() == (1, 0)
        versions = list(RunPreset.objects.filter(kind="budget", slug="platform-default").order_by("version"))
        assert [v.version for v in versions] == [1, 2]
        assert versions[0].content_hash == "stale-hash-simulating-an-edited-file"  # never rewritten
        assert preset_service.system_default_preset("budget").version == 2
        versions[1].name = "Old name"
        versions[1].save()
        assert preset_service.seed_presets() == (0, 1)  # metadata only: no new version

    def test_user_row_blocking_a_system_slug_is_skipped(self, caplog):
        RunPreset.objects.filter(kind="sampling", slug="provider-default").delete()
        RunPreset.objects.create(
            kind="sampling",
            slug="provider-default",
            name="mine",
            config={"llm_params": {}},
            created_by=UserFactory(),
        )
        preset_service.seed_presets()
        assert not RunPreset.objects.filter(kind="sampling", slug="provider-default", is_system=True).exists()
        assert "a user preset already uses the slug" in caplog.text
        # The default path still works, without provenance.
        assert preset_service.system_default_preset("sampling") is None
        assert preset_service.builtin_default_config("sampling") == {"llm_params": {}}

    def test_ensure_recreates_missing_defaults_even_without_files(self, tmp_path, monkeypatch):
        RunPreset.objects.filter(is_system=True).delete()
        monkeypatch.setattr(preset_service, "PRESETS_DIR", tmp_path)
        preset_service.ensure_system_presets()
        system = set(RunPreset.objects.filter(is_system=True).values_list("kind", "slug"))
        assert system == {("harness", f"{h}-default") for h in HARNESSES} | {
            ("sampling", "provider-default"),
            ("budget", "platform-default"),
        }

    def test_seed_all_runs_presets_first_and_isolated(self):
        from backend.generation import seeding

        calls = []
        with (
            mock.patch.object(seeding, "seed_presets", side_effect=RuntimeError("broken presets")),
            mock.patch.object(seeding, "seed_requirements", side_effect=lambda **kw: calls.append("req") or (0, 0)),
            mock.patch.object(seeding, "seed_content_blocks", return_value=(0, 0)),
            mock.patch.object(seeding, "seed_profiles", return_value=(0, 0)),
            mock.patch.object(seeding, "seed_app_profiles", return_value=(0, 0)),
        ):
            results = seeding.seed_all()
        assert next(iter(results)) == "presets"
        assert results["presets"] == (0, 0)
        assert calls == ["req"]  # later seeders still ran

    def test_is_default_flags(self):
        preset_service.ensure_system_presets()
        defaults = {
            (p.kind, p.slug) for p in RunPreset.objects.filter(is_system=True) if preset_service.is_default_preset(p)
        }
        assert ("harness", "aider-default") in defaults
        assert ("sampling", "provider-default") in defaults
        user_preset = _preset(UserFactory(), "budget", "x-budget", {"max_llm_requests": 5})
        assert preset_service.is_default_preset(user_preset) is False


# ── harness option schemas ─────────────────────────────────────────────


def _section(name, options=None, **kw):
    from backend.generation.services.agent.config import merge_agent_config

    return get_harness(name).snapshot_section(
        model_slug="vendor/coder",
        context_window=kw.get("context_window", 100_000),
        max_output_tokens=kw.get("max_output_tokens", 32_000),
        agent_config=merge_agent_config({"harness": name}),
        title="t",
        options=options,
    )


class TestHarnessOptions:
    @pytest.mark.parametrize("name", list(HARNESSES))
    def test_defaults_render_the_byte_identical_section(self, name):
        harness = get_harness(name)
        baseline = json.dumps(_section(name), sort_keys=True)
        assert json.dumps(_section(name, {}), sort_keys=True) == baseline
        assert json.dumps(_section(name, harness.default_options()), sort_keys=True) == baseline
        assert harness.description
        for field in harness.option_schema():
            data = field.to_dict()
            assert {"key", "type", "label", "help", "default"} <= set(data)
            assert data["default"] == harness.default_options()[field.key]

    def test_schemas(self):
        assert [f.key for f in get_harness("aider").option_schema()] == ["edit_format", "map_tokens"]
        edit = get_harness("aider").option_schema()[0].to_dict()
        assert [c["value"] for c in edit["choices"]] == ["diff", "whole", "udiff", "diff-fenced"]
        timeout = get_harness("mini-swe-agent").option_schema()[0].to_dict()
        assert (timeout["key"], timeout["min"], timeout["max"], timeout["default"]) == (
            "command_timeout_seconds",
            10,
            600,
            30,
        )
        assert get_harness("opencode").option_schema() == []

    def test_aider_options_render_into_args(self):
        section = _section("aider", {"edit_format": "whole", "map_tokens": 0})
        args = section["args"]
        assert args[args.index("--edit-format") + 1] == "whole"
        assert args[args.index("--map-tokens") + 1] == "0"
        assert section["edit_format"] == "whole"
        assert "'whole'" in section["system_prompt_source"]

    def test_mini_timeout_renders_into_environment_config(self):
        section = _section("mini-swe-agent", {"command_timeout_seconds": 120})
        assert section["config"]["environment"] == {"timeout": 120}
        files = get_harness("mini-swe-agent").files(section, "brief")
        assert json.loads(files["mini-llmlab.yaml"])["environment"] == {"timeout": 120}
        assert "environment" not in _section("mini-swe-agent")["config"]

    def test_opencode_takes_no_options(self):
        with pytest.raises(ValueError, match="Unknown opencode options"):
            _section("opencode", {"anything": 1})


# ── snapshot identity keys ─────────────────────────────────────────────


class TestIdentityKeys:
    def test_method_key_suffixes_are_content_addressed(self):
        app, model = _app(), _model()
        base = _snapshot(model, app, agent_overrides={"harness": "aider"})
        assert base["method_key"] == "agent:aider@0.86.2"
        whole = _snapshot(model, app, agent_overrides={"harness": "aider"}, harness_options={"edit_format": "whole"})
        assert whole["method_key"].startswith("agent:aider@0.86.2+opts:")
        again = _snapshot(
            model,
            app,
            agent_overrides={"harness": "aider"},
            harness_options={"edit_format": "whole", "map_tokens": 1024},
        )
        assert again["method_key"] == whole["method_key"]
        temp = _snapshot(model, app, llm_overrides={"temperature": 0})
        assert "+llm:" in temp["method_key"]
        assert _snapshot(model, app, llm_overrides={"temperature": 0.0})["method_key"] == temp["method_key"]
        assert "+llm:" not in _snapshot(model, app, llm_overrides={"max_tokens": 8000})["method_key"]
        assert "+llm:" in _snapshot(model, app, agent_overrides={"sampling_policy": "harness_native"})["method_key"]
        budget = _snapshot(model, app, agent_overrides={"max_llm_requests": 30})
        assert "+budget:" in budget["method_key"]
        assert "+budget:" not in _snapshot(model, app, agent_overrides={"max_llm_requests": 100})["method_key"]
        assert "+budget:" in _snapshot(model, app, agent_overrides={"network_policy": "none"})["method_key"]

    def test_config_fingerprint_is_seed_free(self):
        app, model = _app(), _model()
        a = _snapshot(model, app, experiment_seed=1)
        b = _snapshot(model, app, experiment_seed=2)
        assert a["run_fingerprint"] != b["run_fingerprint"]
        assert a["config_fingerprint"] == b["config_fingerprint"]
        assert a["config_fingerprint"] != a["run_fingerprint"]
        no_seed = _model(model_id="vendor/noseed", metadata={"openrouter_supported_parameters": ["tools"]})
        c = _snapshot(no_seed, app, experiment_seed=1)
        assert c["config_fingerprint"] == _snapshot(no_seed, app, experiment_seed=2)["config_fingerprint"]
        opts = _snapshot(model, app, agent_overrides={"harness": "aider"}, harness_options={"map_tokens": 0})
        assert (
            opts["config_fingerprint"]
            != _snapshot(model, app, agent_overrides={"harness": "aider"})["config_fingerprint"]
        )

    def test_options_stay_out_of_the_agent_section_and_context_source_is_recorded(self):
        snap = _snapshot(agent_overrides={"harness": "aider"}, harness_options={"edit_format": "whole"})
        assert "harness_options" not in snap["agent"]
        assert snap["harness_options"] == {"edit_format": "whole"}
        assert snap["llm"]["context_window_source"] == "catalog"
        fallback = _snapshot(_model(model_id="vendor/nocw", context_window=0))
        assert fallback["llm"]["context_window_source"] == "fallback"
        assert fallback["presets"] == {"harness": None, "sampling": None, "budget": None}

    def test_options_for_another_harness_fail_the_snapshot(self):
        with pytest.raises(ValueError, match="Unknown opencode options"):
            _snapshot(harness_options={"edit_format": "whole"})


# ── POST /jobs/agent/ ──────────────────────────────────────────────────


class TestAgentCreate:
    def test_presets_are_applied_into_job_fields(self, user_client):
        client, user = user_client
        app, model = _app(), _model()
        whole = _preset(user, "harness", "aider-whole", {"harness": "aider", "options": {"edit_format": "whole"}})
        sampling = _preset(
            user,
            "sampling",
            "t0",
            {"llm_params": {"temperature": 0, "top_p": 0.9}, "sampling_policy": "provider_default"},
        )
        budget = _preset(user, "budget", "small", {"max_llm_requests": 30, "wall_clock_seconds": 1800})
        res, dispatch = _post(
            client,
            "/jobs/agent/",
            {
                "app_requirement_ids": [app.id],
                "model_ids": [model.id],
                "harness_preset_ids": [whole.id, preset_service.system_default_preset("harness", "opencode").id],
                "sampling_preset_id": sampling.id,
                "budget_preset_id": budget.id,
                "llm_params": {"top_p": 0.5},
                "agent_config": {"wall_clock_seconds": 2400},
                "max_tokens": 16000,
                "trials": 2,
                "base_seed": 11,
            },
        )
        assert res.status_code == 200, res.content
        assert res.json()["job_count"] == 4
        jobs = list(GenerationJob.objects.filter(created_by=user))
        assert len(jobs) == 4
        assert dispatch.call_count == 4
        aider = [j for j in jobs if j.agent_config["harness"] == "aider"]
        assert {j.harness_preset_id for j in aider} == {whole.id}
        job = aider[0]
        assert job.harness_options == {"edit_format": "whole"}
        assert job.llm_params == {"max_tokens": 16000, "temperature": 0, "top_p": 0.5}
        assert job.agent_config == {
            "max_llm_requests": 30,
            "wall_clock_seconds": 2400,
            "sampling_policy": "provider_default",
            "harness": "aider",
        }
        assert (job.sampling_preset_id, job.budget_preset_id) == (sampling.id, budget.id)
        snap = job.resolved_bundle
        assert snap["agent"]["max_llm_requests"] == 30
        assert snap["gateway"]["forced_params"]["temperature"] == 0
        assert snap["harness"]["edit_format"] == "whole"
        assert snap["presets"]["harness"]["slug"] == "aider-whole"
        assert job.bundle_key == snap["method_key"]
        for suffix in ("+opts:", "+llm:", "+budget:"):
            assert suffix in job.bundle_key
        batch = GenerationBatch.objects.get(id=res.json()["batch_id"])
        assert batch.request["base_seed"] == 11
        assert batch.request["order_seed"] == 11
        assert batch.request["harness_preset_ids"][0] == whole.id
        assert batch.request["sampling_preset_id"] == sampling.id
        # Common random numbers: one seed per (task, trial) for every preset and model.
        for trial in (0, 1):
            assert len({j.experiment_seed for j in jobs if j.repeat_index == trial}) == 1

    def test_defaults_and_legacy_fields_record_system_presets(self, user_client, settings):
        client, user = user_client
        settings.GENERATION_DEFAULT_STACK = "fastapi-react"
        model = _model()
        res, _ = _post(
            client,
            "/jobs/agent/",
            {"description": "Build a thing", "model_ids": [model.id], "harnesses": ["aider", "aider"]},
        )
        assert res.status_code == 200, res.content
        job = GenerationJob.objects.get(created_by=user)
        assert job.stack_slug == "fastapi-react"
        assert job.harness_preset.slug == "aider-default"
        assert job.sampling_preset.slug == "provider-default"
        assert job.budget_preset.slug == "platform-default"
        assert job.llm_params == {}
        assert job.agent_config == {"harness": "aider"}

    def test_profile_stack_applies_when_no_stack_is_given(self, user_client):
        client, user = user_client
        profile = GenerationProfileFactory(scaffolding_slug="fastapi-vue", llm_config={"temperature": 0.4})
        res, _ = _post(
            client,
            "/jobs/agent/",
            {"description": "x", "model_ids": [_model().id], "profile_id": profile.id},
        )
        assert res.status_code == 200, res.content
        job = GenerationJob.objects.get(created_by=user)
        assert job.stack_slug == "fastapi-vue"
        assert job.llm_params == {"temperature": 0.4}

    def test_conflicting_and_invalid_requests(self, user_client):
        client, user = user_client
        model = _model()
        aider_default = preset_service.system_default_preset("harness", "aider")
        cases = [
            ({"harnesses": ["aider"], "harness_preset_ids": [aider_default.id]}, 400, "not both"),
            ({"agent_config": {"harness": "aider"}, "harnesses": ["aider"]}, 400, "agent_config.harness"),
            ({"harness_preset_ids": [999_999]}, 404, "Unknown preset id"),
            ({"sampling_preset_id": aider_default.id}, 400, "not a sampling preset"),
            ({"stack_slug": "cobol-mainframe"}, 404, "Unknown stack slug"),
            ({"llm_params": {"temperature": 5}}, 400, "temperature"),
            ({"harnesses": ["cursor"]}, 400, "Unknown harness"),
        ]
        for extra, status, needle in cases:
            res, _ = _post(client, "/jobs/agent/", {"description": "x", "model_ids": [model.id], **extra})
            assert res.status_code == status, (extra, res.content)
            assert needle in res.json()["detail"], (extra, res.json())
        theirs = _preset(UserFactory(), "budget", "theirs", {"max_llm_requests": 5})
        res, _ = _post(
            client,
            "/jobs/agent/",
            {"description": "x", "model_ids": [model.id], "budget_preset_id": theirs.id},
        )
        assert res.status_code == 404
        res, _ = _post(client, "/jobs/agent/", {"description": "x", "model_ids": []})
        assert res.status_code == 400
        assert res.json()["code"] == "no_models"
        assert not GenerationJob.objects.filter(created_by=user).exists()

    def test_duplicate_harness_configs_are_refused(self, user_client):
        client, user = user_client
        a = _preset(
            user,
            "harness",
            "mini-a",
            {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 90}},
        )
        b = _preset(
            user,
            "harness",
            "mini-b",
            {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 90.0}},
        )
        res, _ = _post(
            client,
            "/jobs/agent/",
            {"description": "x", "model_ids": [_model().id], "harness_preset_ids": [a.id, b.id]},
        )
        assert res.status_code == 400
        assert res.json()["code"] == "duplicate_harness_config"

    def test_stored_preset_that_no_longer_validates_is_refused(self, user_client):
        client, user = user_client
        preset = _preset(
            user,
            "harness",
            "mini-long",
            {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 120}},
        )
        RunPreset.objects.filter(id=preset.id).update(
            config={"harness": "mini-swe-agent", "options": {"removed_option": 1}},
        )
        res, _ = _post(
            client,
            "/jobs/agent/",
            {"description": "x", "model_ids": [_model().id], "harness_preset_ids": [preset.id]},
        )
        assert res.status_code == 400
        assert res.json()["code"] == "invalid_preset"
        assert "no longer valid" in res.json()["detail"]

    def test_missing_api_key_is_preflighted(self, user_client):
        client, _ = user_client
        res, _ = _post(client, "/jobs/agent/", {"description": "x", "model_ids": [_model().id]}, has_key=False)
        assert res.status_code == 400
        assert res.json()["code"] == "missing_api_key"


# ── POST /jobs/agent/preview/ ──────────────────────────────────────────


class TestAgentPreview:
    def test_shape_and_no_writes(self, user_client):
        client, _user = user_client
        app = _app()
        model = _model()
        claude = _model(
            model_id="anthropic/claude-x",
            metadata={"openrouter_supported_parameters": ["tools", "temperature", "max_tokens"]},
        )
        jobs_before, batches_before = GenerationJob.objects.count(), GenerationBatch.objects.count()
        res, dispatch = _post(
            client,
            "/jobs/agent/preview/",
            {
                "app_requirement_ids": [app.id],
                "model_ids": [model.id, claude.id],
                "harnesses": ["opencode", "aider"],
                "llm_params": {"temperature": 0.2},
                "trials": 3,
                "base_seed": 5,
            },
        )
        assert res.status_code == 200, res.content
        body = res.json()
        assert (GenerationJob.objects.count(), GenerationBatch.objects.count()) == (jobs_before, batches_before)
        dispatch.assert_not_called()
        assert (body["run_count"], body["task_count"], body["model_count"], body["harness_count"], body["trials"]) == (
            12,
            1,
            2,
            2,
            3,
        )
        assert body["stack_slug"] == "flask-react"
        assert body["graded"] is True
        assert body["llm_params"] == {"effective": {"temperature": 0.2}, "sources": {"temperature": "inline"}}
        assert body["agent_config"]["max_llm_requests"] == 100
        assert "harness" not in body["agent_config"]
        assert body["max_cost_usd_total"] == pytest.approx(12 * 5.0)
        assert [h["preset"]["slug"] for h in body["harnesses"]] == ["opencode-default", "aider-default"]
        aider = body["harnesses"][1]
        assert aider["effective_options"] == {"edit_format": "diff", "map_tokens": 1024}
        assert aider["non_default_options"] == []
        rows = {m["model_slug"]: m for m in body["models"]}
        assert rows["vendor/coder"]["seed_forwarded"] is True
        # A model without seed support: seed is reported apart, never as a dropped param.
        assert rows["anthropic/claude-x"]["seed_forwarded"] is False
        assert rows["anthropic/claude-x"]["dropped_params"] == {}
        assert "seed" not in rows["anthropic/claude-x"]["forced_params"]
        assert rows["vendor/coder"]["output_cap"] == 32000
        codes = _codes(body)
        assert "params_dropped" not in codes
        assert codes.get("single_trial") is None
        assert codes.get("random_seeds") is None

    def test_warning_codes(self, user_client, settings):
        client, user = user_client
        expensive = _model(
            model_id="frontier/big",
            input_price_per_token=0.00001,  # $10 / M: 100 requests x 20k tokens = $20 > $5
            metadata={"openrouter_supported_parameters": ["tools", "seed", "max_tokens"]},
            context_window=0,
            max_output_tokens=8000,
        )
        no_tools = _model(model_id="vendor/plain", supports_function_calling=False)
        whole = _preset(user, "harness", "aider-whole", {"harness": "aider", "options": {"edit_format": "whole"}})
        res, _ = _post(
            client,
            "/jobs/agent/preview/",
            {
                "description": "Free-form task",
                "model_ids": [expensive.id, no_tools.id],
                "harness_preset_ids": [whole.id, preset_service.system_default_preset("harness", "opencode").id],
                "llm_params": {"top_p": 0.5, "reasoning": {"max_tokens": 9000}},
                "agent_config": {"wall_clock_seconds": 600, "sampling_policy": "harness_native"},
            },
            has_key=False,
        )
        assert res.status_code == 200, res.content
        body = res.json()
        codes = _codes(body)
        expected = {
            "missing_api_key": "error",
            "models_without_tools": "error",
            "reasoning_exceeds_output_cap": "error",
            "params_dropped": "warn",
            "context_window_fallback": "warn",
            "cost_cap_may_bind": "warn",
            "wall_clock_may_bind": "warn",
            "whole_file_small_output": "warn",
            "harness_native_sampling": "warn",
            "ungraded_task": "warn",
            "non_default_harness_options": "info",
            "budget_changes_brief": "info",
            "single_trial": "info",
            "random_seeds": "info",
        }
        for code, severity in expected.items():
            assert codes.get(code) == severity, (code, body["warnings"])
        assert body["graded"] is False
        dropped = [w for w in body["warnings"] if w["code"] == "params_dropped"]
        assert dropped[0]["model_id"] == expensive.id
        assert "seed" not in dropped[0]["detail"]
        assert "top_p" in dropped[0]["detail"]
        severities = [w["severity"] for w in body["warnings"]]
        assert severities == sorted(severities, key=["error", "warn", "info"].index)
        tools_row = next(m for m in body["models"] if m["model_id"] == no_tools.id)
        assert tools_row["tool_harness_ok"] is False

    def test_duplicate_invalid_and_resolution_errors(self, user_client):
        client, user = user_client
        a = _preset(user, "harness", "aider-nomap", {"harness": "aider", "options": {"map_tokens": 0}})
        b = _preset(user, "harness", "aider-nomap-2", {"harness": "aider", "options": {"map_tokens": 0}})
        bad_budget = _preset(user, "budget", "broken", {"max_llm_requests": 5})
        RunPreset.objects.filter(id=bad_budget.id).update(config={"max_llm_requests": 0})
        model = _model()
        with mock.patch(
            "backend.generation.services.agent.harnesses.opencode.default_prompt",
            side_effect=ValueError("No captured default prompt"),
        ):
            res, _ = _post(
                client,
                "/jobs/agent/preview/",
                {
                    "description": "x",
                    "model_ids": [model.id],
                    "harness_preset_ids": [a.id, b.id, preset_service.system_default_preset("harness", "opencode").id],
                    "budget_preset_id": bad_budget.id,
                    "trials": 2,
                    "base_seed": 1,
                },
            )
        assert res.status_code == 200, res.content
        codes = _codes(res.json())
        assert codes["duplicate_harness_config"] == "error"
        assert codes["invalid_preset"] == "error"
        assert codes["resolution_failed"] == "error"
        failed = next(w for w in res.json()["warnings"] if w["code"] == "resolution_failed")
        assert "opencode-default" in failed["detail"]

    def test_empty_form_reports_no_tasks_and_no_models(self, user_client):
        client, _ = user_client
        res, _ = _post(client, "/jobs/agent/preview/", {})
        assert res.status_code == 200
        codes = _codes(res.json())
        assert codes["no_tasks"] == codes["no_models"] == "error"
        assert res.json()["run_count"] == 0

    def test_large_batch_and_sources(self, user_client):
        client, user = user_client
        apps = [_app(slug=f"app-{i}") for i in range(6)]
        models = [_model(model_id=f"vendor/m{i}") for i in range(3)]
        sampling = _preset(user, "sampling", "warm", {"llm_params": {"temperature": 0.7, "top_p": 0.9}})
        profile = GenerationProfileFactory(llm_config={"top_k": 40, "top_p": 0.8})
        res, _ = _post(
            client,
            "/jobs/agent/preview/",
            {
                "app_requirement_ids": [a.id for a in apps],
                "model_ids": [m.id for m in models],
                "sampling_preset_id": sampling.id,
                "profile_id": profile.id,
                "max_tokens": 8000,
                "llm_params": {"temperature": 0.1},
                "trials": 3,
                "base_seed": 3,
            },
        )
        body = res.json()
        assert body["run_count"] == 54
        assert _codes(body)["large_batch"] == "warn"
        assert body["llm_params"]["effective"] == {"max_tokens": 8000, "top_k": 40, "top_p": 0.9, "temperature": 0.1}
        assert body["llm_params"]["sources"] == {
            "max_tokens": "max_tokens",
            "top_k": "profile",
            "top_p": "preset",
            "temperature": "inline",
        }
        assert body["presets"]["sampling"]["slug"] == "warm"
        assert all(m["output_cap"] == 8000 for m in body["models"])


# ── GET /agent/config/ ─────────────────────────────────────────────────


def test_agent_config_endpoint(user_client):
    client, _ = user_client
    body = client.get(f"{API}/agent/config/").json()
    assert body["defaults"] == {
        "harness": "opencode",
        "sampling_policy": "provider_default",
        "wall_clock_seconds": 3600,
        "max_llm_requests": 100,
        "max_total_tokens": 30_000_000,
        "max_cost_usd": 5.0,
        "network_policy": "packages",
    }
    assert body["limits"]["max_llm_requests"] == {"type": "int", "min": 1, "max": 2000}
    assert body["limits"]["max_cost_usd"]["type"] == "float"
    assert body["network_policies"] == ["none", "packages"]
    assert body["sampling_policies"] == ["provider_default", "harness_native"]
    assert body["default_provider_routing"]["require_parameters"] is True
    assert body["default_stack_slug"] == "flask-react"
    assert body["default_max_output_tokens"] == 32000
    harnesses = {h["name"]: h for h in body["harnesses"]}
    assert harnesses["opencode"]["is_default"] is True
    assert harnesses["aider"]["is_default"] is False
    assert harnesses["aider"]["default_options"] == {"edit_format": "diff", "map_tokens": 1024}
    assert harnesses["mini-swe-agent"]["option_schema"][0]["key"] == "command_timeout_seconds"
    assert harnesses["opencode"]["option_schema"] == []
    assert all(h["description"] and h["version"] for h in body["harnesses"])
    # The legacy listing keeps its shape.
    legacy = client.get(f"{API}/agent/harnesses/").json()
    assert set(legacy[0]) == {"name", "label", "version", "requires_tool_calling"}


# ── cloning ────────────────────────────────────────────────────────────


class TestCloning:
    @pytest.fixture
    def preset_job(self, settings, tmp_path):
        settings.GENERATION_RUNS_ROOT = str(tmp_path / "runs")
        user = UserFactory()
        harness_preset = _preset(
            user,
            "harness",
            "mini-long",
            {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 120}},
        )
        sampling = _preset(user, "sampling", "t0", {"llm_params": {"temperature": 0}})
        experiment = ExperimentFactory(created_by=user)
        condition = ExperimentConditionFactory(experiment=experiment, model=_model(), harness="mini-swe-agent")
        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.AGENT,
            created_by=user,
            model=condition.model,
            stack_slug="flask-react",
            app_requirement=_app(),
            experiment=experiment,
            condition=condition,
            repeat_index=1,
            llm_params={"temperature": 0},
            agent_config={"harness": "mini-swe-agent"},
            harness_options={"command_timeout_seconds": 120},
            harness_preset=harness_preset,
            sampling_preset=sampling,
            budget_preset=preset_service.system_default_preset("budget"),
            experiment_seed=42,
        )
        apply_agent_snapshot_to_job(job)
        job.status = GenerationJob.Status.FAILED
        job.save()
        return job

    def test_retry_keeps_presets_cell_and_configuration(self, preset_job):
        retry = clone_job(preset_job, preset_job.created_by, reuse_snapshot=False, new_seed=True, keep_cell=True)
        assert (retry.harness_preset_id, retry.sampling_preset_id, retry.budget_preset_id) == (
            preset_job.harness_preset_id,
            preset_job.sampling_preset_id,
            preset_job.budget_preset_id,
        )
        assert retry.harness_options == {"command_timeout_seconds": 120}
        assert (retry.experiment_id, retry.condition_id, retry.repeat_index) == (
            preset_job.experiment_id,
            preset_job.condition_id,
            1,
        )
        original = preset_job.resolved_bundle
        assert retry.resolved_bundle["config_fingerprint"] == original["config_fingerprint"]
        # The replacement trial runs under the cell's seed: same run identity.
        assert retry.experiment_seed == 42
        assert retry.resolved_bundle["run_fingerprint"] == original["run_fingerprint"]
        assert retry.resolved_bundle["harness"]["config"]["environment"] == {"timeout": 120}
        assert retry.resolved_bundle["presets"] == original["presets"]
        assert retry.bundle_key == original["method_key"]

    def test_replay_and_rerun_keep_the_method_key(self, preset_job):
        replay = clone_job(preset_job, preset_job.created_by, reuse_snapshot=True, new_seed=False, keep_batch=False)
        rerun = clone_job(preset_job, preset_job.created_by, reuse_snapshot=True, new_seed=True)
        method_key = preset_job.resolved_bundle["method_key"]
        assert "+opts:" in method_key
        assert replay.bundle_key == rerun.bundle_key == method_key
        assert replay.resolved_bundle["run_fingerprint"] == preset_job.resolved_bundle["run_fingerprint"]
        assert rerun.resolved_bundle["config_fingerprint"] == preset_job.resolved_bundle["config_fingerprint"]
        assert rerun.harness_preset_id == preset_job.harness_preset_id

    def test_failed_snapshot_leaves_no_orphan(self, preset_job):
        count = GenerationJob.objects.count()
        GenerationJob.objects.filter(id=preset_job.id).update(harness_options={"edit_format": "whole"})
        preset_job.refresh_from_db()
        with pytest.raises(ValueError, match="Unknown mini-swe-agent options"):
            clone_job(preset_job, preset_job.created_by, reuse_snapshot=False, new_seed=True)
        assert GenerationJob.objects.count() == count


# ── runner validity ────────────────────────────────────────────────────


def test_seed_only_drop_is_info_other_drops_warn():
    def notes(dropped):
        snap = {"gateway": {"dropped_params": dropped}, "llm": {"model_slug": "m"}}
        verdict = assess_validity(snap, {"agent": {}, "evaluation": {"status": "passed"}})
        return {n["code"]: n["severity"] for n in verdict["notes"]}

    assert notes({"seed": "x"})["params_not_applied"] == "info"
    assert notes({"seed": "x", "top_p": "x"})["params_not_applied"] == "warn"
    assert "params_not_applied" not in notes({})
