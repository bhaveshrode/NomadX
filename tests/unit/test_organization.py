import pytest
from nomadx.src.domain.organization.organization import Organization, OrganizationStatus
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
from nomadx.src.domain.organization.events import OrganizationCreated

def test_organization_creation_success():
    org_id = OrganizationId()
    name = OrganizationName("NomadX Merchant")
    slug = OrganizationSlug("nomadx-merchant")
    owner_id = UserId()
    settings = OrganizationSettings("USD")

    result = Organization.create(
        id=org_id,
        name=name,
        slug=slug,
        owner_id=owner_id,
        settings=settings
    )

    assert result.is_success
    org = result.value
    assert org.id == org_id
    assert org.name == name
    assert org.slug == slug
    assert org.owner_id == owner_id
    assert org.settings.default_currency == "USD"
    assert org.status == OrganizationStatus.ACTIVE

    # Assert event was raised
    assert len(org.domain_events) == 1
    event = org.domain_events[0]
    assert isinstance(event, OrganizationCreated)
    assert event.organization_id == org_id
    assert event.owner_id == owner_id

def test_organization_name_validation():
    with pytest.raises(ValueError, match="Organization name must be between"):
        OrganizationName("a")

    with pytest.raises(ValueError, match="Organization name cannot be empty"):
        OrganizationName("")

def test_organization_slug_validation():
    with pytest.raises(ValueError, match="Organization slug can only contain"):
        OrganizationSlug("Invalid Slug Name")

    with pytest.raises(ValueError, match="Organization slug must be between"):
        OrganizationSlug("ab")

def test_organization_settings_validation():
    with pytest.raises(ValueError, match="Default currency must be a 3- to 6-letter"):
        OrganizationSettings("US")
    
    with pytest.raises(ValueError, match="Default currency must be a 3- to 6-letter"):
        OrganizationSettings("INVALIDTICKER")
