import pytest
from nomadx.src.domain.organization.value_objects import OrganizationId, OrganizationName, OrganizationSlug, OrganizationSettings, UserId
from nomadx.src.domain.organization.organization import Organization, OrganizationRole, OrganizationStatus

def test_organization_creation_auto_joins_owner():
    org_id = OrganizationId()
    owner_id = UserId()
    org_settings = OrganizationSettings("USD")
    
    org_result = Organization.create(
        id=org_id,
        name=OrganizationName("Acme Corp"),
        slug=OrganizationSlug("acme-corp"),
        owner_id=owner_id,
        settings=org_settings
    )
    
    assert org_result.is_success
    org = org_result.value
    assert org.owner_id == owner_id
    assert len(org.memberships) == 1
    
    owner_member = org.memberships.get(owner_id.value)
    assert owner_member is not None
    assert owner_member.user_id == owner_id
    assert owner_member.role == OrganizationRole.OWNER
    assert owner_member.status == "ACTIVE"


def test_organization_add_and_remove_members():
    org = Organization.create(
        id=OrganizationId(),
        name=OrganizationName("Acme Corp"),
        slug=OrganizationSlug("acme-corp"),
        owner_id=UserId(),
        settings=OrganizationSettings("USD")
    ).value

    member_id = UserId()
    
    # Add member
    add_res = org.add_membership(member_id, OrganizationRole.MEMBER)
    assert add_res.is_success
    assert len(org.memberships) == 2
    assert org.memberships[member_id.value].role == OrganizationRole.MEMBER

    # Add duplicate member -> Conflict error
    dup_res = org.add_membership(member_id, OrganizationRole.ADMIN)
    assert dup_res.is_failure
    assert "already a member" in dup_res.error.message

    # Remove member
    remove_res = org.remove_membership(member_id)
    assert remove_res.is_success
    assert len(org.memberships) == 1
    assert member_id.value not in org.memberships

    # Remove owner -> Conflict error
    remove_owner_res = org.remove_membership(org.owner_id)
    assert remove_owner_res.is_failure
    assert "Cannot remove the owner" in remove_owner_res.error.message


def test_organization_transfer_ownership():
    owner_id = UserId()
    new_owner_id = UserId()
    org = Organization.create(
        id=OrganizationId(),
        name=OrganizationName("Acme Corp"),
        slug=OrganizationSlug("acme-corp"),
        owner_id=owner_id,
        settings=OrganizationSettings("USD")
    ).value

    # Ownership transfer to non-member should fail
    fail_transfer = org.transfer_ownership(new_owner_id, owner_id)
    assert fail_transfer.is_failure
    assert "transferred to an active member" in fail_transfer.error.message

    # Join the new owner as member first
    org.add_membership(new_owner_id, OrganizationRole.ADMIN)

    # Transfer ownership
    success_transfer = org.transfer_ownership(new_owner_id, owner_id)
    assert success_transfer.is_success

    # Verify owner_id updated
    assert org.owner_id == new_owner_id

    # Verify former owner role downgraded to ADMIN
    assert org.memberships[owner_id.value].role == OrganizationRole.ADMIN

    # Verify new owner role upgraded to OWNER
    assert org.memberships[new_owner_id.value].role == OrganizationRole.OWNER
