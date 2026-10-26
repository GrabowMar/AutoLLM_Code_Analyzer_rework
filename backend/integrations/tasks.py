"""Celery tasks for outbound webhooks."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_SECONDS = (15, 60, 240)  # before retry 1, 2, 3


@shared_task(bind=True, acks_late=True, max_retries=MAX_RETRIES, ignore_result=True)
def deliver_webhook(self, delivery_id: int) -> str:
    """Send one :class:`WebhookDelivery`; retries transient failures (5xx, 429, network) with backoff."""
    from backend.integrations.models import WebhookDelivery
    from backend.integrations.services import webhooks

    delivery = WebhookDelivery.objects.select_related("subscription").filter(pk=delivery_id).first()
    if delivery is None:
        return "missing"
    if delivery.status != WebhookDelivery.Status.PENDING:
        return delivery.status
    if not delivery.subscription.enabled:
        delivery.status = WebhookDelivery.Status.FAILED
        delivery.error = "subscription disabled"
        delivery.save(update_fields=["status", "error", "updated_at"])
        return "disabled"
    final = self.request.retries >= self.max_retries
    result = webhooks.send_delivery(delivery, final=final)
    if not result.ok and result.retryable and not final:
        countdown = BACKOFF_SECONDS[min(self.request.retries, len(BACKOFF_SECONDS) - 1)]
        raise self.retry(countdown=countdown)
    return delivery.status


@shared_task(ignore_result=True)
def sweep_webhook_events() -> dict:
    """Periodic (Celery beat): emit events for terminal transitions that bypassed post_save."""
    from backend.integrations.services import webhooks

    return webhooks.sweep()
