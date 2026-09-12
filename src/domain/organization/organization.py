from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any

from ..shared.entities.entity import Entity
from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from .value_objects import OrganizationId, OrganizationName, OrganizationSlug, OrganizationSettings, UserId
from .events import OrganizationCreated, MembershipCreated

class OrganizationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class OrganizationRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class Membership(Entity[UserId]):
    """
    Domain Entity representing a User's membership context and role inside an Organization.
    """
    def __init__(self, user_id: UserId, role: OrganizationRole, joined_at: datetime, status: str = "ACTIVE"):
        super().__init__(user_id)
        self._role = role
        self._joined_at = joined_at
        self._status = status

    @property
    def user_id(self) -> UserId:
        """Retrieves the entity's user identity."""
        return self.id

    @property
    def role(self) -> OrganizationRole:
        """Exposes the user role."""
        return self._role

    @property
    def joined_at(self) -> datetime:
        """Timestamp of joining."""
        return self._joined_at

    @property
    def status(self) -> str:
        """Status of membership."""
        return self._status

    def update_role(self, role: OrganizationRole) -> None:
        """Modifies the user's role. Internal to the Organization aggregate boundary."""
        self._role = role


class Organization(AggregateRoot[OrganizationId]):
    """
    Aggregate Root guarding Organization settings, membership entities, and profile lifecycle invariants.
    """
    def __init__(self, id: OrganizationId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    # Getters
    @property
    def name(self) -> OrganizationName:
        return self._props["name"]

    @property
    def slug(self) -> OrganizationSlug:
        return self._props["slug"]

    @property
    def status(self) -> OrganizationStatus:
        return self._props["status"]

    @property
    def settings(self) -> OrganizationSettings:
        return self._props["settings"]

    @property
    def owner_id(self) -> UserId:
        return self._props["owner_id"]

    @property
    def memberships(self) -> Dict[str, Membership]:
        return self._props["memberships"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: OrganizationId,
        name: OrganizationName,
        slug: OrganizationSlug,
        owner_id: UserId,
        settings: OrganizationSettings
    ) -> Result["Organization"]:
        """
        Factory method to create a new Organization in ACTIVE status.
        Ensures valid properties, joins the owner, and registers OrganizationCreated.
        """
        if id is None or name is None or slug is None or owner_id is None or settings is None:
            return Result.fail(ResultError.validation("Mandatory fields cannot be null."))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Build memberships map and join the owner automatically
        memberships: Dict[str, Membership] = {}
        owner_membership = Membership(
            user_id=owner_id,
            role=OrganizationRole.OWNER,
            joined_at=now
        )
        memberships[owner_id.value] = owner_membership

        props = {
            "name": name,
            "slug": slug,
            "status": OrganizationStatus.ACTIVE,
            "settings": settings,
            "owner_id": owner_id,
            "memberships": memberships,
            "created_at": now,
            "updated_at": now
        }

        org = cls(id, props)
        org._add_domain_event(OrganizationCreated(id, owner_id))
        return Result.ok(org)

    def add_membership(self, user_id: UserId, role: OrganizationRole) -> Result[None]:
        """Registers a new member inside the organization."""
        if self.status != OrganizationStatus.ACTIVE:
            return Result.fail(ResultError.conflict("Cannot add members to an inactive organization."))
        if user_id.value in self.memberships:
            return Result.fail(ResultError.conflict("User is already a member of this organization."))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        membership = Membership(user_id, role, now)
        self.memberships[user_id.value] = membership
        self._props["updated_at"] = now
        
        self._add_domain_event(MembershipCreated(self.id, user_id, role.value))
        return Result.ok()

    def remove_membership(self, user_id: UserId) -> Result[None]:
        """Removes a user from membership. Guards against deleting the owner."""
        if self.status != OrganizationStatus.ACTIVE:
            return Result.fail(ResultError.conflict("Cannot modify membership in an inactive organization."))
        if user_id.value not in self.memberships:
            return Result.fail(ResultError.not_found("Member not found."))

        member = self.memberships[user_id.value]
        if member.role == OrganizationRole.OWNER:
            return Result.fail(
                ResultError.conflict("Cannot remove the owner. Transfer ownership to another member first.")
            )

        del self.memberships[user_id.value]
        self._props["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        return Result.ok()

    def transfer_ownership(self, new_owner_id: UserId, current_owner_id: UserId) -> Result[None]:
        """Transfers ownership from the current owner to another active member."""
        if self.status != OrganizationStatus.ACTIVE:
            return Result.fail(ResultError.conflict("Cannot transfer ownership in an inactive organization."))
        if self.owner_id.value != current_owner_id.value:
            return Result.fail(ResultError.unauthorized("Only the current owner can transfer ownership."))
        if new_owner_id.value not in self.memberships:
            return Result.fail(ResultError.conflict("Ownership can only be transferred to an active member."))

        prev_owner = self.memberships.get(current_owner_id.value)
        if prev_owner:
            prev_owner.update_role(OrganizationRole.ADMIN)

        new_owner = self.memberships[new_owner_id.value]
        new_owner.update_role(OrganizationRole.OWNER)
        self._props["owner_id"] = new_owner_id
        self._props["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        return Result.ok()

    def update_settings(self, settings: OrganizationSettings) -> Result[None]:
        """Updates organizational preferences and settings."""
        if self.status != OrganizationStatus.ACTIVE:
            return Result.fail(ResultError.conflict("Cannot update settings of an inactive organization."))
        self._props["settings"] = settings
        self._props["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        return Result.ok()

    def suspend(self) -> Result[None]:
        """Suspends the organization."""
        if self.status != OrganizationStatus.ACTIVE:
            return Result.fail(ResultError.conflict("Only active organizations can be suspended."))
        self._props["status"] = OrganizationStatus.SUSPENDED
        self._props["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        return Result.ok()
