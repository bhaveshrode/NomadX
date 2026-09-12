from datetime import datetime
from ..shared.events.domain_event import DomainEvent
from .value_objects import UserId, Email

class UserRegistered(DomainEvent):
    """Event raised when a new User registers."""
    def __init__(self, user_id: UserId, email: Email, name: str, occurred_on: datetime = None):
        super().__init__(occurred_on)
        self.user_id = user_id
        self.email = email
        self.name = name

    def get_aggregate_id(self) -> str:
        return str(self.user_id.value)


class EmailVerified(DomainEvent):
    """Event raised when a User's email is successfully verified."""
    def __init__(self, user_id: UserId, occurred_on: datetime = None):
        super().__init__(occurred_on)
        self.user_id = user_id

    def get_aggregate_id(self) -> str:
        return str(self.user_id.value)


class UserAuthenticated(DomainEvent):
    """Event raised when a User login is successful."""
    def __init__(self, user_id: UserId, occurred_on: datetime = None):
        super().__init__(occurred_on)
        self.user_id = user_id

    def get_aggregate_id(self) -> str:
        return str(self.user_id.value)


class UserLockedOut(DomainEvent):
    """Event raised when a User account is locked due to consecutive login failures."""
    def __init__(self, user_id: UserId, locked_until: datetime, occurred_on: datetime = None):
        super().__init__(occurred_on)
        self.user_id = user_id
        self.locked_until = locked_until

    def get_aggregate_id(self) -> str:
        return str(self.user_id.value)
