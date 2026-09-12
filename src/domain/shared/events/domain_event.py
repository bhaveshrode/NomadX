from datetime import datetime, timezone
from abc import ABC, abstractmethod

class DomainEvent(ABC):
    """
    Contract representing a Domain Event in NomadX.
    """
    def __init__(self, occurred_on: datetime = None):
        self._occurred_on = occurred_on or datetime.now(timezone.utc).replace(tzinfo=None)

    @property
    def occurred_on(self) -> datetime:
        """The date and time when the event occurred."""
        return self._occurred_on

    @property
    def event_name(self) -> str:
        """Helper to get the event class name."""
        return self.__class__.__name__

    @abstractmethod
    def get_aggregate_id(self) -> str:
        """Returns the associated aggregate's unique identifier."""
        pass
