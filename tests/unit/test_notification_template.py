import pytest
from datetime import datetime, timezone
from uuid import uuid4

from nomadx.src.domain.notification.template import (
    NotificationTemplate,
    TemplateId,
    TemplateReference,
    TemplateVersion,
    TemplateContent,
    TemplateStatus
)
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.shared.result.result import Result
from nomadx.src.application.notification.template_service import NotificationTemplateService
from nomadx.src.infrastructure.workers.notification_worker import BackoffPolicy


class MockNotificationTemplateRepository:
    def __init__(self):
        self.templates = {}

    async def get_by_id(self, id):
        return self.templates.get(id.value)

    async def get_active_version(self, organization_id, reference):
        active = None
        for t in self.templates.values():
            if (t.organization_id.value == organization_id.value and
                    t.reference.value == reference.value and
                    t.status == TemplateStatus.ACTIVE):
                if not active or t.version.value > active.version.value:
                    active = t
        return active

    async def find_by_reference_and_version(self, organization_id, reference, version):
        for t in self.templates.values():
            if (t.organization_id.value == organization_id.value and
                    t.reference.value == reference.value and
                    t.version.value == version.value):
                return t
        return None

    async def save(self, template):
        self.templates[template.id.value] = template

    async def delete(self, id):
        pass


def test_template_reference_validation():
    # Valid references
    assert TemplateReference("invoice.issued").value == "invoice.issued"
    assert TemplateReference("invoice-payment_received-1").value == "invoice-payment_received-1"

    # Invalid references raise ValueError
    with pytest.raises(ValueError):
        TemplateReference("invoice/issued")
    with pytest.raises(ValueError):
        TemplateReference("")


def test_template_version_validation():
    assert TemplateVersion("v1").value == "v1"
    assert TemplateVersion("v99").value == "v99"

    with pytest.raises(ValueError):
        TemplateVersion("version1")
    with pytest.raises(ValueError):
        TemplateVersion("v1.2")


@pytest.mark.asyncio
async def test_template_rendering_success():
    repo = MockNotificationTemplateRepository()
    org_id = OrganizationId(str(uuid4()))
    template_id = TemplateId(str(uuid4()))
    
    template = NotificationTemplate.create(
        id=template_id,
        organization_id=org_id,
        reference=TemplateReference("payment.received"),
        version=TemplateVersion("v1"),
        content=TemplateContent("Hello {{customer_name}}, payment of {{amount}} received."),
        status=TemplateStatus.ACTIVE
    ).value
    await repo.save(template)

    service = NotificationTemplateService(repo)
    res = await service.render(
        organization_id=org_id,
        reference=TemplateReference("payment.received"),
        variables={"customer_name": "Alice", "amount": "100.00"}
    )
    assert res.is_success is True
    assert res.value == "Hello Alice, payment of 100.00 received."


@pytest.mark.asyncio
async def test_template_rendering_missing_variables():
    repo = MockNotificationTemplateRepository()
    org_id = OrganizationId(str(uuid4()))
    template_id = TemplateId(str(uuid4()))
    
    template = NotificationTemplate.create(
        id=template_id,
        organization_id=org_id,
        reference=TemplateReference("payment.received"),
        version=TemplateVersion("v1"),
        content=TemplateContent("Hello {{customer_name}}, payment of {{amount}} received."),
        status=TemplateStatus.ACTIVE
    ).value
    await repo.save(template)

    service = NotificationTemplateService(repo)
    res = await service.render(
        organization_id=org_id,
        reference=TemplateReference("payment.received"),
        variables={"customer_name": "Alice"}  # missing amount
    )
    assert res.is_success is False
    assert "Missing required template variable" in res.error.message


@pytest.mark.asyncio
async def test_template_version_selection():
    repo = MockNotificationTemplateRepository()
    org_id = OrganizationId(str(uuid4()))
    
    t1 = NotificationTemplate.create(
        id=TemplateId(str(uuid4())),
        organization_id=org_id,
        reference=TemplateReference("invoice.issued"),
        version=TemplateVersion("v1"),
        content=TemplateContent("Content v1 {{name}}"),
        status=TemplateStatus.INACTIVE
    ).value
    t2 = NotificationTemplate.create(
        id=TemplateId(str(uuid4())),
        organization_id=org_id,
        reference=TemplateReference("invoice.issued"),
        version=TemplateVersion("v2"),
        content=TemplateContent("Content v2 {{name}}"),
        status=TemplateStatus.ACTIVE
    ).value
    await repo.save(t1)
    await repo.save(t2)

    service = NotificationTemplateService(repo)
    
    # 1. Resolves ACTIVE version (v2)
    res_active = await service.render(org_id, TemplateReference("invoice.issued"), {"name": "Bob"})
    assert res_active.value == "Content v2 Bob"

    # 2. Resolves specified version (v1)
    res_v1 = await service.render(
        org_id,
        TemplateReference("invoice.issued"),
        {"name": "Bob"},
        version=TemplateVersion("v1")
    )
    assert res_v1.value == "Content v1 Bob"


def test_retry_backoff_policy():
    policy = BackoffPolicy()
    start_time = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Attempt 1: 1 minute delay
    t1 = policy.next_retry_at(1, start_time)
    assert (t1 - start_time).total_seconds() == 60

    # Attempt 2: 5 minutes delay
    t2 = policy.next_retry_at(2, start_time)
    assert (t2 - start_time).total_seconds() == 300

    # Attempt 3: 15 minutes delay
    t3 = policy.next_retry_at(3, start_time)
    assert (t3 - start_time).total_seconds() == 900
