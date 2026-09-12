from dataclasses import dataclass

@dataclass(frozen=True)
class ReplayNotificationCommand:
    """Command requesting manual replay/redrive of a non-active notification."""
    notification_id: str
    organization_id: str
