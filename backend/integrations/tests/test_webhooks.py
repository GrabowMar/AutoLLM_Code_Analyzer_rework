# ruff: noqa: S105, S106 — fake secrets
"""Outbound webhooks: formatting, signing, SSRF, de-duplication, signals, API scoping (network mocked)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
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
        "event": f"generation.{status}",
        "event_id": f"generation.{status}:abc:{status}",
        "occurred_at": "2026-10-26T10:00:00+00:00",
        "summary": "Generation completed: Todo app",
        "object": {"type": "generation_job", "id": "abc", "title": "Todo app", "status": status},
        "url": "https://lab.example.com/applications/abc",
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
    assert body["text"] == "Generation completed: Todo app"
    att = body["attachments"][0]
    assert att["color"] == "#22c55e"
    assert "<https://lab.example.com/applications/abc|" in att["blocks"][0]["text"]["text"]
    assert any(f["text"].startswith("*Model*") for f in att["blocks"][1]["fields"])


def test_discord_format_color_by_status():
    sub = make_sub(UserFactory(), kind="discord", url="https://discord.com/api/webhooks/1/tok")
    ok = _body(webhooks.build_request(sub, payload("completed")))["embeds"][0]
    bad = _body(webhooks.build_request(sub, payload("failed")))["embeds"][0]
    assert ok["color"] == 0x22C55E
    assert bad["color"] == 0xEF4444
    assert ok["url"] == "https://lab.example.com/applications/abc"
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
    assert card["actions"][0]["url"] == "https://lab.example.com/applications/abc"


def test_ntfy_json_publish_with_token():
    sub = make_sub(UserFactory(), kind="ntfy", url="https://ntfy.sh/my-topic", secret="tk_abcdefghijkl")
    req = webhooks.build_request(sub, payload("failed"))
    body = _body(req)
    assert req.url == "https://ntfy.sh/"
    assert req.headers["Authorization"] == "Bearer tk_abcdefghijkl"
    assert body["topic"] == "my-topic"
    assert body["priority"] == 4
    assert body["tags"] == ["x"]
    assert body["click"] == "https://lab.example.com/applications/abc"
    assert "Model: gpt-4o" in body["message"]


def test_ntfy_self_hosted_subpath():
    sub = make_sub(UserFactory(), kind="ntfy", url="https://push.example.org/ntfy/alerts")
    req = webhooks.build_request(sub, payload())
    assert req.url == "https://push.example.org/ntfy/"
    assert _body(req)["topic"] == "alerts"


def test_telegram_markdown_v2():
    sub = make_sub(UserFactory(), kind="telegram", secret="123456:ABC-token_xyz", telegram_chat_id="-100123")
    p = payload()
    p["summary"] = "Generation completed: my_app (v1.2)!"
    req = webhooks.build_request(sub, p)
    body = _body(req)
    assert req.url == "https://api.telegram.org/bot123456:ABC-token_xyz/sendMessage"
    assert body["chat_id"] == "-100123"
    assert body["parse_mode"] == "MarkdownV2"
    assert r"*Generation completed: my\_app \(v1\.2\)\!*" in body["text"]
    assert "[Open in LLM Lab](https://lab.example.com/applications/abc)" in body["text"]


def test_generic_signature():
    signing_key = "fake-" + "signing-key"
    sub = make_sub(UserFactory(), kind="generic", secret=signing_key)
    req = webhooks.build_request(sub, payload(), delivery_id=42)
    expected = "sha256=" + hmac.new(signing_key.encode(), req.body, hashlib.sha256).hexdigest()
    assert req.headers["X-LLMLab-Signature"] == expected
    assert req.headers["X-LLMLab-Event"] == "generation.completed"
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


def _job(user, **kw) -> GenerationJob:
    return GenerationJob.objects.create(created_by=user, mode="custom", **kw)


def test_signal_creates_delivery_on_terminal_status(no_dispatch, django_capture_on_commit_callbacks):
    user = UserFactory()
    sub = make_sub(user, events=["generation.completed"])
    make_sub(user, events=["generation.completed"], enabled=False)
    make_sub(UserFactory(), events=["generation.completed"])  # someone else's
    job = _job(user)
    assert WebhookDelivery.objects.count() == 0
    with django_capture_on_commit_callbacks(execute=True):
        job.status = GenerationJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.duration_seconds = 83
        job.save()
    delivery = WebhookDelivery.objects.get()
    assert delivery.subscription == sub
    assert delivery.event_key == f"generation.completed:{job.pk}:completed"
    assert delivery.payload["object"]["id"] == str(job.pk)
    assert delivery.payload["url"].endswith(f"/applications/{job.pk}")
    assert {"name": "Duration", "value": "1m 23s"} in delivery.payload["fields"]
    no_dispatch.assert_called_once_with(delivery.pk)


def test_dedupe_same_event_once(no_dispatch, django_capture_on_commit_callbacks):
    user = UserFactory()
    make_sub(user, events=["generation.failed"])
    job = _job(user)
    with django_capture_on_commit_callbacks(execute=True):
        job.status = GenerationJob.Status.FAILED
        job.save()
        job.error_message = "boom"
        job.save()
        assert webhooks.emit(job) == 0
    assert WebhookDelivery.objects.count() == 1
    assert no_dispatch.call_count == 1


def test_unsubscribed_event_and_non_terminal_ignored(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["generation.failed"])
    job = _job(user)
    job.status = GenerationJob.Status.RUNNING
    job.save()
    job.status = GenerationJob.Status.COMPLETED
    job.save()
    assert WebhookDelivery.objects.count() == 0


def test_experiment_jobs_opt_in(no_dispatch):
    user = UserFactory()
    plain = make_sub(user, events=["generation.completed"])
    opted = make_sub(user, events=["generation.completed"], include_experiment_jobs=True)
    experiment = Experiment.objects.create(name="E", slug="e", created_by=user)
    job = _job(user, experiment=experiment)
    job.status = GenerationJob.Status.COMPLETED
    job.save()
    subs = set(WebhookDelivery.objects.values_list("subscription_id", flat=True))
    assert subs == {opted.pk}
    assert plain.pk not in subs


def test_analysis_and_experiment_events(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["analysis.completed", "experiment.completed"])
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

    experiment = Experiment.objects.create(name="Big run", slug="big", created_by=user)
    _job(user, experiment=experiment, status=GenerationJob.Status.COMPLETED)
    experiment.status = Experiment.Status.COMPLETED
    experiment.completed_at = timezone.now()
    experiment.save()
    exp = WebhookDelivery.objects.get(event_type="experiment.completed")
    assert exp.payload["data"]["jobs"] == {"completed": 1}
    assert exp.payload["url"].endswith(f"/experiments/{experiment.pk}")


def test_analysis_payload_never_reports_an_unscanned_run_as_clean(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["analysis.completed"])
    run = AnalysisRun.objects.create(created_by=user, plan_hash="a" * 64)
    run.status = AnalysisRun.Status.PARTIAL
    # summary schema 2: no static tool succeeded -> static counts are None, AI consensus apart.
    run.summary = {
        "schema_version": 2,
        "total_findings": None,
        "severity_counts": None,
        "static_measured": False,
        "issues": None,
        "ai_findings": 2,
        "tools_run": 2,
        "tools_completed": 1,
        "cost_usd": 0.01,
        "cost_complete": False,
    }
    run.save(update_fields=["status", "summary"])
    payload = WebhookDelivery.objects.get(event_type="analysis.completed").payload
    fields = {f["name"]: f["value"] for f in payload["fields"]}
    assert fields["Findings"].startswith("not measured")
    assert fields["AI consensus"] == "2"
    assert "Severity" not in fields
    data = payload["data"]
    assert (data["total_findings"], data["static_measured"], data["ai_findings"]) == (None, False, 2)
    assert (data["plan_hash"], data["cost_complete"]) == ("a" * 64, False)


@pytest.mark.parametrize(
    ("functional", "expected"),
    [
        ({"status": "passed", "pass_rate": 0.75, "source": "conformance"}, 0.75),
        ({"status": "not_applicable", "pass_rate": 0.0, "source": "conformance"}, None),
        ({"health_ok": True, "pass_rate": 1.0, "source": "smoke"}, None),
    ],
)
def test_generation_payload_pass_rate_is_conformance_only(no_dispatch, functional, expected):
    from backend.integrations.services.webhooks import _generation_payload

    job = _job(UserFactory(), status=GenerationJob.Status.COMPLETED)
    job.metrics = {"functional": functional}
    assert _generation_payload(job)["data"].get("pass_rate") == expected


def test_save_without_status_in_update_fields_is_ignored(no_dispatch):
    user = UserFactory()
    make_sub(user, events=["generation.completed"])
    job = _job(user, status=GenerationJob.Status.COMPLETED)
    WebhookDelivery.objects.all().delete()
    job.save(update_fields=["metrics"])
    assert WebhookDelivery.objects.count() == 0


def test_sweep_catches_queryset_updates_and_skips_older_than_subscription(no_dispatch):
    user = UserFactory()
    old_job = _job(user)
    GenerationJob.objects.filter(pk=old_job.pk).update(
        status=GenerationJob.Status.COMPLETED,
        completed_at=timezone.now() - timedelta(minutes=5),
    )
    sub = make_sub(user, events=["generation.completed", "experiment.completed"])
    job = _job(user)
    GenerationJob.objects.filter(pk=job.pk).update(status=GenerationJob.Status.COMPLETED, completed_at=timezone.now())
    experiment = Experiment.objects.create(name="E", slug="e2", created_by=user)
    Experiment.objects.filter(pk=experiment.pk).update(status="completed", completed_at=timezone.now())
    assert WebhookDelivery.objects.count() == 0
    stats = webhooks.sweep()
    assert stats["emitted"] == 2
    keys = set(WebhookDelivery.objects.filter(subscription=sub).values_list("event_key", flat=True))
    assert keys == {f"generation.completed:{job.pk}:completed", f"experiment.completed:{experiment.pk}:completed"}
    assert webhooks.sweep()["emitted"] == 0  # idempotent


def test_emit_never_raises():
    user = UserFactory()
    make_sub(user, events=["generation.completed"])
    job = _job(user, status=GenerationJob.Status.COMPLETED)
    with mock.patch.object(webhooks, "build_payload", side_effect=RuntimeError("bug")):
        assert webhooks.emit(job) == 0


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
        {"name": "Team", "kind": "slack", "url": SLACK_URL, "events": ["generation.failed", "generation.completed"]},
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()
    assert data["url_hint"] == "https://hooks.slack.com/…1234"
    assert data["has_url"] is True
    assert data["events"] == ["generation.completed", "generation.failed"]
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
    assert "generation.completed" in ids
    assert "experiment.completed" in ids
    assert all(t["description"] for t in types)


def test_api_requires_auth():
    assert Client().get("/api/integrations/webhooks/").status_code == 401
