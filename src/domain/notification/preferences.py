from datetime import datetime
from typing import Dict, Any, Optional

from ..shared.entities.identifier import UniqueEntityID
from ..shared.entities.entity import Entity
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from .value_objects import NotificationChannel


class PreferenceId(UniqueEntityID):
    """Strongly typed identifier representing a notification preference's unique identity."""
    pass


class NotificationPreference(Entity):
    """
    NotificationPreference Aggregate/Entity.
    Represents organization preference overrides for delivery channels per event type.
    """
    def __init__(self, id: PreferenceId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def event_type(self) -> str:
        return self._props["event_type"]

    @property
    def channel(self) -> NotificationChannel:
        return self._props["channel"]

    @property
    def is_enabled(self) -> bool:
        return self._props["is_enabled"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: PreferenceId,
        organization_id: OrganizationId,
        event_type: str,
        channel: NotificationChannel,
        is_enabled: bool = True,
        occurred_on: Optional[datetime] = None
    ) -> Result["NotificationPreference"]:
        if None in (id, organization_id, event_type, channel):
            return Result.fail(ResultError.validation("All preference fields are mandatory."))

        now = Clock.now()
        props = {
            "organization_id": organization_id,
            "event_type": event_type.strip(),
            "channel": channel,
            "is_enabled": is_enabled,
            "created_at": occurred_on or now,
            "updated_at": occurred_on or now
        }
        return Result.ok(cls(id, props))

    def enable(self) -> Result[None]:
        self._props["is_enabled"] = True
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)

    def disable(self) -> Result[None]:
        self._props["is_enabled"] = False
        self._props["updated_at"] = Clock.now()
        return Result.ok(None)
