import json
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.organization.organization import Organization, OrganizationStatus
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
from nomadx.src.infrastructure.database.repositories import PostgresOrganizationRepository
from nomadx.src.infrastructure.database.models import OutboxEventDbModel

@pytest.mark.asyncio
async def test_repository_save_and_retrieve(db_session: AsyncSession):
    repo = PostgresOrganizationRepository(db_session)
    
    org_id = OrganizationId()
    name = OrganizationName("NomadX Merchant Store")
    slug = OrganizationSlug("nomadx-store")
    owner_id = UserId()
    settings = OrganizationSettings("EUR")

    # Create and verify aggregate has domain events
    org = Organization.create(
        id=org_id,
        name=name,
        slug=slug,
        owner_id=owner_id,
        settings=settings
    ).value
    
    assert len(org.domain_events) == 1

    # Save to database
    await repo.save(org)
    await db_session.commit()

    # Aggregate events should be cleared
    assert len(org.domain_events) == 0

    # Retrieve and verify properties match
    retrieved = await repo.find_by_id(org_id)
    assert retrieved is not None
    assert retrieved.id == org_id
    assert retrieved.name == name
    assert retrieved.slug == slug
    assert retrieved.owner_id == owner_id
    assert retrieved.settings.default_currency == "EUR"
    assert retrieved.status == OrganizationStatus.ACTIVE

    # Lookup by slug
    by_slug = await repo.find_by_slug(slug)
    assert by_slug is not None
    assert by_slug.id == org_id

    # Verify Outbox table contains the event
    outbox_result = await db_session.execute(select(OutboxEventDbModel))
    outbox_records = outbox_result.scalars().all()
    assert len(outbox_records) == 1
    
    record = outbox_records[0]
    assert record.event_type == "OrganizationCreated"
    assert record.processed is False
    
    payload = json.loads(record.payload)
    assert payload["organization_id"] == str(org_id.value)
    assert payload["owner_id"] == str(owner_id.value)
