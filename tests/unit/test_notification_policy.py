import pytest
from uuid import uuid4

from nomadx.src.domain.notification.policies import NotificationPolicy, DEFAULT_POLICY_MAPPING
from nomadx.src.domain.notification.value_objects import NotificationChannel
from nomadx.src.domain.notification.preferences import NotificationPreference, PreferenceId
from nomadx.src.domain.organization.value_objects import OrganizationId


class MockPreferenceRepository:
    def __init__(self):
        self.preferences = {}

    async def find_preference(self, organization_id, event_type, channel):
        key = (organization_id.value, event_type, channel)
        return self.preferences.get(key)

    async def save(self, preference):
        key = (preference.organization_id.value, preference.event_type, preference.channel)
        self.preferences[key] = preference


def test_event_policy_mappings():
    # Verify default mapping entries exist
    policy = NotificationPolicy(MockPreferenceRepository())
    
    m_invoice = policy.get_event_mapping("InvoiceIssued")
    assert m_invoice["template"] == "invoice.issued"
    assert m_invoice["priority"] == "NORMAL"
    assert m_invoice["recipient_type"] == "CUSTOMER"

    m_failed = policy.get_event_mapping("SettlementFailed")
    assert m_failed["template"] == "settlement.failed"
    assert m_failed["priority"] == "HIGH"
    assert m_failed["recipient_type"] == "MERCHANT"


@pytest.mark.asyncio
async def test_policy_fallback_to_defaults():
    repo = MockPreferenceRepository()
    policy = NotificationPolicy(repo)
    org_id = OrganizationId(str(uuid4()))

    # Default for InvoiceIssued should be EMAIL only
    channels = await policy.get_enabled_channels(org_id, "InvoiceIssued")
    assert NotificationChannel.EMAIL in channels
    assert NotificationChannel.WEBHOOK not in channels

    # Default for SettlementCompleted should be both EMAIL and WEBHOOK
    channels_settlement = await policy.get_enabled_channels(org_id, "SettlementCompleted")
    assert NotificationChannel.EMAIL in channels_settlement
    assert NotificationChannel.WEBHOOK in channels_settlement


@pytest.mark.asyncio
async def test_policy_override_with_merchant_preferences():
    repo = MockPreferenceRepository()
    policy = NotificationPolicy(repo)
    org_id = OrganizationId(str(uuid4()))

    # 1. Disable EMAIL for InvoiceIssued
    p1 = NotificationPreference.create(
        id=PreferenceId(str(uuid4())),
        organization_id=org_id,
        event_type="InvoiceIssued",
        channel=NotificationChannel.EMAIL,
        is_enabled=False
    ).value
    await repo.save(p1)

    channels1 = await policy.get_enabled_channels(org_id, "InvoiceIssued")
    assert NotificationChannel.EMAIL not in channels1

    # 2. Enable WEBHOOK for InvoiceIssued
    p2 = NotificationPreference.create(
        id=PreferenceId(str(uuid4())),
        organization_id=org_id,
        event_type="InvoiceIssued",
        channel=NotificationChannel.WEBHOOK,
        is_enabled=True
    ).value
    await repo.save(p2)

    channels2 = await policy.get_enabled_channels(org_id, "InvoiceIssued")
    assert NotificationChannel.WEBHOOK in channels2
