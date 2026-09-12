from datetime import datetime
from ..shared.events.domain_event import DomainEvent
from .value_objects import OrganizationId, UserId

class OrganizationCreated(DomainEvent):
    """Event raised when a new Organization is successfully created."""
    def __init__(
        self, 
        organization_id: OrganizationId, 
        owner_id: UserId, 
        occurred_on: datetime = None
    ):
        super().__init__(occurred_on)
        self.organization_id = organization_id
        self.owner_id = owner_id

    def get_aggregate_id(self) -> str:
        return str(self.organization_id.value)


class MembershipCreated(DomainEvent):
    """Event raised when a User joins an Organization."""
    def __init__(
        self, 
        organization_id: OrganizationId, 
        user_id: UserId, 
        role: str, 
        occurred_on: datetime = None
    ):
        super().__init__(occurred_on)
        self.organization_id = organization_id
        self.user_id = user_id
        self.role = role

    def get_aggregate_id(self) -> str:
        return str(self.organization_id.value)
