# ruff: noqa: S105, S106 — fake secrets
"""Outbound webhooks: formatting, signing, SSRF, de-duplication, signals, API scoping (network mocked)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.apps import apps
from django.test import Client
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import StudyStatus
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import StudyFactory
from backend.inference.security import UnsafeTargetError
from backend.integrations.models import WebhookDelivery
from backend.integrations.models import WebhookSubscription
from backend.integrations.services import webhooks
from backend.integrations.tasks import deliver_webhook
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PUBLIC_IP = "93.184.216.34"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/abcdefSECRET1234"


@pytest.fixture(autouse=True)
def _public_dns():
    """Every hostname resolves to a public address unless a test says otherwise."""
    with mock.patch.object(webhooks.security, "resolve", return_value=(PUBLIC_IP,)) as resolve:
        yield resolve


@pytest.fixture
def no_dispatch():
    with mock.patch.object(deliver_webhook, "delay") as delay:
        yield delay


def make_sub(user, kind="generic", events=None, **kw) -> WebhookSubscription:
    sub = WebhookSubscription(
        user=user,
        name=kw.pop("name", f"{kind} hook"),
        kind=kind,
        events=events if events is not None else list(webhooks.EVENT_TYPES),
        **{k: v for k, v in kw.items() if k not in ("url", "secret")},
    )
    if kind != "telegram":
        sub.set_url(kw.get("url", "https://example.com/hook/path?x=1"))
    if kw.get("secret"):
        sub.set_secret(kw["secret"])
    sub.save()
    return sub


def payload(status="completed") -> dict:
    return {
        "event": f"run.{status}",
        "event_id": f"run.{status}:abc:{status}",
        "occurred_at": "2026-10-26T10:00:00+00:00",
        "summary": "Run completed: Todo app",
        "object": {"type": "generation_run", "id": "abc", "title": "Todo app", "status": status},
        "url": "https://lab.example.com/samples/abc",
        "fields": [{"name": "Model", "value": "gpt-4o"}, {"name": "Duration", "value": "1m 2s"}],
        "data": {"model": "gpt-4o"},
    }


# ── models / secrets ────────────────────────────────────────────────


def test_url_and_secret_encrypted_with_masked_hints():
    sub = make_sub(UserFactory(), kind="slack", url=SLACK_URL, secret="supersecretvalue99")
    sub.refresh_from_db()
    assert SLACK_URL not in sub.encrypted_url
    assert sub.get_url() == SLACK_URL
    assert sub.url_hint == "https://hooks.slack.com/…1234"
    assert "SECRET" not in sub.url_hint
    assert sub.get_secret() == "supersecretvalue99"
    assert sub.secret_hint == "…ue99"


# ── formatting per kind ─────────────────────────────────────────────


def _body(request) -> dict:
    return json.loads(request.body)


def test_slack_format():
    sub = make_sub(UserFactory(), kind="slack", url=SLACK_URL)
    req = webhooks.build_request(sub, payload())
    body = _body(req)
    assert req.url == SLACK_URL
    assert body["text"] == "Run completed: Todo app"
    att = body["attachments"][0]
    assert att["color"] == "#22c55e"
    assert "<https://lab.example.com/samples/abc|" in att["blocks"][0]["text"]["text"]
    assert any(f["text"].startswith("*Model*") for f in att["blocks"][1]["fields"])


def test_discord_format_color_by_status():
    sub = make_sub(UserFactory(), kind="discord", url="https://discord.com/api/webhooks/1/tok")
    ok = _body(webhooks.build_request(sub, payload("completed")))["embeds"][0]
    bad = _body(webhooks.build_request(sub, payload("failed")))["embeds"][0]
    assert ok["color"] == 0x22C55E
    assert bad["color"] == 0xEF4444
    assert ok["url"] == "https://lab.example.com/samples/abc"
    assert {"name": "Model", "value": "gpt-4o", "inline": True} in ok["fields"]
    assert _body(webhooks.build_request(sub, payload()))["allowed_mentions"] == {"parse": []}


def test_teams_adaptive_card():
    sub = make_sub(UserFactory(), kind="teams", url="https://example.webhook.office.com/webhookb2/x")
    body = _body(webhooks.build_request(sub, payload("failed")))
    card = body["attachments"][0]["content"]
    assert body["type"] == "message"
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["color"] == "Attention"
    assert card["body"][1]["facts"][0] == {"title": "Model", "value": "gpt-4o"}
    assert card["actions"][0]["url"] == "https://lab.example.com/samples/abc"


def test_ntfy_json_publish_with_token():
    sub = make_sub(UserFactory(), kind="ntfy", url="https://ntfy.sh/my-topic", secret="tk_abcdefghijkl")
    req = webhooks.build_request(sub, payload("failed"))
    body = _body(req)
    assert req.url == "https://ntfy.sh/"
    assert req.headers["Authorization"] == "Bearer tk_abcdefghijkl"
    assert body["topic"] == "my-topic"
    assert body["priority"] == 4
    assert body["tags"] == ["x"]
    assert body["click"] == "https://lab.example.com/samples/abc"
    assert "Model: gpt-4o" in body["message"]


def test_ntfy_self_hosted_subpath():
    sub = make_sub(UserFactory(), kind="ntfy", url="https://push.example.org/ntfy/alerts")
    req = webhooks.build_request(sub, payload())
    assert req.url == "https://push.example.org/ntfy/"
    assert _body(req)["topic"] == "alerts"


def test_telegram_markdown_v2():
    sub = make_sub(UserFactory(), kind="telegram", secret="123456:ABC-token_xyz", telegram_chat_id="-100123")
    p = payload()
    p["summary"] = "Run completed: my_app (v1.2)!"
    req = webhooks.build_request(sub, p)
    body = _body(req)
    assert req.url == "https://api.telegram.org/bot123456:ABC-token_xyz/sendMessage"
    assert body["chat_id"] == "-100123"
    assert body["parse_mode"] == "MarkdownV2"
    assert r"*Run completed: my\_app \(v1\.2\)\!*" in body["text"]
    assert "[Open in LLM Lab](https://lab.example.com/samples/abc)" in body["text"]


def test_generic_signature():
    signing_key = "fake-" + "signing-key"
    sub = make_sub(UserFactory(), kind="generic", secret=signing_key)
    req = webhooks.build_request(sub, payload(), delivery_id=42)
    expected = "sha256=" + hmac.new(signing_key.encode(), req.body, hashlib.sha256).hexdigest()
    assert req.headers["X-LLMLab-Signature"] == expected
    assert req.headers["X-LLMLab-Event"] == "run.completed"
    assert req.headers["X-LLMLab-Delivery"] == "42"
    assert _body(req) == payload()


def test_generic_unsigned_without_secret():
    req = webhooks.build_request(make_sub(UserFactory(), kind="generic"), payload())
    assert "X-LLMLab-Signature" not in req.headers


# ── SSRF ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.10", "::1"])
def test_ssrf_rejects_private_addresses(_public_dns, address):
    _public_dns.return_value = (address,)
    with pytest.raises(UnsafeTargetError):
        webhooks.validate_webhook_url("https://evil.example.com/hook")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",  # http without DEBUG
        "ftp://example.com/hook",
        "https://user:pw@example.com/hook",
        "https://localhost/hook",
        "https://postgres/hook",
        "https://example.com/hook#frag",
    ],
)
def test_ssrf_rejects_bad_urls(settings, url):
    settings.DEBUG = False
    with pytest.raises(UnsafeTargetError):
        webhooks.validate_webhook_url(url)


def test_http_allowed_only_in_debug(settings):
    settings.DEBUG = True
    assert webhooks.validate_webhook_url("http://example.com/hook").scheme == "http"


def test_post_connects_to_vetted_ip_and_refuses_redirects():
    conn = mock.MagicMock()
    conn.getresponse.return_value = mock.Mock(status=302, read=mock.Mock(return_value=b""))
    with mock.patch.object(webhooks, "_connect", return_value=conn) as connect:
        resp = webhooks.post("https://example.com/hook?a=1", b"{}", {"Content-Type": "application/json"})
    target = connect.call_args[0][0]
    assert target.addresses == (PUBLIC_IP,)
    assert target.path == "/hook?a=1"
    assert conn.request.call_args[0][:2] == ("POST", "/hook?a=1")
    assert conn.request.call_args[1]["headers"]["Host"] == "example.com"
    assert resp.status == 302
    sub = make_sub(UserFactory())
    with mock.patch.object(webhooks, "post", return_value=webhooks.Response(302, "")):
        result = webhooks.attempt(sub, payload())
    assert not result.ok
    assert "redirect" in result.error


def test_post_rejects_unsafe_target(_public_dns):
    _public_dns.return_value = ("10.1.2.3",)
    with pytest.raises(webhooks.DeliveryError) as err:
        webhooks.post("https://internal.example.com/hook", b"{}", {})
    assert err.value.retryable is False


def test_create_api_rejects_private_target(client, _public_dns):
    user = UserFactory()
    client.force_login(user)
    _public_dns.return_value = ("10.0.0.7",)
    resp = client.post(
        "/api/integrations/webhooks/",
        {"name": "x", "kind": "generic", "url": "https://intranet.example.com/h", "events": []},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert WebhookSubscription.objects.count() == 0


# ── delivery recording / retries ────────────────────────────────────


def test_send_delivery_records_success_and_failure():
    sub = make_sub(UserFactory(), kind="slack", url=SLACK_URL)
    delivery = WebhookDelivery.objects.create(subscription=sub, event_key="k1", event_type="test", payload=payload())
    with mock.patch.object(webhooks, "post", return_value=webhooks.Response(200, "ok")):
        assert webhooks.send_delivery(delivery).ok
    delivery.refresh_from_db()
    sub.refresh_from_db()
    assert (delivery.status, delivery.attempts, delivery.response_code) == ("sent", 1, 200)
    assert sub.last_success_at is not None

    d2 = WebhookDelivery.objects.create(subscription=sub, event_key="k2", event_type="test", payload=payload())
    echo = f"invalid_token {SLACK_URL}"
    with mock.patch.object(webhooks, "post", return_value=webhooks.Response(503, echo)):
        result = webhooks.send_delivery(d2, final=False)
    d2.refresh_from_db()
    assert result.retryable
    assert d2.status == "pending"  # a retry follows
    assert SLACK_URL not in d2.error  # echoed secrets are scrubbed
    with mock.patch.object(webhooks, "post", return_value=webhooks.Response(404, "no_service")):
        result = webhooks.send_delivery(d2, final=False)
    d2.refresh_from_db()
    assert not result.retryable
    assert (d2.status, d2.attempts, d2.response_code) == ("failed", 2, 404)
    sub.refresh_from_db()
    assert "HTTP 404" in sub.last_error


def test_task_retries_transient_failure():
    sub = make_sub(UserFactory())
    delivery = WebhookDelivery.objects.create(subscription=sub, event_key="k", event_type="test", payload=payload())
    err = webhooks.DeliveryError("cannot connect", retryable=True)
    with (
        mock.patch.object(webhooks, "post", side_effect=err),
        mock.patch.object(deliver_webhook, "retry", side_effect=RuntimeError("retry")) as retry,
        pytest.raises(RuntimeError),
    ):
        deliver_webhook.apply(args=[delivery.pk], throw=True)
    assert retry.call_args[1]["countdown"] == 15
    delivery.refresh_from_db()
    assert delivery.status == "pending"
    assert delivery.attempts == 1


def test_task_skips_already_sent():
    sub = make_sub(UserFactory())
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        event_key="k",
        event_type="test",
        payload=payload(),
        status="sent",
    )
    with mock.patch.object(webhooks, "post") as post:
        deliver_webhook.apply(args=[delivery.pk])
    post.assert_not_called()


# ── events: signals, dedupe, sweep ──────────────────────────────────


def test_every_label_resolves_through_apps_get_model():
    """``signals.connect`` resolves every label in ``ready()``: a stale one breaks ``django.setup()``."""
    labels = set(webhooks._STATUS_EVENTS) | set(webhooks._SWEEP_TIME_FIELDS) | set(webhooks._PAYLOAD_BUILDERS)
    assert set(webhooks._STATUS_EVENTS) == set(webhooks._SWEEP_TIME_FIELDS) == set(webhooks._PAYLOAD_BUILDERS)
    for label in labels:
        model = apps.get_model(label)
        assert webhooks.model_key(model) == label
        status_field = model._meta.get_field("status")
        assert set(webhooks._STATUS_EVENTS[label]) <= {value for value, _ in status_field.choices}, label
        for name in webhooks._SWEEP_TIME_FIELDS[label]:
            model._meta.get_field(name)
        model._meta.get_field("created_by")
    assert apps.get_model(webhooks.RUN_LABEL) is GenerationRun
    assert apps.get_model(webhooks.STUDY_LABEL) is Study
    every_event = {event for events in webhooks._STATUS_EVENTS.values() for event in events.values()}
    assert every_event == set(webhooks.EVENT_TYPES)


def _run(user, **kw) -> GenerationRun:
    return GenerationRunFactory.create(created_by=user, **kw)


def test_signal_creates_delivery_on_terminal_status(no_dispatch, django_capture_on_commit_callbacks):
    user = UserFactory()
    sub = make_sub(user, events=["run.completed"])
    make_sub(user, events=["run.completed"], enabled=False)
    make_sub(UserFactory(), events=["run.completed"])  # someone else's
    run = _run(user)
    assert WebhookDelivery.objects.count() == 0
    with django_capture_on_commit_callbacks(execute=True):
        run.status = RunStatus.COMPLETED
        run.completed_at = timezone.now()
        run.duration_seconds = 83
        run.metrics = {"cost_usd": 0.0123, "total_tokens": 4000}
        run.save()
    delivery = WebhookDelivery.objects.get()
    assert delivery.subscription == sub
    assert delivery.event_key == f"run.completed:{run.pk}:completed"
    assert delivery.payload["object"] == {
        "type": "generation_run",
        "id": str(run.pk),
        "title": run.task_version.content["title"],
        "status": "completed",
    }
    assert delivery.payload["url"].endswith(f"/samples/{run.pk}")
    fields = {f["name"]: f["value"] for f in delivery.payload["fields"]}
    assert fields["Duration"] == "1m 23s"
    assert fields["Cost"] == "$0.0123"
    # No evaluation yet: reported as not measured, never as a pass rate.
    assert fields["Pass rate"] == "not measured"
    assert delivery.payload["data"]["eval_status"] == "not_measured"
    assert "pass_rate" not in delivery.payload["data"]
    no_dispatch.assert_called_once_with(delivery.pk)


def test_run_pass_rate_only_when_measured(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["run.completed"])
    run = _run(user)
    run.status = RunStatus.COMPLETED
    run.metrics = {
        "eval": {
            "eval_suite": "suite@1",
            "eval_level": "security",
            "status": "measured",
            "measured": True,
            "agent_pass_rate": 0.75,
        },
    }
    run.save()
    fields = {f["name"]: f["value"] for f in WebhookDelivery.objects.get().payload["fields"]}
    assert fields["Pass rate"] == "75%"


def test_dedupe_same_event_once(no_dispatch, django_capture_on_commit_callbacks):
    user = UserFactory()
    make_sub(user, events=["run.failed"])
    run = _run(user)
    with django_capture_on_commit_callbacks(execute=True):
        run.status = RunStatus.FAILED
        run.save()
        run.error_message = "boom"
        run.save()
        assert webhooks.emit(run) == 0
    assert WebhookDelivery.objects.count() == 1
    assert no_dispatch.call_count == 1


def test_unsubscribed_event_and_non_terminal_ignored(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["run.failed"])
    run = _run(user)
    run.status = RunStatus.RUNNING
    run.save()
    run.status = RunStatus.COMPLETED
    run.save()
    assert WebhookDelivery.objects.count() == 0


def test_study_runs_opt_in(no_dispatch):
    user = UserFactory()
    plain = make_sub(user, events=["run.completed"])
    opted = make_sub(user, events=["run.completed"], include_study_runs=True)
    study = StudyFactory(created_by=user)
    run = _run(user, study=study)
    run.status = RunStatus.COMPLETED
    run.save()
    subs = set(WebhookDelivery.objects.values_list("subscription_id", flat=True))
    assert subs == {opted.pk}
    assert plain.pk not in subs


def test_analysis_and_study_events(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["analysis.completed", "study.completed", "study.stopped"])
    run = AnalysisRun.objects.create(created_by=user)
    run.status = AnalysisRun.Status.PARTIAL
    run.summary = {"total_findings": 7, "severity_counts": {"high": 2, "low": 5}, "tools_run": 3, "tools_completed": 2}
    run.save(update_fields=["status", "summary"])
    delivery = WebhookDelivery.objects.get(event_type="analysis.completed")
    assert delivery.event_key == f"analysis.completed:{run.pk}:partial"
    fields = {f["name"]: f["value"] for f in delivery.payload["fields"]}
    assert fields["Findings"] == "7"
    assert fields["Severity"] == "2 high · 5 low"
    assert "partial" in delivery.payload["summary"]

    study = StudyFactory(name="Big run", created_by=user)
    _run(user, study=study, status=RunStatus.COMPLETED)
    study.status = StudyStatus.COMPLETED
    study.completed_at = timezone.now()
    study.save()
    done = WebhookDelivery.objects.get(event_type="study.completed")
    assert done.payload["data"]["runs"] == {"completed": 1}
    assert done.payload["url"].endswith(f"/studies/{study.pk}")
    assert done.payload["object"]["type"] == "study"
    assert done.payload["summary"] == "Study completed: Big run"

    stopped = StudyFactory(created_by=user)
    stopped.status = StudyStatus.STOPPED
    stopped.save()
    assert WebhookDelivery.objects.get(event_type="study.stopped").event_key == f"study.stopped:{stopped.pk}:stopped"


def test_save_without_status_in_update_fields_is_ignored(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["run.completed"])
    run = _run(user, status=RunStatus.COMPLETED)
    WebhookDelivery.objects.all().delete()
    run.save(update_fields=["metrics"])
    assert WebhookDelivery.objects.count() == 0


def test_sweep_catches_queryset_updates_and_skips_older_than_subscription(no_dispatch):
    user = UserFactory()
    old_run = _run(user)
    GenerationRun.objects.filter(pk=old_run.pk).update(
        status=RunStatus.COMPLETED,
        completed_at=timezone.now() - timedelta(minutes=5),
    )
    sub = make_sub(user, events=["run.completed", "study.completed"])
    run = _run(user)
    GenerationRun.objects.filter(pk=run.pk).update(status=RunStatus.COMPLETED, completed_at=timezone.now())
    study = StudyFactory(created_by=user)
    Study.objects.filter(pk=study.pk).update(status="completed", completed_at=timezone.now())
    assert WebhookDelivery.objects.count() == 0
    stats = webhooks.sweep()
    assert stats["emitted"] == 2
    keys = set(WebhookDelivery.objects.filter(subscription=sub).values_list("event_key", flat=True))
    assert keys == {f"run.completed:{run.pk}:completed", f"study.completed:{study.pk}:completed"}
    assert webhooks.sweep()["emitted"] == 0  # idempotent


def test_emit_never_raises():
    user = UserFactory()
    make_sub(user, events=["run.completed"])
    run = _run(user, status=RunStatus.COMPLETED)
    with mock.patch.object(webhooks, "build_payload", side_effect=RuntimeError("bug")):
        assert webhooks.emit(run) == 0


# ── API ─────────────────────────────────────────────────────────────


@pytest.fixture
def alice():
    user = UserFactory()
    c = Client()
    c.force_login(user)
    return c, user


@pytest.fixture
def bob():
    user = UserFactory()
    c = Client()
    c.force_login(user)
    return c, user


def test_api_create_list_secrets_write_only(alice):
    c, _user = alice
    resp = c.post(
        "/api/integrations/webhooks/",
        {"name": "Team", "kind": "slack", "url": SLACK_URL, "events": ["run.failed", "run.completed"]},
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()
    assert data["url_hint"] == "https://hooks.slack.com/…1234"
    assert data["has_url"] is True
    assert data["events"] == ["run.completed", "run.failed"]
    assert SLACK_URL not in resp.content.decode()
    listed = c.get("/api/integrations/webhooks/").json()
    assert [s["id"] for s in listed] == [data["id"]]
    assert SLACK_URL not in json.dumps(listed)


def test_api_validation(alice):
    c, _ = alice
    bad_event = c.post(
        "/api/integrations/webhooks/",
        {"name": "x", "kind": "generic", "url": "https://example.com/h", "events": ["nope"]},
        content_type="application/json",
    )
    assert bad_event.status_code == 400
    no_token = c.post(
        "/api/integrations/webhooks/",
        {"name": "tg", "kind": "telegram", "telegram_chat_id": "123"},
        content_type="application/json",
    )
    assert no_token.status_code == 400
    tg = c.post(
        "/api/integrations/webhooks/",
        {"name": "tg", "kind": "telegram", "telegram_chat_id": "123", "secret": "123456:ABCDEFtoken"},
        content_type="application/json",
    )
    assert tg.status_code == 201
    assert tg.json()["has_secret"] is True
    assert "ABCDEF" not in tg.content.decode()


def test_api_scoped_between_users(alice, bob):
    ca, ua = alice
    cb, _ub = bob
    sub = make_sub(ua)
    WebhookDelivery.objects.create(subscription=sub, event_key="k", event_type="test", payload={})
    base = f"/api/integrations/webhooks/{sub.pk}/"
    assert cb.get("/api/integrations/webhooks/").json() == []
    assert cb.patch(base, {"name": "pwned"}, content_type="application/json").status_code == 404
    assert cb.get(f"{base}deliveries/").status_code == 404
    assert cb.post(f"{base}test/").status_code == 404
    resp = cb.delete(base)
    assert resp.status_code == 404
    assert WebhookSubscription.objects.filter(pk=sub.pk, name="generic hook").exists()
    assert len(ca.get(f"{base}deliveries/").json()) == 1


def test_api_patch_delete_and_test(alice):
    c, user = alice
    sub = make_sub(user, secret="old-secret-value-1")
    base = f"/api/integrations/webhooks/{sub.pk}/"
    resp = c.patch(
        base,
        {"enabled": False, "secret": "", "events": ["report.failed"]},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["has_secret"] is False
    assert resp.json()["events"] == ["report.failed"]

    with mock.patch.object(webhooks, "post", return_value=webhooks.Response(204, "")) as post:
        result = c.post(f"{base}test/").json()
    assert result["ok"] is True
    assert result["status_code"] == 204
    assert result["delivery"]["event_type"] == "test"
    assert result["delivery"]["status"] == "sent"
    assert json.loads(post.call_args[0][1])["event"] == "test"
    deliveries = c.get(f"{base}deliveries/").json()
    assert len(deliveries) == 1

    resp = c.delete(base)
    assert resp.status_code == 204
    assert not WebhookSubscription.objects.filter(pk=sub.pk).exists()


def test_api_event_types(alice):
    c, _ = alice
    types = c.get("/api/integrations/webhooks/event-types/").json()
    ids = [t["id"] for t in types]
    assert {"run.completed", "study.completed", "study.stopped"} <= set(ids)
    assert not [i for i in ids if i.startswith(("generation.", "experi" + "ment."))]
    assert all(t["description"] for t in types)


def test_api_requires_auth():
    assert Client().get("/api/integrations/webhooks/").status_code == 401
