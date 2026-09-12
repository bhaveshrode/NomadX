import pytest
from nomadx.src.infrastructure.notification.fake import (
    FakeEmailDeliveryAdapter,
    FakeWebhookDeliveryAdapter,
    NotificationDeliveryAdapter
)
from nomadx.src.infrastructure.notification.models import NotificationDeliveryRequest


@pytest.mark.asyncio
async def test_email_adapter_delivery_success():
    adapter = FakeEmailDeliveryAdapter()
    req = NotificationDeliveryRequest(
        notification_id="notif-1",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={"msg": "test"},
        idempotency_key="idem-key-1"
    )

    res = await adapter.deliver(req)
    assert res.is_success is True
    assert res.provider_reference.startswith("email-ref-")
    assert res.failure_code is None
    assert res.failure_message is None
    assert res.failure_retryable is False


@pytest.mark.asyncio
async def test_email_adapter_invalid_recipient():
    adapter = FakeEmailDeliveryAdapter()
    req = NotificationDeliveryRequest(
        notification_id="notif-2",
        organization_id="org-1",
        channel="EMAIL",
        recipient="invalid-email-address",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-2"
    )

    res = await adapter.deliver(req)
    assert res.is_success is False
    assert res.failure_code == "INVALID_RECIPIENT"
    assert res.failure_retryable is False


@pytest.mark.asyncio
async def test_email_adapter_temporary_and_permanent_failures():
    adapter = FakeEmailDeliveryAdapter()
    
    # 1. Config retryable failure
    adapter.set_behavior("idem-key-temp", "TEMPORARY_FAILURE")
    req_temp = NotificationDeliveryRequest(
        notification_id="notif-3",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-temp"
    )
    res_temp = await adapter.deliver(req_temp)
    assert res_temp.is_success is False
    assert res_temp.failure_code == "PROVIDER_RATE_LIMIT"
    assert res_temp.failure_retryable is True

    # 2. Config timeout failure (also retryable)
    adapter.set_behavior("idem-key-timeout", "TIMEOUT")
    req_timeout = NotificationDeliveryRequest(
        notification_id="notif-4",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-timeout"
    )
    res_timeout = await adapter.deliver(req_timeout)
    assert res_timeout.is_success is False
    assert res_timeout.failure_code == "EMAIL_PROVIDER_TIMEOUT"
    assert res_timeout.failure_retryable is True

    # 3. Config permanent failure
    adapter.set_behavior("idem-key-perm", "PERMANENT_FAILURE")
    req_perm = NotificationDeliveryRequest(
        notification_id="notif-5",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-perm"
    )
    res_perm = await adapter.deliver(req_perm)
    assert res_perm.is_success is False
    assert res_perm.failure_code == "PROVIDER_REJECTED"
    assert res_perm.failure_retryable is False


@pytest.mark.asyncio
async def test_email_adapter_idempotency_cache():
    adapter = FakeEmailDeliveryAdapter()
    req = NotificationDeliveryRequest(
        notification_id="notif-6",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-idem"
    )

    # First execution succeeds
    res1 = await adapter.deliver(req)
    assert res1.is_success is True

    # Override behavior to failure, but execution history should yield SUCCESS from cache
    adapter.set_behavior("idem-key-idem", "PERMANENT_FAILURE")
    res2 = await adapter.deliver(req)
    assert res2.is_success is True
    assert res2.provider_reference == res1.provider_reference


@pytest.mark.asyncio
async def test_webhook_adapter_delivery_success():
    adapter = FakeWebhookDeliveryAdapter()
    req = NotificationDeliveryRequest(
        notification_id="notif-7",
        organization_id="org-1",
        channel="WEBHOOK",
        recipient="https://webhook.site/callback",
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={"msg": "test"},
        idempotency_key="idem-key-webhook"
    )

    res = await adapter.deliver(req)
    assert res.is_success is True
    assert res.provider_reference.startswith("webhook-ref-")
    assert res.failure_code is None


@pytest.mark.asyncio
async def test_webhook_adapter_invalid_recipient():
    adapter = FakeWebhookDeliveryAdapter()
    req = NotificationDeliveryRequest(
        notification_id="notif-8",
        organization_id="org-1",
        channel="WEBHOOK",
        recipient="http://insecure.url", # not https
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-webhook-invalid"
    )

    res = await adapter.deliver(req)
    assert res.is_success is False
    assert res.failure_code == "INVALID_RECIPIENT"
    assert res.failure_retryable is False


@pytest.mark.asyncio
async def test_webhook_adapter_temporary_and_permanent_failures():
    adapter = FakeWebhookDeliveryAdapter()
    
    # 1. Config retryable failure
    adapter.set_behavior("idem-key-web-temp", "TEMPORARY_FAILURE")
    req_temp = NotificationDeliveryRequest(
        notification_id="notif-9",
        organization_id="org-1",
        channel="WEBHOOK",
        recipient="https://webhook.site",
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-web-temp"
    )
    res_temp = await adapter.deliver(req_temp)
    assert res_temp.is_success is False
    assert res_temp.failure_code == "WEBHOOK_TIMEOUT"
    assert res_temp.failure_retryable is True

    # 2. Config permanent failure
    adapter.set_behavior("idem-key-web-perm", "PERMANENT_FAILURE")
    req_perm = NotificationDeliveryRequest(
        notification_id="notif-10",
        organization_id="org-1",
        channel="WEBHOOK",
        recipient="https://webhook.site",
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={},
        idempotency_key="idem-key-web-perm"
    )
    res_perm = await adapter.deliver(req_perm)
    assert res_perm.is_success is False
    assert res_perm.failure_code == "PROVIDER_REJECTED"
    assert res_perm.failure_retryable is False


@pytest.mark.asyncio
async def test_composite_delivery_adapter():
    email = FakeEmailDeliveryAdapter()
    webhook = FakeWebhookDeliveryAdapter()
    composite = NotificationDeliveryAdapter(email, webhook)

    # 1. Route email
    req_email = NotificationDeliveryRequest(
        notification_id="notif-11",
        organization_id="org-1",
        channel="EMAIL",
        recipient="user@example.com",
        notification_type="INVOICE_ISSUED",
        subject="Hello",
        payload={},
        idempotency_key="idem-composite-email"
    )
    res_email = await composite.deliver(req_email)
    assert res_email.is_success is True
    assert len(email._delivery_history) == 1

    # 2. Route webhook
    req_webhook = NotificationDeliveryRequest(
        notification_id="notif-12",
        organization_id="org-1",
        channel="WEBHOOK",
        recipient="https://webhook.site",
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={},
        idempotency_key="idem-composite-webhook"
    )
    res_webhook = await composite.deliver(req_webhook)
    assert res_webhook.is_success is True
    assert len(webhook._delivery_history) == 1

    # 3. Unsupported channel
    req_unsupported = NotificationDeliveryRequest(
        notification_id="notif-13",
        organization_id="org-1",
        channel="SMS", # unsupported
        recipient="12345",
        notification_type="PAYMENT_CONFIRMED",
        subject="Hello",
        payload={},
        idempotency_key="idem-composite-sms"
    )
    res_unsupported = await composite.deliver(req_unsupported)
    assert res_unsupported.is_success is False
    assert res_unsupported.failure_code == "UNKNOWN_ERROR"
